"""Single-process entrypoint. --demo uses isolated synthetic data on loopback."""

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))


def main(demo=False):
    import uvicorn

    from app.catalog import Catalog
    from app.config import Settings
    from app.main import create_app

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--port", type=int, default=int(os.getenv("PORT", "5173" if demo else "8000"))
    )
    args = parser.parse_args()
    if demo:
        settings = Settings(
            _env_file=None,
            openai_api_key="",
            ekt_live_enabled=False,
            catalog_db=ROOT / "backend/data/demo.sqlite3",
            catalog_stale_seconds=3600,
            purchase_terms_path=ROOT / "backend/examples/purchase_terms.ekt.json",
            demo_cart_enabled=True,
            cart_cookie_secure=False,
            public_demo=False,
        )
    else:
        settings = Settings()
    if demo or os.getenv("DEMO_SEED_SYNTHETIC", "false").lower() == "true":
        if settings.ekt_live_enabled:
            raise ValueError("Synthetic seed requires EKT_LIVE_ENABLED=false")
        catalog = Catalog(settings.catalog_db, settings.catalog_stale_seconds)
        if any(p.source != "synthetic" for p in catalog.all()):
            raise ValueError("Refusing to seed a database containing real catalogue data")
        for item in json.loads((ROOT / "backend/examples/demo_catalog.json").read_text("utf-8")):
            catalog.upsert(item, datetime.now(UTC), "synthetic")
    uvicorn.run(
        create_app(settings),
        host="127.0.0.1" if demo else "0.0.0.0",
        port=args.port,
        workers=1,
        proxy_headers=not demo,
        forwarded_allow_ips=os.getenv("TRUSTED_PROXY_IPS", "127.0.0.1"),
    )


if __name__ == "__main__":
    main()
