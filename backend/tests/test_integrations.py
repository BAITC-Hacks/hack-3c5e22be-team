import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import httpx2
import pytest
from fastapi.testclient import TestClient
from openai import AsyncOpenAI

from app.ai import Interpreter
from app.ekt import CatalogUnavailable, EktClient
from app.main import create_app
from app.models import Intent


def test_openai_uses_structured_intent_and_no_server_side_history(settings):
    mock = SimpleNamespace(
        responses=SimpleNamespace(
            parse=AsyncMock(
                return_value=SimpleNamespace(
                    output_parsed=Intent(
                        intent="details", query="", product_id=900002, topic="all"
                    ),
                )
            )
        )
    )
    interpreter = Interpreter(settings, client=mock)
    result, mode, warning = asyncio.run(interpreter.resolve("А наличие?", [], [900002]))
    assert result.product_id == 900002
    assert mode == "openai" and warning is None
    arguments = mock.responses.parse.call_args.kwargs
    assert arguments["store"] is False
    assert arguments["text_format"] is Intent
    assert "api_key" not in arguments


def test_hallucinated_product_id_is_discarded(settings):
    mock = SimpleNamespace(
        responses=SimpleNamespace(
            parse=AsyncMock(
                return_value=SimpleNamespace(
                    output_parsed=Intent(
                        intent="details", query="DEMO-002", product_id=777, topic="all"
                    ),
                )
            )
        )
    )
    result, _, _ = asyncio.run(Interpreter(settings, client=mock).resolve("Наличие", [], [900002]))
    assert result.product_id is None


def test_openai_refusal_falls_back_without_inventing_facts(settings):
    mock = SimpleNamespace(
        responses=SimpleNamespace(
            parse=AsyncMock(
                return_value=SimpleNamespace(output_parsed=None),
            )
        )
    )
    result, mode, warning = asyncio.run(
        Interpreter(settings, client=mock).resolve("DEMO-002", [], [])
    )
    assert mode == "rules" and warning
    assert result.query == "DEMO-002"


def test_upstream_timeout_is_safe(settings):
    settings = type(settings)(
        _env_file=None,
        **{
            **settings.model_dump(),
            "ekt_username": "test-user",
            "ekt_password": "test-only-password",
        },
    )

    def fail(request):
        assert request.url.host == "ekt.kz"
        assert request.headers["Authorization"].startswith("Basic ")
        raise httpx.ReadTimeout("private diagnostic", request=request)

    async def run():
        client = EktClient(settings, transport=httpx.MockTransport(fail))
        try:
            with pytest.raises(CatalogUnavailable) as error:
                await client.detail(123)
            assert "private diagnostic" not in str(error.value)
            assert "password" not in str(error.value)
        finally:
            await client.close()

    asyncio.run(run())


def test_failed_refresh_never_returns_cached_success(settings, raw_products):
    settings.ekt_live_enabled = True
    ekt = SimpleNamespace(
        detail=AsyncMock(side_effect=CatalogUnavailable("Нет связи с каталогом")), close=AsyncMock()
    )
    from datetime import UTC, datetime

    with TestClient(create_app(settings, ekt_client=ekt)) as client:
        client.app.state.catalog.upsert(raw_products[1], datetime.now(UTC), "synthetic")
        assert client.get("/api/products/900002?refresh=true").status_code == 503
        assert client.get("/api/products/900002").status_code == 200


def test_redirect_not_followed_with_credentials(settings):
    settings = type(settings)(
        _env_file=None,
        **{
            **settings.model_dump(),
            "ekt_username": "test-user",
            "ekt_password": "test-only",
        },
    )
    requests = []

    def redirect(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://other.example/"})

    async def run():
        client = EktClient(settings, transport=httpx.MockTransport(redirect))
        try:
            with pytest.raises(CatalogUnavailable):
                await client.detail(123)
        finally:
            await client.close()

    asyncio.run(run())
    assert len(requests) == 1


@pytest.mark.parametrize("status", [200, 429])
def test_real_openai_sdk_with_mocked_http_transport(settings, status):
    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        if status == 429:
            return httpx2.Response(
                429, json={"error": {"message": "Rate limited", "type": "limit"}}
            )
        intent = {"intent": "search", "query": "DEMO-002", "product_id": None, "topic": "all"}
        return httpx2.Response(
            200,
            json={
                "id": "resp_test",
                "object": "response",
                "created_at": 1,
                "status": "completed",
                "model": settings.openai_model,
                "output": [
                    {
                        "id": "msg_test",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {"type": "output_text", "text": json.dumps(intent), "annotations": []}
                        ],
                    }
                ],
            },
        )

    async def run():
        client = AsyncOpenAI(
            api_key="test-only-not-a-real-key",
            max_retries=0,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handle)),
        )
        interpreter = Interpreter(settings, client=client)
        try:
            intent, mode, warning = await interpreter.resolve("DEMO-002", [], [])
            assert intent.query.casefold() == "demo-002"
            assert mode == ("openai" if status == 200 else "rules")
            assert bool(warning) == (status != 200)
        finally:
            await interpreter.close()

    asyncio.run(run())
    assert len(requests) == 1
    assert requests[0]["store"] is False
    assert requests[0]["text"]["format"]["type"] == "json_schema"
    assert requests[0]["text"]["format"]["strict"] is True
