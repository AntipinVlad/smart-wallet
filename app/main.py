from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aiohttp
from fastapi import FastAPI

from app.api.errors import INTERNAL_ERROR_RESPONSES, register_exception_handlers
from app.api.health import router as health_router
from app.api.middleware import RequestContextMiddleware
from app.api.v1.auth import router as auth_router
from app.api.v1.wallets import router as wallet_router
from app.api.v1.operations import router as operation_router
from app.api.v1.users import router as users_router
from app.core.config import settings
from app.core.logging import setup_logging
from app.database import engine
from app.api.deps import AppState

# Before anything logs. Also replaces the logging setup of uvicorn, so all logs have one format
setup_logging(settings.log_level, settings.log_format)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[AppState]:
    # One HTTP session for the whole app: it keeps connections to external APIs open between requests
    timeout = aiohttp.ClientTimeout(total=settings.exchange_api_timeout_seconds)
    async with aiohttp.ClientSession(timeout=timeout) as http_session:
        yield AppState(http_session=http_session)
    # Database connections belong to the event loop that opened them, so they are closed together with it
    await engine.dispose()


app = FastAPI(lifespan=lifespan, responses=INTERNAL_ERROR_RESPONSES)
register_exception_handlers(app)
app.add_middleware(RequestContextMiddleware)

app.include_router(health_router)
app.include_router(auth_router, prefix="/api/v1", tags=["auth"])
app.include_router(users_router, prefix="/api/v1", tags=["users"])
app.include_router(wallet_router, prefix="/api/v1", tags=["wallet"])
app.include_router(operation_router, prefix="/api/v1", tags=["operation"])
