import logging
from collections.abc import Mapping
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.exceptions import (
    AppError,
    AuthenticationError,
    ConflictError,
    ErrorCode,
    ExternalServiceError,
    ExternalServiceUnavailableError,
    NotFoundError,
)
from app.schemas import ErrorResponse

logger = logging.getLogger(__name__)


STATUS_BY_ERROR_GROUP: dict[type[AppError], int] = {
    NotFoundError: 404,
    ConflictError: 409,
    AuthenticationError: 401,
    ExternalServiceError: 502,
    ExternalServiceUnavailableError: 503,
}


def status_code_for(error_type: type[AppError]) -> int:
    for group, status_code in STATUS_BY_ERROR_GROUP.items():
        if issubclass(error_type, group):
            return status_code
    raise TypeError(f"{error_type.__name__} does not belong to any error group")


def error_response(
    status_code: int,
    code: ErrorCode,
    message: str,
    details: list[dict[str, Any]] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    error: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return JSONResponse(status_code=status_code, content={"error": error}, headers=headers)


async def handle_app_error(_: Request, error: AppError) -> JSONResponse:
    return error_response(status_code_for(type(error)), error.code, error.message)


async def handle_http_exception(_: Request, error: StarletteHTTPException) -> JSONResponse:
    status = HTTPStatus(error.status_code)
    code = ErrorCode(status.name) if status.name in ErrorCode.__members__ else ErrorCode.HTTP_ERROR
    message = error.detail if isinstance(error.detail, str) else status.phrase
    return error_response(error.status_code, code, message, headers=error.headers)


async def handle_validation_error(_: Request, error: RequestValidationError) -> JSONResponse:
    # Without the input values: they can contain a password
    details = [
        {"field": ".".join(str(part) for part in item["loc"]), "message": item["msg"]} for item in error.errors()
    ]
    return error_response(422, ErrorCode.VALIDATION_ERROR, "Request validation failed", details)


def unexpected_error_response(method: str, path: str, error: Exception) -> JSONResponse:
    # No details for the client: they can contain SQL, file paths or data of other users
    logger.error("Unhandled error on %s %s", method, path, exc_info=error)
    return error_response(500, ErrorCode.INTERNAL_ERROR, "Internal server error")


def register_exception_handlers(app: FastAPI) -> None:
    # Starlette types a handler as taking any Exception, a handler for one type does not match it
    app.add_exception_handler(AppError, handle_app_error)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, handle_http_exception)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, handle_validation_error)  # type: ignore[arg-type]
    # No Exception handler: RequestContextMiddleware turns unhandled errors into a 500


def _documented_response(status_code: int, codes: list[ErrorCode]) -> dict[str, Any]:
    return {"model": ErrorResponse, "description": f"{HTTPStatus(status_code).phrase}: {', '.join(codes)}"}


def error_responses(*error_types: type[AppError], validation: bool = False) -> dict[int | str, dict[str, Any]]:
    # validation=True replaces FastAPI's default 422 description, which has another body format
    codes_by_status: dict[int, list[ErrorCode]] = {}
    for error_type in error_types:
        codes_by_status.setdefault(status_code_for(error_type), []).append(error_type.code)
    if validation:
        codes_by_status[422] = [ErrorCode.VALIDATION_ERROR]
    return {
        status_code: _documented_response(status_code, codes) for status_code, codes in sorted(codes_by_status.items())
    }


INTERNAL_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    500: _documented_response(500, [ErrorCode.INTERNAL_ERROR]),
}
