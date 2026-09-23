import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, Query
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.ai import Interpreter
from app.catalog import Catalog
from app.chat import ChatService, Sessions
from app.config import Settings
from app.ekt import CatalogUnavailable, EktClient
from app.errors import ApiError, http_error, validation_error
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
        app.state.terms = load_terms(settings.purchase_terms_path)
        app.state.chat = ChatService(app.state.catalog, app.state.interpreter, app.state.terms)
        try:
            yield
        finally:
            await app.state.interpreter.close()
            await app.state.ekt.close()

    app = FastAPI(title="EKT Assistant — Backend", version="0.2.0", lifespan=lifespan)
    app.add_exception_handler(StarletteHTTPException, http_error)
    app.add_exception_handler(RequestValidationError, validation_error)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.get("/health")
    def health():
        return {
            "status": "ok",
            "catalog_count": app.state.catalog.count(),
            "ai_configured": settings.ai_configured,
            "live_catalog_enabled": settings.ekt_live_enabled,
            "cart_enabled": False,
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
            raise ApiError(404, "PRODUCT_NOT_FOUND", "Товар отсутствует в загруженной выборке.")
        if refresh:
            if not settings.ekt_live_enabled:
                raise ApiError(503, "CATALOG_DISABLED", "Проверка живого каталога отключена.")
            try:
                raw, observed_at = await app.state.ekt.detail(product_id)
                app.state.catalog.upsert(raw, observed_at)
            except CatalogUnavailable as exc:
                raise ApiError(503, "CATALOG_UNAVAILABLE", str(exc), retryable=True) from exc
            except (ValueError, KeyError, TypeError) as exc:
                raise ApiError(
                    502, "INVALID_CATALOG_RESPONSE", "Некорректная структура карточки каталога."
                ) from exc
            current = app.state.catalog.get(product_id)
        return current

    @app.get("/api/products/{product_id}/alternatives", response_model=AlternativesResult)
    def alternatives(product_id: int):
        if app.state.catalog.get(product_id) is None:
            raise ApiError(404, "PRODUCT_NOT_FOUND", "Товар отсутствует в загруженной выборке.")
        return app.state.catalog.alternatives(product_id)

    @app.get("/api/purchase-terms")
    def terms():
        return app.state.terms

    @app.post("/api/chat/sessions", status_code=201)
    async def new_session():
        try:
            token = app.state.sessions.create()
        except OverflowError as exc:
            raise ApiError(
                503, "SESSION_CAPACITY", "Лимит сессий достигнут. Повторите позже.", retryable=True
            ) from exc
        return {"session_token": token, "expires_in": settings.session_ttl_seconds}

    def session_for(authorization: str | None):
        if not authorization or not authorization.startswith("Bearer "):
            raise ApiError(401, "SESSION_REQUIRED", "Создайте сессию и передайте её токен.")
        token = authorization.removeprefix("Bearer ")
        session = app.state.sessions.get(token)
        if session is None:
            raise ApiError(401, "SESSION_EXPIRED", "Сессия не найдена или истекла.")
        return token, session

    @app.delete("/api/chat/sessions/current", status_code=204)
    async def delete_session(authorization: str | None = Header(default=None)):
        token, _ = session_for(authorization)
        app.state.sessions.items.pop(token, None)

    @app.post("/api/chat", response_model=ChatResponse)
    async def chat(body: ChatRequest, authorization: str | None = Header(default=None)):
        token, session = session_for(authorization)
        if body.request_id in session.replies:
            original_message, response = session.replies[body.request_id]
            if original_message != body.message:
                raise ApiError(
                    409,
                    "REQUEST_ID_REUSED",
                    "Этот request_id уже использован для другого сообщения.",
                )
            return response
        if session.lock.locked():
            raise ApiError(
                409,
                "REQUEST_IN_PROGRESS",
                "Предыдущий запрос этой сессии ещё обрабатывается.",
                retryable=True,
            )
        now = time.monotonic()
        session.requests = [timestamp for timestamp in session.requests if now - timestamp < 60]
        if len(session.requests) >= settings.chat_requests_per_minute:
            raise ApiError(
                429,
                "RATE_LIMITED",
                "Слишком много запросов. Повторите через минуту.",
                retryable=True,
            )
        session.requests.append(now)
        async with session.lock:
            response = await app.state.chat.reply(session, body.message)
            if app.state.sessions.get(token) is not session:
                raise ApiError(
                    401, "SESSION_EXPIRED", "Сессия завершена во время обработки запроса."
                )
            if body.request_id is not None:
                session.replies[body.request_id] = (body.message, response)
                if len(session.replies) > 20:
                    session.replies.popitem(last=False)
            return response

    return app


app = create_app()
