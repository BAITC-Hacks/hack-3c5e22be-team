"""Explicit local import or bounded read-only API sync. Run from backend/."""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from app.catalog import Catalog
from app.config import Settings
from app.ekt import CatalogUnavailable, EktClient


def import_files(catalog: Catalog, paths: list[Path], source: str) -> int:
    count = 0
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        items = payload.get("items", [payload]) if isinstance(payload, dict) else payload
        observed_at = datetime.fromtimestamp(path.stat().st_mtime, UTC)
        for raw in items:
            catalog.upsert(raw, observed_at, source)
            count += 1
    return count


async def sync(settings: Settings, pages: int, max_details: int) -> dict:
    catalog = Catalog(settings.catalog_db, settings.catalog_stale_seconds)
    client = EktClient(settings)
    summary = {"listed": 0, "detailed": 0, "failures": 0}
    seen = set()
    try:
        for page in range(1, pages + 1):
            payload = await client.page(page)
            items = payload["items"]
            if not items:
                break
            for raw in items:
                catalog.upsert(raw, datetime.now(UTC))
                summary["listed"] += 1
                if raw["id"] in seen or len(seen) >= max_details:
                    continue
                seen.add(raw["id"])
                try:
                    detail, observed_at = await client.detail(raw["id"])
                    catalog.upsert(detail, observed_at)
                    summary["detailed"] += 1
                except (CatalogUnavailable, ValueError, KeyError, TypeError):
                    summary["failures"] += 1
    finally:
        await client.close()
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files", nargs="+", type=Path)
    parser.add_argument("--source", choices=["ekt_api", "synthetic"], default="ekt_api")
    parser.add_argument("--pages", type=int, default=1)
    parser.add_argument("--max-details", type=int, default=20)
    args = parser.parse_args()
    settings = Settings()
    if args.files:
        result = {"imported": import_files(Catalog(settings.catalog_db), args.files, args.source)}
    else:
        if not 1 <= args.pages <= 10 or not 0 <= args.max_details <= 200:
            parser.error("Use 1–10 pages and 0–200 details per import.")
        try:
            result = asyncio.run(sync(settings, args.pages, args.max_details))
        except CatalogUnavailable as exc:
            parser.exit(1, f"{exc}\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
