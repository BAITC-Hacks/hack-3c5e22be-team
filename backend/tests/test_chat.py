import time

from app.terms import PurchaseTerms


def test_context_is_kept_within_session(client, auth):
    initial = client.post("/api/chat", json={"message": "DEMO-002"}, headers=auth).json()
    assert initial["products"][0]["id"] == 900002
    followup = client.post(
        "/api/chat", json={"message": "А какие характеристики?"}, headers=auth
    ).json()
    assert followup["products"][0]["id"] == 900002
    assert "16А" in followup["message"]
    assert followup["mode"] == "rules"
    assert followup["warnings"]


def test_sessions_are_isolated(client, auth):
    client.post("/api/chat", json={"message": "DEMO-002"}, headers=auth)
    second = client.post("/api/chat/sessions").json()["session_token"]
    response = client.post(
        "/api/chat",
        json={"message": "А какие характеристики?"},
        headers={"Authorization": f"Bearer {second}"},
    ).json()
    assert response["products"] == []


def test_new_product_replaces_previous_context(client, auth):
    client.post("/api/chat", json={"message": "DEMO-002"}, headers=auth)
    response = client.post("/api/chat", json={"message": "DEMO-001"}, headers=auth).json()
    assert response["products"][0]["id"] == 900001
    response = client.post(
        "/api/chat",
        json={"message": "Характеристики DEMO-003"},
        headers=auth,
    ).json()
    assert response["products"][0]["id"] == 900003


def test_session_required_expired_and_revoked(client, auth):
    assert client.post("/api/chat", json={"message": "DEMO-002"}).status_code == 401
    assert client.delete("/api/chat/sessions/current", headers=auth).status_code == 204
    assert client.post("/api/chat", json={"message": "DEMO-002"}, headers=auth).status_code == 401
    token = client.post("/api/chat/sessions").json()["session_token"]
    client.app.state.sessions.items[token].expires_at = time.monotonic() - 1
    expired = client.post(
        "/api/chat", json={"message": "DEMO-002"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert expired.status_code == 401


def test_no_cart_operation_even_after_confirmation(client, auth):
    client.post("/api/chat", json={"message": "DEMO-002"}, headers=auth)
    response = client.post(
        "/api/chat", json={"message": "Да, добавь 2 в корзину"}, headers=auth
    ).json()
    assert response["cart_action"] == "integration_required"
    assert "не добавлены" in response["message"]
    assert client.get("/health").json()["cart_enabled"] is False


def test_out_of_stock_product_includes_explained_candidates(client, auth):
    response = client.post("/api/chat", json={"message": "DEMO-001"}, headers=auth).json()
    assert [a["product"]["id"] for a in response["alternatives"]] == [900002]
    assert response["alternatives"][0]["reasons"]
    assert "синтетические" in response["message"]


def test_no_invented_purchase_terms(client, auth):
    response = client.post("/api/chat", json={"message": "Как оплатить?"}, headers=auth).json()
    assert "не подключены" in response["message"]
    assert client.get("/api/purchase-terms").json()["verified"] is False


def test_verified_terms_use_configured_source():
    terms = PurchaseTerms(
        verified=True, source_url="https://ekt.kz/", payment="Согласованный тестовый текст"
    )
    answer = terms.answer("payment")
    assert "Согласованный тестовый текст" in answer
    assert "https://ekt.kz/" in answer
    assert "Доставка" not in answer


def test_blank_and_oversized_messages_rejected(client, auth):
    for message in ("   ", "x" * 2001):
        assert client.post("/api/chat", json={"message": message}, headers=auth).status_code == 422


def test_conflicting_current_is_not_stated_as_a_confirmed_fact(client, auth):
    response = client.post("/api/chat", json={"message": "DEMO-003"}, headers=auth).json()
    assert "Ток: 25" not in response["message"]
    assert "Ток: противоречивые данные" in response["message"]
