from html import escape

from fastapi import Header, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.cart import CartError, ConfirmRequest, ProposalRequest

COOKIE_NAME = "ekt_demo_session"


def install_cart_routes(app, settings):
    @app.exception_handler(CartError)
    async def cart_error(request, exc):
        return JSONResponse(
            status_code=exc.status,
            content={"error": {"code": exc.code, "message": exc.message, "retryable": False}},
            headers={"Cache-Control": "no-store"},
        )

    def require_session(token):
        if not settings.demo_cart_enabled:
            raise CartError("CART_DISABLED", "Демонстрационная корзина отключена.", 503)
        session = app.state.sessions.get(token or "")
        if session is None:
            raise CartError("SESSION_EXPIRED", "Создайте новую сессию.", 401)
        return session

    def bearer(authorization):
        if not authorization or not authorization.startswith("Bearer "):
            raise CartError("SESSION_EXPIRED", "Требуется Bearer-сессия.", 401)
        return authorization[7:]

    async def operation(authorization, action):
        token = bearer(authorization)
        session = require_session(token)
        async with session.lock:
            require_session(token)  # Could expire or be deleted while waiting for the lock.
            return JSONResponse(action(session.cart), headers={"Cache-Control": "no-store"})

    @app.get("/api/cart")
    async def get_cart(authorization: str | None = Header(default=None)):
        return await operation(authorization, app.state.cart.snapshot)

    @app.post("/api/cart/proposals")
    async def propose(body: ProposalRequest, authorization: str | None = Header(default=None)):
        return await operation(authorization, lambda state: app.state.cart.propose(state, body))

    @app.post("/api/cart/confirm")
    async def confirm(
        body: ConfirmRequest,
        request: Request,
        authorization: str | None = Header(default=None),
        idempotency_key: str | None = Header(default=None),
    ):
        response = await operation(
            authorization,
            lambda state: app.state.cart.confirm(state, body.proposal_id, idempotency_key),
        )
        # Rebind navigation to the confirmed Bearer session even if another tab logged in.
        response.set_cookie(
            COOKIE_NAME,
            bearer(authorization),
            httponly=True,
            samesite="lax",
            path="/cart",
            secure=settings.cart_cookie_secure or request.url.scheme == "https",
            max_age=settings.session_ttl_seconds,
        )
        return response

    @app.get("/cart", response_class=HTMLResponse)
    async def cart_page(request: Request):
        token = request.cookies.get(COOKIE_NAME)
        session = require_session(token)
        async with session.lock:
            require_session(token)
            cart = app.state.cart.snapshot(session.cart)
        rows = "".join(
            "<tr><td>"
            + escape(item["name"])
            + "<br>"
            + escape(item["article"])
            + "</td><td>"
            + str(item["quantity"])
            + " шт</td><td>"
            + item["total"]
            + " KZT</td></tr>"
            for item in cart["items"]
        )
        body = f"""<!doctype html><html lang="ru"><meta charset="utf-8">
        <meta name="viewport" content="width=device-width,initial-scale=1">
        <title>Демонстрационная корзина</title>
        <style>body{{font:18px/1.5 system-ui;margin:24px;max-width:900px}}
        table{{width:100%;border-collapse:collapse}}td,th{{padding:12px;text-align:left;
        border-bottom:1px solid #ddd;overflow-wrap:anywhere}}a{{color:#1456ab}}</style>
        <h1>Демонстрационная корзина</h1><p>{escape(cart["warning"])}</p>
        <p>Данные этой сессии хранятся до её завершения или перезапуска сервера.</p>
        <table><thead><tr><th>Товар</th><th>Количество</th><th>Сумма</th></tr></thead>
        <tbody>{rows}</tbody></table><p>{"Корзина пуста." if not rows else ""}</p>
        <p>Итого: {cart["total"]} KZT</p><p>Версия корзины: {cart["version"]}</p>
        <a href="/cart">Обновить корзину</a></html>"""
        return HTMLResponse(
            body,
            headers={
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; "
                "frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
            },
        )
