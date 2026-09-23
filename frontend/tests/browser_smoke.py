"""UI integration tests against run_demo.py. No model keys or external catalog calls."""
import json
import os
from pathlib import Path
import sqlite3
from uuid import UUID

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / 'test-results'


def run():
    OUTPUT.mkdir(exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel=os.environ.get('BROWSER_CHANNEL', 'msedge'))
        context = browser.new_context(viewport={'width': 1600, 'height': 1100}, reduced_motion='reduce')
        page = context.new_page()
        errors, requests, confirms = [], [], []
        auth = {}
        page.on('pageerror', lambda error: errors.append(str(error)))

        def request_seen(request):
            if request.url.endswith('/api/chat'):
                requests.append(request.post_data_json)
            if request.url.endswith('/api/cart/confirm'):
                confirms.append({'body': request.post_data_json, 'key': request.headers.get('idempotency-key')})
            if request.headers.get('authorization'):
                auth['Authorization'] = request.headers['authorization']

        page.on('request', request_seen)
        page.goto('http://127.0.0.1:5173')
        expect(page.locator('#connection')).to_contain_text('Подключено')
        expect(page.locator('#send')).to_be_enabled()
        page.screenshot(path=str(OUTPUT / 'desktop-home.png'), full_page=True)

        def idle():
            expect(page.locator('#send')).to_be_enabled(timeout=30000)

        def send(text):
            page.locator('#message').fill(text)
            page.locator('#send').click()
            idle()

        def cart():
            result = context.request.get('http://127.0.0.1:8000/api/cart', headers=auth)
            assert result.ok
            return result.json()

        def propose(quantity=2):
            card = page.locator('.product[data-product-id="900002"]').last
            card.locator('.quantity-form input').fill(str(quantity))
            card.get_by_role('button', name='В корзину', exact=True).click()
            expect(page.locator('#proposal-dialog')).to_be_visible()
            idle()

        def confirm():
            page.locator('#confirm-proposal').click()
            idle()

        send('DEMO-002')
        expect(page.locator('.message.assistant').last).to_contain_text('Ответ из каталога')
        expect(page.locator('.product').last).to_contain_text('ТЕСТОВЫЕ ДАННЫЕ')
        send('А какие характеристики?')
        expect(page.locator('.message.assistant').last).to_contain_text('DEMO-002')
        assert not cart()['items']
        propose(2)
        assert not cart()['items'], 'Proposal changed cart before confirmation'
        assert not confirms
        expect(page.locator('#proposal-content')).to_contain_text('2400')
        page.screenshot(path=str(OUTPUT / 'desktop-confirmation.png'), full_page=True)
        confirm()
        expect(page.locator('#proposal-dialog')).not_to_be_visible()
        assert cart()['items'][0]['quantity'] == 2
        assert len(confirms) == 1
        page.screenshot(path=str(OUTPUT / 'desktop-cart.png'), full_page=True)
        assert page.locator('#cart-link').get_attribute('href') == 'http://127.0.0.1:8000/cart'
        # Real cookie-protected cart page, not a mocked view.
        with page.expect_popup() as popup:
            page.locator('#cart-link').click()
        cart_page = popup.value
        expect(cart_page.locator('body')).to_contain_text('DEMO-002')
        cart_page.close()
        cookies = context.cookies('http://127.0.0.1:8000/cart')
        assert any(c['name'] == 'ekt_demo_session' and c['httpOnly'] for c in cookies)

        # Set, cancel, remove and clear all require a separate confirmation.
        page.locator('.cart-item input').fill('3')
        page.locator('.cart-item').get_by_role('button', name='Изменить', exact=True).click()
        idle()
        assert cart()['items'][0]['quantity'] == 2
        confirm()
        assert cart()['items'][0]['quantity'] == 3
        page.locator('.cart-item').get_by_role('button', name='Удалить', exact=True).click()
        idle()
        page.locator('#cancel-proposal').click()
        idle()
        assert cart()['items'][0]['quantity'] == 3
        page.locator('.cart-item').get_by_role('button', name='Удалить', exact=True).click()
        idle()
        confirm()
        assert not cart()['items']

        # Chat proposal, including text 'yes' never invoking confirm on its own.
        send('DEMO-002')
        send('Добавь 2')
        expect(page.locator('#proposal-dialog')).to_be_visible()
        assert not cart()['items']
        confirm()
        assert cart()['items'][0]['quantity'] == 2
        page.locator('#clear-cart').click()
        idle()
        assert cart()['items']
        confirm()
        assert not cart()['items']

        # Lose the response after an actual successful confirmation; repeat key.
        propose(2)
        attempts = []

        def lost_confirm(route):
            attempts.append(route.request.headers['idempotency-key'])
            if len(attempts) == 1:
                result = route.fetch()
                assert result.ok
                route.abort()
            else:
                route.continue_()

        page.route('**/api/cart/confirm', lost_confirm)
        confirm()
        expect(page.locator('#dialog-error')).to_be_visible()
        assert cart()['items'][0]['quantity'] == 2
        page.locator('#dialog-retry').click()
        idle()
        expect(page.locator('#proposal-dialog')).not_to_be_visible()
        assert attempts[0] == attempts[1]
        UUID(attempts[0])
        assert cart()['items'][0]['quantity'] == 2
        page.unroute('**/api/cart/confirm')

        # Actual synthetic database change between proposal and confirmation.
        # Only touch the isolated demo database, never the real catalog.
        db_path = ROOT / 'backend/data/demo.sqlite3'
        with sqlite3.connect(db_path) as db:
            original, source = db.execute('SELECT raw, source FROM products WHERE id=900002').fetchone()
        assert source == 'synthetic'
        try:
            propose(1)
            raw = json.loads(original)
            raw['price'] = 1300
            with sqlite3.connect(db_path) as db:
                db.execute('UPDATE products SET raw=? WHERE id=900002', (json.dumps(raw),))
            before = len(confirms)
            confirm()
            expect(page.locator('#proposal-dialog')).to_be_visible()
            expect(page.locator('#proposal-content')).to_contain_text('Прежнее предложение')
            expect(page.locator('#proposal-content')).to_contain_text('1300')
            assert len(confirms) == before + 1
            assert cart()['items'][0]['quantity'] == 2
            assert cart()['items'][0]['unit_price'] == '1200.00'
            # Even increased stock requires another explicit confirmation.
            raw['quantity'] = 9
            with sqlite3.connect(db_path) as db:
                db.execute('UPDATE products SET raw=? WHERE id=900002', (json.dumps(raw),))
            confirm()
            expect(page.locator('#proposal-dialog')).to_be_visible()
            expect(page.locator('#proposal-content')).to_contain_text('Остаток изменился')
            assert cart()['items'][0]['quantity'] == 2
            confirm()
            assert cart()['items'][0]['quantity'] == 3
            assert cart()['items'][0]['unit_price'] == '1300.00'
        finally:
            with sqlite3.connect(db_path) as db:
                db.execute('UPDATE products SET raw=? WHERE id=900002', (original,))

        send('DEMO-001')
        expect(page.locator('.message.assistant').last).to_contain_text('КАНДИДАТ В АНАЛОГИ')
        send('DEMO-003')
        expect(page.locator('.product').last).to_contain_text('противоречит')
        send('NONEXISTENT-12345')
        expect(page.locator('.message.assistant').last).to_contain_text('не найден')

        # Chat retry preserves request UUID. Error response controls retry button.
        page.route('**/api/chat', lambda route: route.abort())
        send('DEMO-002')
        expect(page.locator('#error')).to_be_visible()
        request_id = requests[-1]['request_id']
        page.unroute('**/api/chat')
        page.locator('#retry').click()
        idle()
        assert requests[-1]['request_id'] == request_id
        page.route('**/api/chat', lambda route: route.fulfill(status=409, json={'error': {'code': 'REQUEST_ID_REUSED', 'message': 'ID reused', 'retryable': False}}))
        send('DEMO-002')
        expect(page.locator('#retry')).to_be_hidden()
        page.unroute('**/api/chat')

        # Mobile, keyboard-sized viewport, readable confirmation and cart drawer.
        page.set_viewport_size({'width': 390, 'height': 844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        send('DEMO-002')
        page.screenshot(path=str(OUTPUT / 'mobile-chat.png'), full_page=True)
        propose(1)
        page.screenshot(path=str(OUTPUT / 'mobile-confirmation.png'), full_page=True)
        page.set_viewport_size({'width': 390, 'height': 430})
        expect(page.locator('#confirm-proposal')).to_be_in_viewport()
        page.screenshot(path=str(OUTPUT / 'mobile-keyboard.png'), full_page=True)
        page.locator('#cancel-proposal').click()
        idle()
        page.set_viewport_size({'width': 390, 'height': 844})
        page.locator('#open-cart').click()
        idle()
        expect(page.locator('#cart-panel')).to_be_visible()
        page.screenshot(path=str(OUTPUT / 'mobile-cart.png'), full_page=True)
        page.locator('#close-cart').click()
        # Isolated browser does not gain access by opening the cart URL.
        stranger = browser.new_context()
        denied = stranger.request.get('http://127.0.0.1:8000/cart')
        assert denied.status == 401
        stranger.close()
        assert page.evaluate('localStorage.length + sessionStorage.length') == 0
        page.route('**/api/chat', lambda route: route.fulfill(status=401, json={'error': {'code': 'SESSION_EXPIRED', 'message': 'Session ended', 'retryable': False}}))
        send('Цена?')
        expect(page.locator('#recover-session')).to_be_visible()
        page.unroute('**/api/chat')
        page.locator('#recover-session').click()
        idle()
        expect(page.locator('#welcome')).to_be_visible()
        assert not cart()['items']
        page.screenshot(path=str(OUTPUT / 'mobile-home.png'), full_page=True)
        # Untrusted text is never interpreted as HTML.
        page.route('**/api/chat', lambda route: route.fulfill(status=200, json={'message_id': 'safe-test', 'message': '<img src=x onerror=alert(1)>', 'mode': 'rules', 'products': [], 'alternatives': [], 'warnings': [], 'cart_action': 'none'}))
        send('test')
        assert page.locator('#messages img').count() == 0
        assert not errors, errors
        browser.close()
    print('PASS: reference layouts, chat/context, all cart operations, explicit confirmation, cookie cart URL, idempotent retry, actual price/stock changes, errors, expiry, mobile/keyboard, session isolation, safe text')


if __name__ == '__main__':
    run()
