"""Smoke test the deployed, same-origin site through its public URL."""
import os
from pathlib import Path
from playwright.sync_api import expect, sync_playwright

base = os.environ.get('SITE_URL', 'http://127.0.0.1:8080').rstrip('/')
with sync_playwright() as p:
    browser = p.chromium.launch(channel='msedge')
    context = browser.new_context(viewport={'width': 1600, 'height': 1100})
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.goto(base, timeout=60000)
    expect(page.locator('#send')).to_be_enabled(timeout=60000)
    page.locator('#message').fill('DEMO-002')
    page.locator('#send').click()
    card = page.locator('.product[data-product-id="900002"]').last
    expect(card).to_be_visible(timeout=60000)
    card.locator('.quantity-form input').fill('2')
    card.get_by_role('button', name='В корзину', exact=True).click()
    expect(page.locator('#proposal-dialog')).to_be_visible(timeout=30000)
    assert page.locator('.cart-item').count() == 0
    page.locator('#confirm-proposal').click()
    expect(page.locator('#proposal-dialog')).not_to_be_visible(timeout=30000)
    expect(page.locator('.cart-item input')).to_have_value('2')
    assert page.locator('#cart-link').get_attribute('href') == base + '/cart'
    with page.expect_popup() as popup:
        page.locator('#cart-link').click()
    expect(popup.value.locator('body')).to_contain_text('DEMO-002', timeout=30000)
    cookies = context.cookies(base + '/cart')
    assert any(c['httpOnly'] and c['name'] == 'ekt_demo_session' for c in cookies)
    if base.startswith('https:'):
        assert any(c['secure'] and c['name'] == 'ekt_demo_session' for c in cookies)
    page.locator('.cart-item input').fill('3')
    page.locator('.cart-item').get_by_role('button', name='Изменить', exact=True).click()
    expect(page.locator('#proposal-dialog')).to_be_visible()
    page.locator('#confirm-proposal').click()
    expect(page.locator('.cart-item input')).to_have_value('3', timeout=30000)
    output = Path(__file__).resolve().parents[2] / 'test-results'
    output.mkdir(exist_ok=True)
    page.screenshot(path=str(output / 'hosted-desktop.png'), full_page=True)
    page.locator('.cart-item').get_by_role('button', name='Удалить', exact=True).click()
    expect(page.locator('#proposal-dialog')).to_be_visible()
    page.locator('#confirm-proposal').click()
    expect(page.locator('.cart-item')).to_have_count(0, timeout=30000)
    page.set_viewport_size({'width': 390, 'height': 844})
    page.screenshot(path=str(output / 'hosted-mobile.png'), full_page=True)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert not errors, errors
    for path in ('/.env', '/start_hosted.py', '/backend/data/hosted-demo.sqlite3'):
        assert context.request.get(base + path).status == 404
    browser.close()
print('PASS: public HTTPS, session, search, proposal, confirm, cart, change, remove, mobile')
