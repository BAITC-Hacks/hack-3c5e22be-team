from datetime import UTC, datetime

import httpx

from app.config import Settings


class CatalogUnavailable(Exception):
    """Safe error boundary: never expose upstream bodies or credentials."""


class EktClient:
    def __init__(self, settings: Settings, transport=None):
        self.configured = bool(settings.ekt_username and settings.ekt_password.get_secret_value())
        self.client = httpx.AsyncClient(
            base_url="https://ekt.kz/api/",
            auth=httpx.BasicAuth(settings.ekt_username, settings.ekt_password.get_secret_value()),
            timeout=settings.ekt_timeout_seconds,
            follow_redirects=False,
            transport=transport,
        )

    async def _get(self, path: str, params: dict) -> dict:
        if not self.configured:
            raise CatalogUnavailable("Не настроен доступ к каталогу.")
        try:
            response = await self.client.get(path, params=params)
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError("Expected object")
            return result
        except (httpx.HTTPError, ValueError) as exc:
            raise CatalogUnavailable(
                "Каталог недоступен; актуальность данных не подтверждена."
            ) from exc

    async def page(self, page: int) -> dict:
        result = await self._get("products", {"page": page})
        if not isinstance(result.get("items"), list):
            raise CatalogUnavailable("Каталог вернул неизвестный формат списка.")
        return result

    async def detail(self, product_id: int) -> tuple[dict, datetime]:
        result = await self._get("products/detail", {"id": product_id})
        if result.get("id") != product_id or "quantity" not in result:
            raise CatalogUnavailable("Каталог вернул неполную карточку товара.")
        return result, datetime.now(UTC)

    async def close(self):
        await self.client.aclose()
