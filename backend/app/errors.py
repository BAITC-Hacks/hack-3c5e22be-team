from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class ApiError(HTTPException):
    def __init__(self, status: int, code: str, message: str, retryable: bool = False):
        super().__init__(status, message)
        self.code, self.retryable = code, retryable


async def http_error(request: Request, exc: HTTPException):
    code = getattr(exc, "code", f"HTTP_{exc.status_code}")
    return JSONResponse(
        status_code=exc.status_code,
        headers=exc.headers,
        content={
            "detail": exc.detail,
            "error": {
                "code": code,
                "message": str(exc.detail),
                "retryable": getattr(exc, "retryable", False),
            },
        },
    )


async def validation_error(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "detail": [{"loc": e["loc"], "msg": e["msg"], "type": e["type"]} for e in exc.errors()],
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Проверьте формат запроса.",
                "retryable": False,
            },
        },
    )
