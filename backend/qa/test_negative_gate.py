"""Additional QA gate. Run explicitly: python -m pytest qa -v.

Failures remain visible until the corresponding blocker is fixed.
"""

import json
import os
import time
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.import_catalog import import_files
from app.main import create_app


@pytest.fixture
def qa_client(tmp_path):
    settings = Settings(
        _env_file=None,
        openai_api_key="",
        ekt_live_enabled=False,
        catalog_db=tmp_path / "catalog.sqlite3",
        purchase_terms_path=tmp_path / "terms.json",
        demo_cart_enabled=True,
    )
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        client.app.state.catalog.upsert(
            {"id": 900002, "name": "QA DEMO", "article": "QA-002", "price": 1200, "quantity": 8},
            datetime.now(UTC),
            "synthetic",
        )
        token = client.post("/api/chat/sessions").json()["session_token"]
        yield client, {"Authorization": "Bearer " + token}


@pytest.mark.parametrize("endpoint", ["product", "proposal"])
def test_qa004_oversized_product_id_is_client_error(qa_client, endpoint):
    client, auth = qa_client
    huge = 2**100
    if endpoint == "product":
        response = client.get(f"/api/products/{huge}")
    else:
        response = client.post(
            "/api/cart/proposals", headers=auth, json={"product_id": huge, "quantity": 1}
        )
    assert 400 <= response.status_code < 500, f"Got {response.status_code}: {response.text}"
    assert client.get("/api/cart", headers=auth).json()["items"] == []


def test_qa003_documented_demo_reimport_restores_buyable_fixture(qa_client, tmp_path):
    client, auth = qa_client
    fixture = tmp_path / "demo.json"
    fixture.write_text(
        json.dumps(
            [{"id": 900002, "name": "QA DEMO", "article": "QA-002", "price": 1200, "quantity": 8}]
        ),
        encoding="utf-8",
    )
    old = time.time() - 3600
    os.utime(fixture, (old, old))
    import_files(client.app.state.catalog, [fixture], "synthetic")
    import_files(client.app.state.catalog, [fixture], "synthetic")
    response = client.post(
        "/api/cart/proposals", headers=auth, json={"product_id": 900002, "quantity": 1}
    )
    assert response.status_code == 200, response.text


def test_safe_missing_terms(qa_client):
    client, _ = qa_client
    terms = client.get("/api/purchase-terms").json()
    assert terms["verified"] is False
    assert terms["payment"] is None and terms["delivery"] is None


def test_forged_session_and_get_cannot_confirm(qa_client):
    client, auth = qa_client
    response = client.post(
        "/api/cart/proposals", headers=auth, json={"product_id": 900002, "quantity": 1}
    )
    proposal = response.json()
    assert client.get("/api/cart/confirm", params=proposal).status_code == 405
    assert (
        client.post(
            "/api/cart/confirm",
            json={"proposal_id": proposal["proposal_id"]},
            headers={"Authorization": "Bearer forged", "Idempotency-Key": "qa-key-01"},
        ).status_code
        == 401
    )
    assert client.get("/api/cart", headers=auth).json()["items"] == []


def test_wrong_content_type_and_additional_confirm_fields(qa_client):
    client, auth = qa_client
    response = client.post(
        "/api/cart/proposals", headers=auth, json={"product_id": 900002, "quantity": 1}
    )
    proposal_id = response.json()["proposal_id"]
    headers = {**auth, "Idempotency-Key": "qa-key-02"}
    assert (
        client.post(
            "/api/cart/confirm", headers=headers, json={"proposal_id": proposal_id, "quantity": 8}
        ).status_code
        == 422
    )
    assert client.post("/api/cart/confirm", headers=headers, content="not-json").status_code == 422
    assert client.get("/api/cart", headers=auth).json()["items"] == []


def test_independent_sessions_can_reuse_idempotency_key(qa_client):
    client, first = qa_client
    token = client.post("/api/chat/sessions").json()["session_token"]
    second = {"Authorization": "Bearer " + token}
    for auth in (first, second):
        proposal = client.post(
            "/api/cart/proposals", headers=auth, json={"product_id": 900002, "quantity": 1}
        ).json()
        response = client.post(
            "/api/cart/confirm",
            json={"proposal_id": proposal["proposal_id"]},
            headers={**auth, "Idempotency-Key": "same-key-01"},
        )
        assert response.status_code == 200 and response.json()["replayed"] is False
        assert response.json()["cart"]["items"][0]["quantity"] == 1
