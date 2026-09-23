from datetime import UTC, datetime, timedelta

import pytest

from app.normalize import normalize, number, safe_url


@pytest.mark.parametrize("query", ["DEMO-002", "TEST-B16", "900002", "Автомат B 16А"])
def test_search_by_catalog_identifiers_and_name(client, query):
    response = client.get("/api/products", params={"query": query})
    assert response.status_code == 200
    assert [p["id"] for p in response.json()] == [900002]


def test_list_only_product_does_not_claim_zero_stock(client):
    client.app.state.catalog.upsert(
        {"id": 42, "name": "Товар без деталей", "article": "LIST-42", "price": 10},
        datetime.now(UTC),
        "synthetic",
    )
    product = client.get("/api/products/42").json()
    assert product["quantity"] is None
    assert product["detail_loaded"] is False
    assert product["currency"] is None
    assert product["certificate_status"] == "unknown"


def test_conflicting_current_blocks_recommendations(client):
    product = client.get("/api/products/900003").json()
    assert product["quality_issues"][0]["code"] == "conflicting_current"
    response = client.get("/api/products/900003/alternatives").json()
    assert response["items"] == []
    assert "противоречивы" in response["warnings"][0]


def test_alternatives_require_matching_characteristics_and_positive_stock(client):
    response = client.get("/api/products/900001/alternatives").json()
    assert [item["product"]["id"] for item in response["items"]] == [900002]
    assert response["items"][0]["requires_verification"] is True
    assert len(response["items"][0]["reasons"]) == 5


@pytest.mark.parametrize(
    "change", ["missing_mount", "different_voltage", "zero_stock", "unknown_stock"]
)
def test_unsuitable_alternative_excluded(client, raw_products, change):
    raw = raw_products[1]
    if change == "missing_mount":
        raw["properties"].pop("TIP_USTANOVKI")
    elif change == "different_voltage":
        raw["properties"]["NOMINALNOE_NAPRYAZHENIE"] = "230В"
    elif change == "zero_stock":
        raw["quantity"] = 0
    else:
        raw.pop("quantity")
    client.app.state.catalog.upsert(raw, datetime.now(UTC), "synthetic")
    assert client.get("/api/products/900001/alternatives").json()["items"] == []


def test_list_refresh_does_not_make_old_stock_fresh(client, raw_products):
    old = datetime.now(UTC) - timedelta(days=2)
    catalog = client.app.state.catalog
    raw_products[1]["id"] = 900099
    catalog.upsert(raw_products[1], old, "synthetic")
    catalog.upsert(
        {"id": 900099, "name": "New list title", "article": "DEMO-002", "price": 2000},
        datetime.now(UTC),
        "synthetic",
    )
    product = client.get("/api/products/900099").json()
    assert product["stale"] is True
    assert product["observed_at"] == old.isoformat().replace("+00:00", "Z")
    assert product["price"] == "1200"


def test_description_current_conflict_is_detected(raw_products):
    raw = raw_products[1]
    raw["name"] = "Автомат без значения тока в названии"
    raw["description"] = "Номинальный ток: 32А"
    product = normalize(raw, datetime.now(UTC), "synthetic", 300)
    assert product.quality_issues


def test_unknown_product_and_invalid_query(client):
    assert client.get("/api/products/123").status_code == 404
    assert client.get("/api/products", params={"query": "", "limit": 100}).status_code == 422


def test_refresh_is_explicitly_disabled(client):
    response = client.get("/api/products/900002?refresh=true")
    assert response.status_code == 503


@pytest.mark.parametrize("value", ["NaN", "Infinity", -1, True, None, "unknown"])
def test_invalid_numbers_are_unknown(value):
    assert number(value) is None


def test_untrusted_links_are_not_returned():
    assert safe_url("javascript:alert(1)") is None
    assert safe_url("https://ekt.kz.evil.example/item") is None
    assert safe_url("https://user:password@ekt.kz/item") is None
    assert safe_url("https://ekt.kz/catalog/item") == "https://ekt.kz/catalog/item"
