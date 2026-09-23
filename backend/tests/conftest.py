import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def raw_products():
    return json.loads((Path(__file__).parents[1] / "examples/demo_catalog.json").read_text("utf-8"))


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        openai_api_key="",
        ekt_username="",
        ekt_password="",
        ekt_live_enabled=False,
        catalog_db=tmp_path / "catalog.sqlite3",
        purchase_terms_path=tmp_path / "terms.json",
    )


@pytest.fixture
def client(settings, raw_products):
    with TestClient(create_app(settings)) as client:
        for raw in raw_products:
            client.app.state.catalog.upsert(raw, datetime.now(UTC), "synthetic")
        yield client


@pytest.fixture
def auth(client):
    token = client.post("/api/chat/sessions").json()["session_token"]
    return {"Authorization": f"Bearer {token}"}
