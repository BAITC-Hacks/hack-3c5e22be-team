from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app import public_access
from app.main import create_app
from app.public_access import GATE_COOKIE, install_public_access

CREDENTIALS = ("demo-reviewer", "demo-test-password")


def public_settings(**overrides):
    values = {
        "public_demo": True,
        "public_demo_username": SecretStr(CREDENTIALS[0]),
        "public_demo_password": SecretStr(CREDENTIALS[1]),
        "public_demo_requests_per_minute": 20,
        "public_demo_max_requests": 100,
        "public_demo_cookie_secure": True,
        "public_demo_cookie_ttl_seconds": 3600,
        "public_demo_max_body_bytes": 64,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def guarded_app(**overrides):
    app = FastAPI()
    app.state.operations = 0

    @app.get("/")
    def homepage():
        return {"page": "demo"}

    @app.get("/health")
    def health():
        return {"status": "ok", "ai_configured": True, "catalog_count": 321, "cart_enabled": True}

    @app.api_route("/api/chat/sessions", methods=["GET", "POST"])
    @app.api_route("/api/chat", methods=["GET", "POST"])
    @app.api_route("/cart", methods=["GET", "POST"])
    async def operation(request: Request):
        app.state.operations += 1
        return {
            "authorization": request.headers.get("authorization"),
            "body": (await request.body()).decode("utf-8"),
        }

    install_public_access(app, public_settings(**overrides))
    return app


def login(client):
    response = client.get("/", auth=CREDENTIALS)
    assert response.status_code == 200
    return response


def test_disabled_is_compatible_with_existing_local_settings():
    app = FastAPI()
    install_public_access(app, SimpleNamespace(public_demo=False))
    assert app.user_middleware == []
    install_public_access(app, SimpleNamespace())
    assert app.user_middleware == []


@pytest.mark.parametrize(
    "override",
    [
        {"public_demo_username": SecretStr("")},
        {"public_demo_password": SecretStr("  ")},
        {"public_demo_username": SecretStr("invalid:user")},
        {"public_demo_requests_per_minute": 0},
        {"public_demo_max_requests": 0},
        {"public_demo_cookie_ttl_seconds": 0},
        {"public_demo_max_body_bytes": 0},
        {"public_demo_cookie_secure": False},
    ],
)
def test_public_demo_rejects_unsafe_configuration(override):
    with pytest.raises(ValueError, match="PUBLIC_DEMO"):
        guarded_app(**override)


@pytest.mark.parametrize("path", ["/", "/api/chat/sessions", "/cart", "/docs", "/openapi.json"])
def test_gate_protects_entrypoints_before_application_execution(path):
    app = guarded_app()
    with TestClient(app, base_url="https://testserver") as client:
        response = client.get(path)
        assert response.status_code == 401
        assert response.headers["www-authenticate"].startswith("Basic ")
        assert response.json()["error"]["code"] == "DEMO_ACCESS_REQUIRED"
        assert app.state.operations == 0


@pytest.mark.parametrize(
    "authorization", ["Bearer unrelated", "Basic !!!", "Basic Zm9v", "Basic", "Digest foo"]
)
def test_malformed_authorization_does_not_bypass_gate(authorization):
    with TestClient(guarded_app(), base_url="https://testserver") as client:
        response = client.get("/", headers={"Authorization": authorization})
        assert response.status_code == 401


@pytest.mark.parametrize("credentials", [("wrong", CREDENTIALS[1]), (CREDENTIALS[0], "wrong")])
def test_both_username_and_password_are_required(credentials):
    with TestClient(guarded_app(), base_url="https://testserver") as client:
        assert client.get("/", auth=credentials).status_code == 401


def test_gate_cookie_is_secure_and_does_not_replace_bearer_contract():
    app = guarded_app()
    with TestClient(app, base_url="https://testserver") as client:
        response = login(client)
        cookie = response.headers["set-cookie"]
        assert cookie.startswith(GATE_COOKIE + "=")
        assert "HttpOnly" in cookie and "Secure" in cookie
        assert "SameSite=lax" in cookie and "Path=/" in cookie
        assert "Domain=" not in cookie
        assert CREDENTIALS[0] not in cookie and CREDENTIALS[1] not in cookie
        headers = {"Authorization": "Bearer existing-session-token"}
        response = client.post("/api/chat", headers=headers, content="hello")
        assert response.status_code == 200
        assert response.json() == {"authorization": headers["Authorization"], "body": "hello"}
        assert response.headers["cache-control"] == "no-store"
        # A second visitor cannot reuse only the application's Bearer token.
        with TestClient(app, base_url="https://testserver") as stranger:
            assert stranger.post("/api/chat", headers=headers).status_code == 401


def test_login_endpoint_redirects_only_to_homepage():
    with TestClient(guarded_app(), base_url="https://testserver") as client:
        response = client.get(
            "/demo-login?next=https://example.com", auth=CREDENTIALS, follow_redirects=False
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/"
        assert GATE_COOKIE in client.cookies


def test_invalid_expired_and_other_process_cookies_are_rejected(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(public_access.time, "time", lambda: now[0])
    with TestClient(guarded_app(), base_url="https://testserver") as client:
        login(client)
        cookie = client.cookies.get(GATE_COOKIE)
        client.cookies.clear()
        for invalid in ("invalid", cookie[:-1] + ("a" if cookie[-1] != "a" else "b")):
            assert (
                client.get("/", headers={"Cookie": f"{GATE_COOKIE}={invalid}"}).status_code == 401
            )
        now[0] += 3601
        assert client.get("/", headers={"Cookie": f"{GATE_COOKIE}={cookie}"}).status_code == 401
        now[0] = 1000.0
        with TestClient(guarded_app(), base_url="https://testserver") as restarted:
            assert (
                restarted.get("/", headers={"Cookie": f"{GATE_COOKIE}={cookie}"}).status_code == 401
            )


def test_public_health_is_minimal_and_does_not_consume_request_budget():
    with TestClient(
        guarded_app(public_demo_max_requests=1), base_url="https://testserver"
    ) as client:
        for _ in range(3):
            response = client.get("/health")
            assert response.status_code == 200
            assert response.json() == {"status": "ok"}
        login(client)
        assert client.get("/cart").status_code == 200
        assert client.get("/cart").json()["error"]["code"] == "DEMO_BUDGET_EXHAUSTED"


@pytest.mark.parametrize("auth_method", ["basic", "cookie"])
def test_authenticated_health_preserves_frontend_configuration(auth_method):
    with TestClient(guarded_app(), base_url="https://testserver") as client:
        if auth_method == "cookie":
            login(client)
            response = client.get("/health")
        else:
            response = client.get("/health", auth=CREDENTIALS)
        assert response.status_code == 200
        assert response.json() == {
            "status": "ok",
            "ai_configured": True,
            "catalog_count": 321,
            "cart_enabled": True,
        }
        assert response.headers["cache-control"] == "no-store"


def test_minute_limit_is_shared_across_visitors_and_includes_session_creation(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(public_access.time, "monotonic", lambda: now[0])
    app = guarded_app(public_demo_requests_per_minute=2)
    with TestClient(app, base_url="https://testserver") as first:
        with TestClient(app, base_url="https://testserver") as second:
            login(first)
            login(second)
            assert first.post("/api/chat/sessions").status_code == 200
            assert second.post("/api/chat/sessions").status_code == 200
            response = second.get("/cart")
            assert response.status_code == 429
            assert response.json()["error"]["code"] == "DEMO_RATE_LIMITED"
            assert response.headers["retry-after"] == "60"
            assert app.state.operations == 2
            # Creating more visitors or application sessions cannot reset the budget.
            with TestClient(app, base_url="https://testserver") as third:
                login(third)
                assert third.post("/api/chat/sessions").status_code == 429
            now[0] += 60
            assert second.post("/api/chat/sessions").status_code == 200


def test_lifetime_budget_does_not_reset_after_a_minute(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(public_access.time, "monotonic", lambda: now[0])
    with TestClient(
        guarded_app(public_demo_max_requests=1), base_url="https://testserver"
    ) as client:
        login(client)
        assert client.post("/api/chat/sessions").status_code == 200
        now[0] += 3600
        response = client.get("/cart")
        assert response.status_code == 429
        assert response.json()["error"]["code"] == "DEMO_BUDGET_EXHAUSTED"
        assert response.json()["error"]["retryable"] is False
        assert "retry-after" not in response.headers


def test_oversized_body_is_rejected_before_side_effects_with_or_without_declared_length():
    app = guarded_app(public_demo_max_body_bytes=8)
    with TestClient(app, base_url="https://testserver") as client:
        login(client)
        for content in ("123456789", iter([b"1234", b"56789"])):
            response = client.post("/api/chat", content=content)
            assert response.status_code == 413
            assert response.json()["error"]["code"] == "REQUEST_TOO_LARGE"
        # A false small declared size must not bypass the actual-byte bound.
        assert (
            client.post(
                "/api/chat", content="123456789", headers={"Content-Length": "1"}
            ).status_code
            == 413
        )
        assert app.state.operations == 0
        assert client.post("/api/chat", content="12345678").status_code == 200


def test_gate_preserves_real_bearer_cart_cookie_and_csrf_contracts(settings, raw_products):
    settings.demo_cart_enabled = True
    settings.cart_cookie_secure = True
    app = create_app(settings)
    install_public_access(app, public_settings(public_demo_max_body_bytes=2048))
    with TestClient(app, base_url="https://testserver") as client:
        for raw in raw_products:
            app.state.catalog.upsert(raw, datetime.now(UTC), "synthetic")
        assert (
            client.get("/demo-login", auth=CREDENTIALS, follow_redirects=False).status_code == 303
        )
        created = client.post("/api/chat/sessions")
        assert created.status_code == 201
        token = created.json()["session_token"]
        auth = {"Authorization": f"Bearer {token}"}
        proposal = client.post(
            "/api/cart/proposals", headers=auth, json={"product_id": 900002, "quantity": 2}
        )
        assert proposal.status_code == 200
        assert client.get("/cart/state").json()["items"] == []
        # Gate login never grants consent to change a cart or bypasses its CSRF requirement.
        forbidden = client.post(
            "/cart/confirm", json={"proposal_id": proposal.json()["proposal_id"]}
        )
        assert forbidden.status_code == 403
        assert forbidden.json()["error"]["code"] == "CSRF_INVALID"
        confirmed = client.post(
            "/api/cart/confirm",
            headers={**auth, "Idempotency-Key": "gate-integration-confirm"},
            json={"proposal_id": proposal.json()["proposal_id"]},
        )
        assert confirmed.status_code == 200
        assert client.get("/api/cart", headers=auth).json()["items"][0]["quantity"] == 2
        assert client.get("/cart/state").json()["items"][0]["quantity"] == 2
        assert client.get("/cart").status_code == 200
        with TestClient(app, base_url="https://testserver") as stranger:
            assert (
                stranger.get("/demo-login", auth=CREDENTIALS, follow_redirects=False).status_code
                == 303
            )
            assert stranger.get("/cart/state").status_code == 401


def test_invalid_body_length_is_rejected_before_side_effects():
    app = guarded_app()
    with TestClient(app, base_url="https://testserver") as client:
        login(client)
        for length in ("-1", "unknown"):
            response = client.post("/api/chat", content="x", headers={"Content-Length": length})
            assert response.status_code == 400
        assert app.state.operations == 0
