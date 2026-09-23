import inspect
import secrets
import time
from html import escape
from pathlib import Path
from string import Template

from fastapi import Header, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from app.cart import CartError, ConfirmRequest, ProposalRequest

COOKIE_NAME = "ekt_demo_session"


def set_cart_cookie(response, request, token, settings):
    # Remove the previous narrow-path cookie when upgrading an existing browser.
    response.delete_cookie(COOKIE_NAME, path="/cart")
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        path="/",
        secure=settings.cart_cookie_secure or request.url.scheme == "https",
        max_age=settings.session_ttl_seconds,
    )


def install_cart_routes(app, settings):
    @app.middleware("http")
    async def cart_cache_control(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith(("/cart", "/api/cart")):
            response.headers["Cache-Control"] = "no-store"
        return response

    def require_session(token):
        if not settings.demo_cart_enabled:
            raise CartError("CART_DISABLED", "Корзина прототипа отключена.", 503)
        session = app.state.sessions.get(token or "")
        if session is None:
            raise CartError("SESSION_EXPIRED", "Создайте новую сессию.", 401)
        return session

    def bearer(authorization):
        if not authorization or not authorization.startswith("Bearer "):
            raise CartError("SESSION_EXPIRED", "Требуется Bearer-сессия.", 401)
        return authorization[7:]

    async def operation(token, action, mutation=False):
        session = require_session(token)
        if mutation:
            now = time.monotonic()
            session.cart_requests = [t for t in session.cart_requests if now - t < 60]
            if len(session.cart_requests) >= settings.cart_requests_per_minute:
                raise CartError(
                    "RATE_LIMITED", "Слишком много операций. Повторите через минуту.", 429
                )
            session.cart_requests.append(now)
        async with session.lock:

            def validate():
                if require_session(token) is not session:
                    raise CartError("SESSION_EXPIRED", "Сессия завершена.", 401)

            validate()
            result = action(session.cart, validate)
            if inspect.isawaitable(result):
                result = await result
            validate()
            return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @app.get("/api/cart")
    async def get_cart(authorization: str | None = Header(default=None)):
        return await operation(
            bearer(authorization), lambda state, _: app.state.cart.snapshot(state)
        )

    @app.post("/api/cart/proposals")
    async def propose(body: ProposalRequest, authorization: str | None = Header(default=None)):
        return await operation(
            bearer(authorization),
            lambda state, guard: app.state.cart.propose(state, body, guard),
            True,
        )

    @app.post("/api/cart/confirm")
    async def confirm(
        body: ConfirmRequest,
        request: Request,
        authorization: str | None = Header(default=None),
        idempotency_key: str | None = Header(default=None),
    ):
        token = bearer(authorization)
        response = await operation(
            token,
            lambda state, guard: app.state.cart.confirm(
                state, body.proposal_id, idempotency_key, guard
            ),
            True,
        )
        set_cart_cookie(response, request, token, settings)
        return response

    @app.post("/api/cart/cancel")
    async def cancel(body: ConfirmRequest, authorization: str | None = Header(default=None)):
        return await operation(
            bearer(authorization),
            lambda state, _: app.state.cart.cancel(state, body.proposal_id),
            True,
        )

    # Same-origin cart page: cookie + CSRF. Public API: Bearer only.
    def page_token(request, mutate=False):
        token = request.cookies.get(COOKIE_NAME)
        session = require_session(token)
        if mutate:
            csrf = request.headers.get("X-CSRF-Token", "")
            if (
                request.headers.get("origin") != str(request.base_url).rstrip("/")
                or not csrf.isascii()
                or not secrets.compare_digest(csrf, session.csrf_token)
            ):
                raise CartError(
                    "CSRF_INVALID", "Обновите страницу корзины и повторите действие.", 403
                )
        return token

    @app.get("/cart/state", include_in_schema=False)
    async def page_state(request: Request):
        return await operation(page_token(request), lambda state, _: app.state.cart.snapshot(state))

    @app.post("/cart/proposals", include_in_schema=False)
    async def page_propose(body: ProposalRequest, request: Request):
        return await operation(
            page_token(request, True),
            lambda state, guard: app.state.cart.propose(state, body, guard),
            True,
        )

    @app.post("/cart/confirm", include_in_schema=False)
    async def page_confirm(
        body: ConfirmRequest,
        request: Request,
        idempotency_key: str | None = Header(default=None),
    ):
        return await operation(
            page_token(request, True),
            lambda state, guard: app.state.cart.confirm(
                state, body.proposal_id, idempotency_key, guard
            ),
            True,
        )

    @app.post("/cart/cancel", include_in_schema=False)
    async def page_cancel(body: ConfirmRequest, request: Request):
        return await operation(
            page_token(request, True),
            lambda state, _: app.state.cart.cancel(state, body.proposal_id),
            True,
        )

    @app.get("/cart/app.js", include_in_schema=False)
    async def page_script():
        return FileResponse(Path(__file__).parent / "static/cart.js", media_type="text/javascript")

    @app.get("/cart", response_class=HTMLResponse)
    async def cart_page(request: Request):
        token = page_token(request)
        session = require_session(token)
        async with session.lock:
            require_session(token)
            cart = app.state.cart.snapshot(session.cart)
        rows = "".join(
            "<tr><td>"
            + escape(item["name"])
            + "<br><small>"
            + escape(item["article"])
            + "</small></td><td>"
            + str(item["quantity"])
            + " "
            + escape(item["unit"] or "(единица не указана)")
            + "</td><td>"
            + escape(item["total"])
            + " "
            + escape(item["currency"] or "(валюта не указана)")
            + '</td><td><button data-set="'
            + str(item["product_id"])
            + '" data-quantity="'
            + str(item["quantity"])
            + '">Изменить</button> '
            + '<button data-remove="'
            + str(item["product_id"])
            + '">Удалить</button></td></tr>'
            for item in cart["items"]
        )
        template = (Path(__file__).parent / "static/cart.html").read_text("utf-8")
        body = Template(template).substitute(
            csrf=escape(session.csrf_token),
            warning=escape(cart["warning"]),
            rows=rows,
            empty="Корзина пуста." if not rows else "",
            total=escape(cart["total"]),
            currency=escape(cart["currency"] or "· валюта не указана"),
            version=cart["version"],
            clear_disabled="disabled" if not rows else "",
        )
        return HTMLResponse(
            body,
            headers={
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; "
                "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
                "base-uri 'none'; form-action 'none'",
            },
        )
