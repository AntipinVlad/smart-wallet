import logging
import re
import time
from uuid import uuid4

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.errors import unexpected_error_response
from app.core.logging import request_id_var

access_logger = logging.getLogger("app.access")
logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"
# A client id is kept only if it is safe to write into logs and headers
_VALID_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")
# Probes come every few seconds and would bury the real requests
_NOT_LOGGED_PATH_PREFIX = "/health"


class RequestContextMiddleware:
    # Plain ASGI instead of @app.middleware("http"): it does not wrap the response body, streaming works the same

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = self._request_id(scope)
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        status = 500
        response_started = False

        async def send_with_request_id(message: Message) -> None:
            nonlocal status, response_started
            if message["type"] == "http.response.start":
                status = message["status"]
                response_started = True
                MutableHeaders(scope=message).append(REQUEST_ID_HEADER, request_id)
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception as error:
            # Not re-raised: uvicorn would log it a second time
            if response_started:
                # Part of the response is already sent, a 500 cannot replace it
                logger.exception("Error after the response started on %s %s", scope["method"], scope["path"])
                raise
            response = unexpected_error_response(scope["method"], scope["path"], error)
            await response(scope, receive, send_with_request_id)
        finally:
            if not scope["path"].startswith(_NOT_LOGGED_PATH_PREFIX):
                duration_ms = round((time.perf_counter() - started) * 1000, 1)
                access_logger.info(
                    "%s %s %s %.1fms",
                    scope["method"],
                    scope["path"],
                    status,
                    duration_ms,
                    extra={
                        "method": scope["method"],
                        "path": scope["path"],
                        "status": status,
                        "duration_ms": duration_ms,
                    },
                )
            request_id_var.reset(token)

    @staticmethod
    def _request_id(scope: Scope) -> str:
        headers: list[tuple[bytes, bytes]] = scope["headers"]
        for name, value in headers:
            if name.decode("latin-1").lower() == REQUEST_ID_HEADER.lower():
                candidate = value.decode("latin-1")
                if _VALID_REQUEST_ID.fullmatch(candidate):
                    return candidate
        return uuid4().hex
