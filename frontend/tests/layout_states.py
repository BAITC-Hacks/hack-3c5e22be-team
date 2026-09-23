"""Additional viewport, loading, menu, theme and offline checks."""
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / 'test-results'


def run():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='msedge')
        context = browser.new_context(reduced_motion='reduce')
        page = context.new_page()
        page.goto('http://127.0.0.1:5173')
        expect(page.locator('#send')).to_be_enabled()
        for width, height in [(320, 640), (390, 844), (768, 1024), (1024, 768), (1600, 1100)]:
            page.set_viewport_size({'width': width, 'height': height})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
            expect(page.locator('#send')).to_be_in_viewport()
            if width <= 650:
                page.locator('#menu-toggle').click()
                expect(page.locator('#sidebar')).to_be_visible()
                page.locator('#nav-help').click()
                expect(page.locator('#info-dialog')).to_be_visible()
                page.locator('#close-info').click()
        page.locator('#theme-dark').click()
        assert page.locator('html').get_attribute('data-theme') == 'dark'
        page.locator('#theme-light').click()
        page.locator('#examples').click()
        expect(page.locator('#info-content')).to_contain_text('DEMO-002')
        page.locator('#close-info').click()
        # Hold a real chat request so the loading state can be inspected.
        held = []
        page.route('**/api/chat', lambda route: held.append(route))
        page.locator('#message').fill('DEMO-002')
        page.locator('#send').click()
        expect(page.locator('#pending')).to_be_visible()
        expect(page.locator('#send')).to_be_disabled()
        page.screenshot(path=str(OUTPUT / 'desktop-loading.png'), full_page=True)
        held[0].continue_()
        expect(page.locator('#send')).to_be_enabled(timeout=30000)
        page.unroute('**/api/chat')
        # Changing sessions is destructive to cart, so it has its own confirmation.
        page.locator('#new-chat').click()
        expect(page.locator('#info-dialog')).to_contain_text('корзина текущей сессии будут удалены')
        page.get_by_role('button', name='Остаться', exact=True).click()
        expect(page.locator('.product')).to_be_visible()
        # A fresh page with backend unavailable stays actionable for retry.
        page.route('http://127.0.0.1:8000/**', lambda route: route.abort())
        page.reload()
        expect(page.locator('#error')).to_be_visible()
        expect(page.locator('#connection')).to_contain_text('недоступен')
        page.screenshot(path=str(OUTPUT / 'desktop-offline.png'), full_page=True)
        page.unroute('http://127.0.0.1:8000/**')
        page.locator('#retry').click()
        expect(page.locator('#send')).to_be_enabled(timeout=30000)
        expect(page.locator('#error')).to_be_hidden()
        browser.close()
    print('PASS: five viewport widths, menu/help/examples/theme, pending, session reset prompt, server offline/recovery')


if __name__ == '__main__':
    run()
