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


async def sync(
    settings: Settings,
    pages: int,
    max_details: int,
    concurrency: int = 2,
    client=None,
    progress=None,
) -> dict:
    if not (1 <= pages <= 10 and 0 <= max_details <= 200 and 1 <= concurrency <= 4):
        raise ValueError("Use 1–10 pages, 0–200 details and 1–4 concurrent requests.")
    catalog = Catalog(settings.catalog_db, settings.catalog_stale_seconds)
    client = client or EktClient(settings)
    summary = {
        "listed": 0,
        "detailed": 0,
        "failures": 0,
        "errors": [],
        "started_at": datetime.now(UTC).isoformat(),
    }
    seen = set()
    product_ids = []

    def failed(kind, identifier):
        summary["failures"] += 1
        summary["errors"].append({"kind": kind, "id": identifier})

    semaphore = asyncio.Semaphore(concurrency)

    async def detail(product_id):
        async with semaphore:
            try:
                raw, observed_at = await client.detail(product_id)
                catalog.upsert(raw, observed_at)
                summary["detailed"] += 1
            except CatalogUnavailable:
                failed("detail_unavailable", product_id)
            except (ValueError, KeyError, TypeError):
                failed("invalid_detail", product_id)
            if progress and (summary["detailed"] + summary["failures"]) % 10 == 0:
                progress({"detailed": summary["detailed"], "failures": summary["failures"]})

    try:
        for page in range(1, pages + 1):
            try:
                payload = await client.page(page)
            except CatalogUnavailable:
                failed("page_unavailable", page)
                continue
            items = payload["items"]
            if not items:
                break
            for raw in items:
                try:
                    product_id = raw["id"]
                    catalog.upsert(raw, datetime.now(UTC))
                    if product_id not in seen:
                        seen.add(product_id)
                        summary["listed"] += 1
                        if len(product_ids) < max_details:
                            product_ids.append(product_id)
                except (ValueError, KeyError, TypeError):
                    failed("invalid_list_item", page)
            if progress:
                progress({"page": page, "listed": summary["listed"]})
        await asyncio.gather(*(detail(product_id) for product_id in product_ids))
    finally:
        await client.close()
    summary["finished_at"] = datetime.now(UTC).isoformat()
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files", nargs="+", type=Path)
    parser.add_argument("--source", choices=["ekt_api", "synthetic"], default="ekt_api")
    parser.add_argument("--pages", type=int, default=1)
    parser.add_argument("--max-details", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=None)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    settings = Settings(**({"ekt_timeout_seconds": args.timeout} if args.timeout else {}))
    if args.files:
        result = {"imported": import_files(Catalog(settings.catalog_db), args.files, args.source)}
    else:
        try:
            result = asyncio.run(
                sync(
                    settings,
                    args.pages,
                    args.max_details,
                    args.concurrency,
                    progress=lambda event: print(json.dumps(event), flush=True),
                )
            )
        except (CatalogUnavailable, ValueError) as exc:
            parser.exit(1, f"{exc}\n")
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    if result.get("failures"):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
