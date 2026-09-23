"""Serve only the public frontend assets beside the existing API and cart."""
from pathlib import Path

from fastapi.responses import FileResponse

from app.main import create_app

app = create_app()
FRONTEND = Path(__file__).resolve().parents[2] / 'frontend'


@app.get('/', include_in_schema=False)
def homepage():
    return FileResponse(FRONTEND / 'index.html', headers={'Cache-Control': 'no-store'})


def asset_handler(filename: str):
    def serve():
        return FileResponse(FRONTEND / filename, headers={'Cache-Control': 'no-cache'})
    return serve


for filename in ('styles.css', 'app.js', 'api.js', 'icons.js'):
    app.add_api_route(
        '/' + filename, asset_handler(filename), methods=['GET'], include_in_schema=False
    )
