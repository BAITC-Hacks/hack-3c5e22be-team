"""Serve the existing frontend without exposing repository files or API fallbacks."""

from pathlib import Path

from fastapi.responses import FileResponse

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


def install_site_routes(app):
    def resource(name):
        async def serve():
            return FileResponse(FRONTEND / name, headers={"Cache-Control": "no-cache"})

        return serve

    app.add_api_route("/", resource("index.html"), methods=["GET"], include_in_schema=False)
    for name in ("styles.css", "api.js", "app.js", "icons.js"):
        app.add_api_route(f"/{name}", resource(name), methods=["GET"], include_in_schema=False)
