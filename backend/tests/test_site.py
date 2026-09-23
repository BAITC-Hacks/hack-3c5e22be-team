import time

from fastapi.testclient import TestClient

from app.main import create_app


def test_one_origin_routes_do_not_expose_repository_or_hide_api_errors(client):
    page = client.get("/")
    assert page.status_code == 200 and 'id="chat-form"' in page.text
    script = client.get("/api.js")
    assert script.status_code == 200
    assert "fetch(path," in script.text and ":8000" not in script.text
    for path in (
        "/.env",
        "/README.md",
        "/tests/browser_smoke.py",
        "/api/not-found",
        "/cart/not-found",
    ):
        response = client.get(path)
        assert response.status_code == 404
        assert 'id="chat-form"' not in response.text
    assert client.post("/api/chat/sessions").status_code == 201
    assert client.get("/cart/app.js").status_code == 200


def test_same_origin_cart_cookie_and_csrf_under_https(settings, raw_products):
    from datetime import UTC, datetime

    settings.demo_cart_enabled = True
    settings.cart_cookie_secure = True
    with TestClient(create_app(settings), base_url="https://demo.example") as client:
        client.app.state.catalog.upsert(raw_products[1], datetime.now(UTC), "synthetic")
        session = client.post("/api/chat/sessions", headers={"Origin": "https://demo.example"})
        assert session.status_code == 201 and "Secure" in session.headers["set-cookie"]
        auth = {"Authorization": "Bearer " + session.json()["session_token"]}
        proposal = client.post(
            "/api/cart/proposals", headers=auth, json={"product_id": 900002, "quantity": 1}
        ).json()
        result = client.post(
            "/api/cart/confirm",
            headers={**auth, "Idempotency-Key": "site-qa-01"},
            json={"proposal_id": proposal["proposal_id"]},
        )
        assert result.status_code == 200
        assert client.get(result.json()["cart_url"]).status_code == 200
        assert client.get("/cart/state").json()["items"][0]["quantity"] == 1


def test_second_tab_resumes_cart_until_explicit_reset(settings, raw_products):
    from datetime import UTC, datetime

    settings.demo_cart_enabled = True
    with TestClient(create_app(settings)) as client:
        client.app.state.catalog.upsert(raw_products[1], datetime.now(UTC), "synthetic")
        original = client.post("/api/chat/sessions").json()["session_token"]
        auth = {"Authorization": "Bearer " + original}
        proposal = client.post(
            "/api/cart/proposals", headers=auth, json={"product_id": 900002, "quantity": 2}
        ).json()
        confirmed = client.post(
            "/api/cart/confirm",
            headers={**auth, "Idempotency-Key": "two-tabs-test"},
            json={"proposal_id": proposal["proposal_id"]},
        )
        assert confirmed.status_code == 200
        expires_at = client.app.state.sessions.items[original].expires_at
        resumed = client.post("/api/chat/sessions")
        assert resumed.status_code == 201 and resumed.json()["session_token"] == original
        assert client.app.state.sessions.items[original].expires_at == expires_at
        assert client.get("/cart/state").json()["items"][0]["quantity"] == 2
        assert client.get("/api/cart").status_code == 401  # Bearer is still required.
        assert (
            client.post(
                "/api/chat/sessions", headers={"Origin": "https://evil.example"}
            ).status_code
            == 403
        )
        assert client.delete("/api/chat/sessions/current", headers=auth).status_code == 204
        assert client.get("/cart").status_code == 401
        new_token = client.post("/api/chat/sessions").json()["session_token"]
        assert new_token != original
        assert client.get("/cart/state").json()["items"] == []
        assert client.get("/api/cart", headers=auth).status_code == 401


def test_expired_browser_cookie_creates_new_session(settings):
    settings.demo_cart_enabled = True
    with TestClient(create_app(settings)) as client:
        old_token = client.post("/api/chat/sessions").json()["session_token"]
        client.app.state.sessions.items[old_token].expires_at = time.monotonic() - 1
        new_token = client.post("/api/chat/sessions").json()["session_token"]
        assert new_token != old_token
        assert client.get("/cart/state").json()["items"] == []
