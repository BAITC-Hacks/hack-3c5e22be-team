import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

from app.models import Product, QualityIssue, Stock

ATTRIBUTE_FIELDS = {
    "NOMINALNYY_TOK": "current",
    "NOMINALNOE_NAPRYAZHENIE": "voltage",
    "KOLICHESTVO_POLYUSOV": "poles",
    "NOMINALNAYA_OTKLYUCHAYUSHCHAYA_SPOSOBNOST": "breaking_capacity",
    "TIP_USTANOVKI": "mounting",
    "TORGOVAYA_MARKA": "brand",
}


def number(value) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value).strip().replace(",", "."))
        return parsed if parsed.is_finite() and parsed >= 0 else None
    except InvalidOperation:
        return None


def safe_url(value) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlparse(value)
    if parsed.scheme == "https" and parsed.hostname in {"ekt.kz", "www.ekt.kz"}:
        return value if not parsed.username and not parsed.password else None
    return None


def text_key(value: str) -> str:
    return re.sub(r"\s+", "", value.casefold().replace("ё", "е").replace(",", "."))


def normalize(raw: dict, observed_at: datetime, source: str, stale_seconds: int) -> Product:
    properties = raw.get("properties") or {}
    attributes = {
        name: str(properties[field])
        for field, name in ATTRIBUTE_FIELDS.items()
        if properties.get(field) not in (None, "")
    }
    issues = []
    declared = attributes.get("current", "")
    title_currents = re.findall(r"(?<![\w.,])(\d+(?:[.,]\d+)?)\s*[АA](?!\w)", raw["name"])
    described = re.findall(
        r"номинальный\s+ток\s*:\s*(\d+(?:[.,]\d+)?)\s*[АA](?!\w)",
        raw.get("description") or "",
        re.IGNORECASE,
    )
    field_current = re.fullmatch(r"\s*(\d+(?:[.,]\d+)?)\s*[АA]\s*", declared)
    if field_current and any(
        number(value) != number(field_current.group(1)) for value in title_currents + described
    ):
        issues.append(
            QualityIssue(
                code="conflicting_current",
                message=(
                    "Номинальный ток в свойствах противоречит названию или описанию; "
                    "уточните данные."
                ),
            )
        )
    details = "quantity" in raw or "properties" in raw
    return Product(
        id=raw["id"],
        name=raw["name"],
        article=str(raw.get("article") or ""),
        supplier_article=properties.get("ARTIKULPOSTAVSHCHIKA"),
        description=raw.get("description") or "",
        price=number(raw.get("price")),
        quantity=number(raw.get("quantity")),
        stores=[
            Stock(id=s["id"], name=s["name"], quantity=number(s.get("quantity")))
            for s in raw.get("stores", [])
        ],
        url=safe_url(raw.get("url")),
        image=safe_url(raw.get("image")),
        category=properties.get("OBYEM"),
        attributes=attributes,
        minimum_quantity_raw=str(properties["KRATNOST_MIN"])
        if "KRATNOST_MIN" in properties
        else None,
        quality_issues=issues,
        source=source,
        observed_at=observed_at,
        stale=(datetime.now(UTC) - observed_at).total_seconds() > stale_seconds,
        detail_loaded=details,
    )
