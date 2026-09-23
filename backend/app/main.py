from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app.ai import Interpreter
from app.cart import DemoCart
from app.cart_routes import COOKIE_NAME, install_cart_routes
from app.catalog import Catalog
from app.chat import ChatService, Sessions
from app.config import Settings
from app.ekt import CatalogUnavailable, EktClient
from app.models import AlternativesResult, ChatRequest, ChatResponse, Product
from app.terms import load_terms


def create_app(settings: Settings | None = None, interpreter=None, ekt_client=None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.catalog = Catalog(settings.catalog_db, settings.catalog_stale_seconds)
        app.state.interpreter = interpreter or Interpreter(settings)
        app.state.ekt = ekt_client or EktClient(settings)
        app.state.sessions = Sessions(settings.session_ttl_seconds, settings.max_sessions)
        app.state.cart = DemoCart(app.state.catalog, settings.cart_proposal_ttl_seconds)
        app.state.terms = load_terms(settings.purchase_terms_path)
        app.state.chat = ChatService(app.state.catalog, app.state.interpreter, app.state.terms)
        yield
        await app.state.interpreter.close()
        await app.state.ekt.close()

    app = FastAPI(title="EKT Assistant — Backend", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
        allow_credentials=True,
    )
    install_cart_routes(app, settings)

    @app.get("/health")
    def health():
        return {
            "status": "ok",
            "catalog_count": len(app.state.catalog.all()),
            "ai_configured": settings.ai_configured,
            "live_catalog_enabled": settings.ekt_live_enabled,
            "cart_enabled": settings.demo_cart_enabled,
            "cart_mode": "demo" if settings.demo_cart_enabled else "disabled",
        }

    @app.get("/api/products", response_model=list[Product])
    def search(
        query: str = Query(min_length=1, max_length=200), limit: int = Query(default=5, ge=1, le=20)
    ):
        return app.state.catalog.search(query, limit)

    @app.get("/api/products/{product_id}", response_model=Product)
    async def product(product_id: int, refresh: bool = False):
        current = app.state.catalog.get(product_id)
        if current is None:
            raise HTTPException(404, "Товар отсутствует в загруженной выборке.")
        if refresh:
            if not settings.ekt_live_enabled:
                raise HTTPException(503, "Проверка живого каталога отключена.")
            try:
                raw, observed_at = await app.state.ekt.detail(product_id)
                app.state.catalog.upsert(raw, observed_at)
            except CatalogUnavailable as exc:
                raise HTTPException(503, str(exc)) from exc
            except (ValueError, KeyError, TypeError) as exc:
                raise HTTPException(502, "Некорректная структура карточки каталога.") from exc
            current = app.state.catalog.get(product_id)
        return current

    @app.get("/api/products/{product_id}/alternatives", response_model=AlternativesResult)
    def alternatives(product_id: int):
        if app.state.catalog.get(product_id) is None:
            raise HTTPException(404, "Товар отсутствует в загруженной выборке.")
        return app.state.catalog.alternatives(product_id)

    @app.get("/api/purchase-terms")
    def terms():
        return app.state.terms

    @app.post("/api/chat/sessions", status_code=201)
    def new_session(request: Request, response: Response):
        origin = request.headers.get("origin")
        if (
            origin
            and origin not in settings.cors_origins
            and origin != str(request.base_url).rstrip("/")
        ):
            raise HTTPException(403, "Origin не разрешён.")
        try:
            token = app.state.sessions.create()
        except OverflowError as exc:
            raise HTTPException(503, "Лимит сессий достигнут. Повторите позже.") from exc
        response.headers["Cache-Control"] = "no-store"
        if settings.demo_cart_enabled:
            response.set_cookie(
                COOKIE_NAME,
                token,
                httponly=True,
                samesite="lax",
                path="/cart",
                secure=settings.cart_cookie_secure or request.url.scheme == "https",
                max_age=settings.session_ttl_seconds,
            )
        return {"session_token": token, "expires_in": settings.session_ttl_seconds}

    def session_for(authorization: str | None):
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(401, "Создайте сессию и передайте её токен.")
        token = authorization.removeprefix("Bearer ")
        session = app.state.sessions.get(token)
        if session is None:
            raise HTTPException(401, "Сессия не найдена или истекла.")
        return token, session

    @app.delete("/api/chat/sessions/current", status_code=204)
    def delete_session(response: Response, authorization: str | None = Header(default=None)):
        token, _ = session_for(authorization)
        app.state.sessions.items.pop(token, None)
        response.delete_cookie(COOKIE_NAME, path="/cart")

    @app.post("/api/chat", response_model=ChatResponse)
    async def chat(body: ChatRequest, authorization: str | None = Header(default=None)):
        _, session = session_for(authorization)
        if session.lock.locked():
            raise HTTPException(409, "Предыдущий запрос этой сессии ещё обрабатывается.")
        async with session.lock:
            return await app.state.chat.reply(session, body.message)

    return app


app = create_app()
