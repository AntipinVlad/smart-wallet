import tomllib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiohttp
from fastapi import FastAPI
from openai import AsyncOpenAI
from redis.asyncio import Redis

from app.api.deps import AppState
from app.api.errors import INTERNAL_ERROR_RESPONSES, register_exception_handlers
from app.api.health import router as health_router
from app.api.middleware import RequestContextMiddleware
from app.api.v1.auth import router as auth_router
from app.api.v1.operations import router as operation_router
from app.api.v1.users import router as users_router
from app.api.v1.wallets import router as wallet_router
from app.core.config import settings
from app.core.logging import setup_logging
from app.database import engine
from app.worker import broker as task_broker

# Only in pyproject.toml, bumped with: uv version --bump patch|minor|major
VERSION = tomllib.loads((Path(__file__).parent.parent / "pyproject.toml").read_text(encoding="utf-8"))["project"][
    "version"
]

# Before anything logs
setup_logging(settings.log_level, settings.log_format)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[AppState]:
    # One session for the app: connections stay open between requests
    timeout = aiohttp.ClientTimeout(total=settings.exchange_api_timeout_seconds)
    # Connects on the first command: the app starts even when Redis is down
    redis = Redis.from_url(
        settings.redis_url,
        socket_timeout=settings.redis_timeout_seconds,
        socket_connect_timeout=settings.redis_timeout_seconds,
    )
    openai_client = AsyncOpenAI(
        api_key=settings.openai_api_key.get_secret_value(), timeout=settings.openai_timeout_seconds
    )
    async with aiohttp.ClientSession(timeout=timeout) as http_session, redis, openai_client:
        yield AppState(http_session=http_session, redis=redis, openai=openai_client)
    # Database and queue connections belong to this event loop, so they are closed before it stops
    await engine.dispose()
    # Only the connection pool: startup() is not called, it would make the app fail to start without Redis
    await task_broker.shutdown()


app = FastAPI(title="Smart Wallet", version=VERSION, lifespan=lifespan, responses=INTERNAL_ERROR_RESPONSES)
register_exception_handlers(app)
app.add_middleware(RequestContextMiddleware)

app.include_router(health_router)
app.include_router(auth_router, prefix="/api/v1", tags=["auth"])
app.include_router(users_router, prefix="/api/v1", tags=["users"])
app.include_router(wallet_router, prefix="/api/v1", tags=["wallet"])
app.include_router(operation_router, prefix="/api/v1", tags=["operation"])
