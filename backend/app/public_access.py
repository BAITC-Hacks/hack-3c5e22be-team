"""Process-wide guard for a small, single-worker public demonstration.

The browser authenticates its first navigation with HTTP Basic. A separate,
signed HttpOnly cookie then gates requests without consuming the Authorization
header used by the application's existing Bearer session contract. Budgets and
the cookie signing key intentionally reset when this one process restarts.
"""

import base64
import binascii
import hashlib
import hmac
import math
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field

from fastapi import FastAPI
from pydantic import SecretStr
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

GATE_COOKIE = "__Host-ekt_demo_access"


def _secret(value: str | SecretStr) -> str:
    return value.get_secret_value() if isinstance(value, SecretStr) else str(value)


@dataclass(frozen=True)
class PublicAccessConfig:
    username: bytes = field(repr=False)
    password: bytes = field(repr=False)
    requests_per_minute: int
    max_requests: int
    cookie_ttl_seconds: int
    max_body_bytes: int

    @classmethod
    def from_settings(cls, settings) -> "PublicAccessConfig":
        username = _secret(settings.public_demo_username)
        password = _secret(settings.public_demo_password)
        if not username.strip() or not password.strip() or ":" in username:
            raise ValueError("PUBLIC_DEMO requires nonempty, valid Basic Auth credentials.")
        if not settings.public_demo_cookie_secure:
            raise ValueError("PUBLIC_DEMO requires secure cookies and HTTPS at the public origin.")
        limits = (
            settings.public_demo_requests_per_minute,
            settings.public_demo_max_requests,
            settings.public_demo_cookie_ttl_seconds,
            settings.public_demo_max_body_bytes,
        )
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 1 for value in limits
        ):
            raise ValueError(
                "PUBLIC_DEMO requires positive request, budget, cookie and body limits."
            )
        return cls(username.encode("utf-8"), password.encode("utf-8"), *limits)


class PublicAccessMiddleware:
    def __init__(self, app: ASGIApp, config: PublicAccessConfig):
        self.app = app
        self.config = config
        self.signing_key = secrets.token_bytes(32)
        self.requests: deque[float] = deque()
        self.total_requests = 0
        self.lock = threading.Lock()

    def _cookie(self) -> str:
        expires = int(time.time()) + self.config.cookie_ttl_seconds
        payload = f"{expires}.{secrets.token_urlsafe(16)}"
        signature = hmac.new(self.signing_key, payload.encode(), hashlib.sha256).hexdigest()
        return f"{payload}.{signature}"

    def _valid_cookie(self, value: str | None) -> bool:
        if not value or len(value) > 256:
            return False
        try:
            expires, nonce, signature = value.split(".")
            expected = hmac.new(
                self.signing_key, f"{expires}.{nonce}".encode(), hashlib.sha256
            ).hexdigest()
            valid_signature = hmac.compare_digest(signature.encode(), expected.encode())
            return valid_signature and int(expires) > time.time()
        except (ValueError, UnicodeError):
            return False

    def _valid_basic(self, value: str) -> bool:
        scheme, _, encoded = value.partition(" ")
        if scheme.lower() != "basic" or not encoded or len(encoded) > 4096:
            return False
        try:
            username, password = base64.b64decode(encoded, validate=True).split(b":", 1)
        except (ValueError, binascii.Error):
            return False
        username_ok = hmac.compare_digest(username, self.config.username)
        password_ok = hmac.compare_digest(password, self.config.password)
        return username_ok and password_ok

    def _admit(self) -> Response | None:
        now = time.monotonic()
        with self.lock:
            while self.requests and now - self.requests[0] >= 60:
                self.requests.popleft()
            if self.total_requests >= self.config.max_requests:
                return _error(
                    429,
                    "DEMO_BUDGET_EXHAUSTED",
                    "Общий лимит запросов демонстрации исчерпан. Обратитесь к организатору.",
                )
            if len(self.requests) >= self.config.requests_per_minute:
                retry_after = max(1, math.ceil(60 - (now - self.requests[0])))
                return _error(
                    429,
                    "DEMO_RATE_LIMITED",
                    "Общий лимит запросов в минуту достигнут. Повторите позже.",
                    retryable=True,
                    headers={"Retry-After": str(retry_after)},
                )
            self.requests.append(now)
            self.total_requests += 1
        return None

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        path = scope["path"]
        cookie_ok = self._valid_cookie(request.cookies.get(GATE_COOKIE))
        basic_ok = (
            False if cookie_ok else self._valid_basic(request.headers.get("authorization", ""))
        )
        if path == "/health" and scope["method"] in {"GET", "HEAD"} and not (cookie_ok or basic_ok):
            await JSONResponse({"status": "ok"}, headers={"Cache-Control": "no-store"})(
                scope, receive, send
            )
            return

        if not cookie_ok and not basic_ok:
            await _error(
                401,
                "DEMO_ACCESS_REQUIRED",
                "Для доступа к демонстрации выполните вход.",
                headers={"WWW-Authenticate": 'Basic realm="EKT public demo", charset="UTF-8"'},
            )(scope, receive, send)
            return

        cookie_headers = []
        if basic_ok:
            response = Response()
            response.set_cookie(
                GATE_COOKIE,
                self._cookie(),
                max_age=self.config.cookie_ttl_seconds,
                path="/",
                secure=True,
                httponly=True,
                samesite="lax",
            )
            cookie_headers = [
                (key, value) for key, value in response.raw_headers if key == b"set-cookie"
            ]

        async def guarded_send(message: Message):
            if message["type"] == "http.response.start":
                message = {
                    **message,
                    "headers": [
                        (key, value)
                        for key, value in message.get("headers", [])
                        if key.lower() != b"cache-control"
                    ]
                    + [(b"cache-control", b"no-store")]
                    + cookie_headers,
                }
            await send(message)

        if path == "/demo-login" and scope["method"] in {"GET", "HEAD"}:
            await RedirectResponse("/", status_code=303)(scope, receive, guarded_send)
            return

        protected_operation = path in {"/api", "/cart"} or path.startswith(("/api/", "/cart/"))
        if protected_operation:
            denied = self._admit()
            if denied is not None:
                await denied(scope, receive, guarded_send)
                return

            # Bound actual bytes, including bodies without a Content-Length header.
            # Do this before application execution so oversize input has no side effects.
            raw_length = request.headers.get("content-length")
            if raw_length is not None:
                try:
                    length = int(raw_length)
                    if length < 0:
                        raise ValueError
                except ValueError:
                    await _error(400, "INVALID_CONTENT_LENGTH", "Некорректный размер запроса.")(
                        scope, receive, guarded_send
                    )
                    return
                if length > self.config.max_body_bytes:
                    await _body_too_large()(scope, receive, guarded_send)
                    return
            body = bytearray()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                chunk = message.get("body", b"")
                if len(body) + len(chunk) > self.config.max_body_bytes:
                    await _body_too_large()(scope, receive, guarded_send)
                    return
                body.extend(chunk)
                if not message.get("more_body", False):
                    break
            delivered = False

            async def buffered_receive():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await receive()

            await self.app(scope, buffered_receive, guarded_send)
            return

        await self.app(scope, receive, guarded_send)


def _error(
    status: int,
    code: str,
    message: str,
    retryable: bool = False,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "detail": message,
            "error": {"code": code, "message": message, "retryable": retryable},
        },
        headers={"Cache-Control": "no-store", **(headers or {})},
    )


def _body_too_large() -> JSONResponse:
    return _error(413, "REQUEST_TOO_LARGE", "Превышен допустимый размер запроса.")


def install_public_access(app: FastAPI, settings) -> None:
    """Call after other middleware registration; validates public settings eagerly."""
    if not getattr(settings, "public_demo", False):
        return
    app.add_middleware(PublicAccessMiddleware, config=PublicAccessConfig.from_settings(settings))
