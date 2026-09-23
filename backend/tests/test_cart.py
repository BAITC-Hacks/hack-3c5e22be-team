import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def cart_client(settings, raw_products):
    settings.demo_cart_enabled = True
    with TestClient(create_app(settings)) as client:
        for raw in raw_products:
            client.app.state.catalog.upsert(raw, datetime.now(UTC), "synthetic")
        yield client


def login(client):
    response = client.post("/api/chat/sessions")
    assert response.status_code == 201
    return {"Authorization": "Bearer " + response.json()["session_token"]}


def propose(client, auth, quantity=2, product_id=900002):
    return client.post(
        "/api/cart/proposals", headers=auth, json={"product_id": product_id, "quantity": quantity}
    )


def confirm(client, auth, proposal, key="test-key-0001"):
    return client.post(
        "/api/cart/confirm",
        headers={**auth, "Idempotency-Key": key},
        json={"proposal_id": proposal["proposal_id"]},
    )


def test_confirmation_and_link_always_reads_current_cart(cart_client):
    c = cart_client
    auth = login(c)
    proposal = propose(c, auth).json()
    assert c.get("/api/cart", headers=auth).json()["items"] == []
    assert "Корзина пуста" in c.get("/cart").text
    response = confirm(c, auth, proposal)
    assert response.status_code == 200
    assert response.json()["cart"]["items"][0]["quantity"] == 2
    url = response.json()["cart_url"]
    assert url == "/cart"
    page = c.get(url)
    assert page.status_code == 200
    assert "2400.00 KZT" in page.text
    assert page.headers["cache-control"] == "no-store"
    assert "DEMO-002" in page.text
    another = propose(c, auth, 1).json()
    confirm(c, auth, another, "test-key-0002")
    assert "3600.00 KZT" in c.get(url).text
    replay = confirm(c, auth, proposal).json()
    assert replay["replayed"] is True
    assert replay["cart"]["items"][0]["quantity"] == 3
    assert confirm(c, auth, proposal, "different-key").json()["error"]["code"] == "PROPOSAL_USED"


def test_sessions_are_isolated_and_link_is_not_a_capability(cart_client):
    c = cart_client
    first = login(c)
    proposal = propose(c, first).json()
    first_cookie = c.cookies.get("ekt_demo_session")
    c.cookies.clear()  # A different browser, not another tab sharing the same cookie.
    second = login(c)
    second_cookie = c.cookies.get("ekt_demo_session")
    assert confirm(c, second, proposal).status_code == 404
    assert c.get("/api/cart", headers=second).json()["items"] == []
    assert confirm(c, first, proposal).status_code == 200
    assert "2400.00 KZT" in c.get("/cart").text
    c.cookies.clear()
    c.cookies.set("ekt_demo_session", second_cookie, path="/cart")
    assert "Корзина пуста" in c.get("/cart").text
    assert c.get("/api/cart").status_code == 401  # Cookie alone cannot authorize API.
    assert propose(c, {}, 1).status_code == 401
    c.cookies.clear()
    assert c.get("/cart").status_code == 401
    c.cookies.set("ekt_demo_session", first_cookie, path="/cart")
    assert "2400.00 KZT" in c.get("/cart").text
    assert c.delete("/api/chat/sessions/current", headers=first).status_code == 204
    assert c.get("/api/cart", headers=first).status_code == 401
    c.cookies.set("ekt_demo_session", first_cookie, path="/cart")
    assert c.get("/cart").status_code == 401


@pytest.mark.parametrize("quantity", [0, -1, 1.5, True, "2", 10001])
def test_quantity_validation(cart_client, quantity):
    assert propose(cart_client, login(cart_client), quantity).status_code == 422


def test_stock_includes_existing_cart_and_replay_is_atomic(cart_client):
    c = cart_client
    auth = login(c)
    proposal = propose(c, auth, 5).json()
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: confirm(c, auth, proposal), range(2)))
    assert all(r.status_code == 200 for r in responses)
    assert sorted(r.json()["replayed"] for r in responses) == [False, True]
    assert propose(c, auth, 4).status_code == 409
    next_proposal = propose(c, auth, 3).json()
    assert confirm(c, auth, next_proposal).json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert (
        confirm(c, auth, next_proposal, "second-0001").json()["cart"]["items"][0]["quantity"] == 8
    )
    assert propose(c, auth, 1).status_code == 409


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("price", 1201, "PRICE_CHANGED"),
        ("quantity", 1, "STOCK_CHANGED"),
        ("quantity", 9, "STOCK_CHANGED"),
        ("name", "Changed", "DATA_CONFLICT"),
    ],
)
def test_recheck_invalidates_proposal(cart_client, raw_products, field, value, code):
    c = cart_client
    auth = login(c)
    proposal = propose(c, auth).json()
    raw = dict(raw_products[1], **{field: value})
    c.app.state.catalog.upsert(raw, datetime.now(UTC), "synthetic")
    assert confirm(c, auth, proposal).json()["error"]["code"] == code
    assert c.get("/api/cart", headers=auth).json()["items"] == []
    assert confirm(c, auth, proposal).status_code == 404


def test_expiration_and_replacement(cart_client):
    c = cart_client
    auth = login(c)
    old = propose(c, auth).json()
    current = propose(c, auth, 1).json()
    assert confirm(c, auth, old).status_code == 404
    session = c.app.state.sessions.get(auth["Authorization"][7:])
    session.cart.pending["deadline"] = time.monotonic() - 1
    assert confirm(c, auth, current).json()["error"]["code"] == "PROPOSAL_EXPIRED"
    session.expires_at = time.monotonic() - 1
    assert c.get("/cart").status_code == 401
    assert confirm(c, auth, current).status_code == 401


def test_live_unknown_stale_conflicting_data_rejected(cart_client, raw_products):
    c = cart_client
    auth = login(c)
    assert propose(c, auth, 1, 900001).json()["error"]["code"] == "STOCK_CHANGED"
    assert propose(c, auth, 1, 900003).json()["error"]["code"] == "DATA_CONFLICT"
    assert propose(c, auth, 1, 1234567).status_code == 404
    raw = raw_products[1]
    c.app.state.catalog.upsert(raw, datetime.now(UTC), "ekt_api")
    assert propose(c, auth).json()["error"]["code"] == "CATALOG_DISABLED"
    c.app.state.catalog.upsert(raw, datetime.now(UTC), "synthetic")
    c.app.state.catalog.stale_seconds = -1
    assert propose(c, auth).json()["error"]["code"] == "CATALOG_UNAVAILABLE"
    c.app.state.catalog.stale_seconds = 300
    c.app.state.catalog.upsert(dict(raw, quantity=None), datetime.now(UTC), "synthetic")
    assert propose(c, auth).status_code == 503


def test_csrf_cookie_flags_and_key_requirement(cart_client):
    c = cart_client
    assert (
        c.post("/api/chat/sessions", headers={"Origin": "https://evil.example"}).status_code == 403
    )
    response = c.post("/api/chat/sessions")
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/;" in cookie
    auth = {"Authorization": "Bearer " + response.json()["session_token"]}
    proposal = propose(c, auth).json()
    assert (
        c.post("/api/cart/confirm", json={"proposal_id": proposal["proposal_id"]}).status_code
        == 401
    )
    assert (
        c.post(
            "/api/cart/confirm", headers=auth, json={"proposal_id": proposal["proposal_id"]}
        ).status_code
        == 422
    )
    preflight = c.options(
        "/api/cart/confirm",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,idempotency-key,content-type",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-credentials"] == "true"


def test_cart_disabled_by_default(client, auth):
    assert client.get("/api/cart", headers=auth).status_code == 503


def test_xss_is_escaped(cart_client, raw_products):
    c = cart_client
    auth = login(c)
    raw = dict(raw_products[1], name="<script>alert(1)</script>")
    c.app.state.catalog.upsert(raw, datetime.now(UTC), "synthetic")
    confirm(c, auth, propose(c, auth).json())
    page = c.get("/cart")
    assert "<script>" not in page.text and "&lt;script&gt;" in page.text


def test_stale_at_confirmation_and_no_client_price(cart_client, raw_products):
    c = cart_client
    auth = login(c)
    response = c.post(
        "/api/cart/proposals", headers=auth, json={"product_id": 900002, "quantity": 2, "price": 1}
    )
    assert response.status_code == 422
    proposal = propose(c, auth).json()
    c.app.state.catalog.stale_seconds = -1
    assert confirm(c, auth, proposal).json()["error"]["code"] == "CATALOG_UNAVAILABLE"
    assert c.get("/api/cart", headers=auth).json()["items"] == []


def test_secure_cookie_and_restart_invalidates_session(settings):
    settings.demo_cart_enabled = True
    settings.cart_cookie_secure = True
    with TestClient(create_app(settings), base_url="https://testserver") as c:
        response = c.post("/api/chat/sessions")
        assert "Secure" in response.headers["set-cookie"]
        token = response.json()["session_token"]
        assert c.get("/cart").status_code == 200
    with TestClient(create_app(settings), base_url="https://testserver") as c:
        c.cookies.set("ekt_demo_session", token, path="/cart")
        assert c.get("/cart").status_code == 401
        assert c.get("/api/cart", headers={"Authorization": "Bearer " + token}).status_code == 401


def test_incompatible_quantity_rules_rejected(cart_client, raw_products):
    c = cart_client
    raw = dict(raw_products[1])
    raw["properties"] = {**raw["properties"], "KRATNOST_MIN": "10"}
    c.app.state.catalog.upsert(raw, datetime.now(UTC), "synthetic")
    assert propose(c, login(c)).json()["error"]["code"] == "DATA_CONFLICT"
