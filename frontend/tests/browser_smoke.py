"""Run with both servers running and Playwright installed. No external APIs needed."""
from pathlib import Path
import os
from uuid import UUID

from playwright.sync_api import sync_playwright, expect


def run():
    output = Path("test-results")
    output.mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=os.environ.get("BROWSER_CHANNEL") or None)
        page = browser.new_page(viewport={"width": 1440, "height": 1050})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto("http://127.0.0.1:5173")
        expect(page.locator("#connection")).to_contain_text("Сервер подключён")
        page.screenshot(path=str(output / "desktop.png"), full_page=True)

        def send(text):
            page.locator("#message").fill(text)
            page.locator("#send").click()
            expect(page.locator("#pending")).to_be_hidden(timeout=30000)

        send("DEMO-002")
        expect(page.locator(".product").last).to_contain_text("DEMO-002")
        expect(page.locator(".message.assistant").last).to_contain_text("Ответ из каталога · без обращения к ИИ")
        expect(page.locator(".product-price").last).to_contain_text("валюта не указана")
        send("А какие характеристики?")
        expect(page.locator(".message.assistant").last).to_contain_text("DEMO-002")
        send("DEMO-001")
        expect(page.locator(".message.assistant").last).to_contain_text("КАНДИДАТ В АНАЛОГИ")
        expect(page.locator(".message.assistant").last).to_contain_text("Требуется проверка совместимости")
        send("DEMO-003")
        expect(page.locator(".product").last.locator(".notice")).to_contain_text("противоречит")
        page.locator(".product").last.get_by_text("Сравнить кандидатов").click()
        expect(page.locator("#pending")).to_be_hidden(timeout=30000)
        expect(page.locator(".message.assistant").last).to_contain_text("не найдены")
        send("Добавь в корзину")
        expect(page.locator(".message.assistant").last).to_contain_text("Ничего не добавлено")
        send("Какие условия доставки?")
        expect(page.locator(".message.assistant").last).to_contain_text("условия покупки ещё не подключены")
        # Credentials must not leak into persistent browser storage.
        assert page.evaluate("localStorage.length + sessionStorage.length") == 0
        # Network failure with recovery through the actual retry action.
        page.route("**/api/chat", lambda route: route.abort())
        send("DEMO-002")
        expect(page.locator("#error")).to_be_visible()
        page.unroute("**/api/chat")
        page.locator("#retry").click()
        expect(page.locator("#pending")).to_be_hidden(timeout=30000)
        expect(page.locator("#error")).to_be_hidden()
        # Simulate a lost response AFTER the backend has processed it. The retry
        # must keep both UUID and message, and receive the cached message_id.
        attempts = []
        saved_reply = {}

        def lose_processed_response(route):
            attempts.append(route.request.post_data_json)
            if len(attempts) == 1:
                result = route.fetch()
                assert result.status == 200
                saved_reply.update(result.json())
                route.abort()
            else:
                route.continue_()

        page.route("**/api/chat", lose_processed_response)
        before = page.locator(".message.assistant").count()
        send("DEMO-001")
        expect(page.locator("#error")).to_be_visible()
        page.locator("#retry").click()
        expect(page.locator("#pending")).to_be_hidden(timeout=30000)
        expect(page.locator("#error")).to_be_hidden()
        assert attempts[0] == attempts[1]
        UUID(attempts[0]["request_id"])
        assert page.locator(".message.assistant").count() == before + 1
        assert page.locator(".message.assistant").last.get_attribute("data-message-id") == saved_reply["message_id"]
        send("DEMO-001")
        assert attempts[2]["request_id"] != attempts[0]["request_id"]
        page.unroute("**/api/chat")
        # Same status, different semantics: ID conflict cannot be retried.
        page.route("**/api/chat", lambda route: route.fulfill(
            status=409, json={"error": {"code": "REQUEST_ID_REUSED", "message": "ID reused", "retryable": False}},
        ))
        send("DEMO-002")
        expect(page.locator("#error")).to_contain_text("Отправьте сообщение заново")
        expect(page.locator("#retry")).to_be_hidden()
        page.unroute("**/api/chat")
        # Honor retryable=false even for a 503; status alone is not sufficient.
        page.route("**/api/chat", lambda route: route.fulfill(
            status=503, json={"error": {"code": "CATALOG_DISABLED", "message": "Проверка каталога отключена", "retryable": False}},
        ))
        send("DEMO-002")
        expect(page.locator("#error")).to_contain_text("Проверка каталога отключена")
        expect(page.locator("#retry")).to_be_hidden()
        page.unroute("**/api/chat")
        # Rate limiting produces a manual retry, never an automatic request loop.
        rate_requests = []

        def rate_limited(route):
            rate_requests.append(route.request.post_data_json)
            route.fulfill(status=429, json={"error": {
                "code": "RATE_LIMITED", "message": "Повторите через минуту.", "retryable": True,
            }})

        page.route("**/api/chat", rate_limited)
        send("DEMO-002")
        expect(page.locator("#retry")).to_have_text("Повторить позже")
        assert len(rate_requests) == 1
        page.unroute("**/api/chat")
        page.locator("#retry").click()
        expect(page.locator("#pending")).to_be_hidden(timeout=30000)
        expect(page.locator("#error")).to_be_hidden()
        # Expired session must not silently replay a context-dependent question.
        page.route("**/api/chat", lambda route: route.fulfill(status=401, body='{}'))
        send("А какие характеристики?")
        expect(page.locator("#error")).to_contain_text("Сессия завершена")
        expect(page.locator("#retry")).to_be_hidden()
        page.unroute("**/api/chat")
        page.locator("#new-chat").click()
        expect(page.locator("#welcome")).to_be_visible()
        send("DEMO-002")
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(output / "mobile.png"), full_page=True)
        page.locator("#new-chat-mobile").click()
        expect(page.locator("#welcome")).to_be_visible()
        # Render untrusted catalog text as text, not HTML.
        page.route("**/api/chat", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body='{"message":"<img src=x onerror=alert(1)>","mode":"rules","products":[],"warnings":[],"alternatives":[],"cart_action":"none"}',
        ))
        send("test")
        assert page.locator("#messages img").count() == 0
        expect(page.locator(".message.assistant").last).to_contain_text("<img")
        assert not errors, errors
        browser.close()
    print("PASS: chat context, catalog mode, alternatives, conflicts, cart boundary, terms, network retry, cached UUID retry, structured errors, rate limit, 401, mobile, text safety")


if __name__ == "__main__":
    run()
