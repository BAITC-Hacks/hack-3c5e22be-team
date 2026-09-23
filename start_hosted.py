"""Single-process deployment entry point for Railway/Render and local HTTPS tunnels."""
import json
import os
from pathlib import Path
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT / 'backend')
sys.path.insert(0, str(ROOT / 'backend'))

if os.environ.get('EKT_DEPLOY_DEMO', 'true').lower() == 'true':
    os.environ.update({
        'OPENAI_API_KEY': '', 'EKT_USERNAME': '', 'EKT_PASSWORD': '',
        'EKT_LIVE_ENABLED': 'false', 'DEMO_CART_ENABLED': 'true',
        'CATALOG_DB': str(ROOT / 'backend/data/hosted-demo.sqlite3'),
        'CATALOG_STALE_SECONDS': '86400',
    })
    from app.catalog import Catalog
    catalog = Catalog(Path(os.environ['CATALOG_DB']), 86400)
    # Only the explicitly synthetic fixture gets a new demonstration timestamp.
    fixture = json.loads((ROOT / 'backend/examples/demo_catalog.json').read_text(encoding='utf-8'))
    for product in fixture:
        catalog.upsert(product, datetime.now(timezone.utc), 'synthetic')

if __name__ == '__main__':
    import uvicorn
    uvicorn.run('app.site:app', host=os.environ.get('HOST', '0.0.0.0'),
                port=int(os.environ.get('PORT', '8080')), workers=1,
                proxy_headers=True, forwarded_allow_ips=os.environ.get('FORWARDED_ALLOW_IPS', '127.0.0.1'))
