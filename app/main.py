import tomllib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from app.api.deps import AppState
from app.api.errors import INTERNAL_ERROR_RESPONSES, register_exception_handlers
from app.api.health import router as health_router
from app.api.middleware import RequestContextMiddleware
from app.api.v1.analytics import router as analytics_router
from app.api.v1.auth import router as auth_router
from app.api.v1.operations import router as operation_router
from app.api.v1.users import router as users_router
from app.api.v1.wallets import router as wallet_router
from app.core.clients import create_http_session, create_openai_client, create_redis
from app.core.config import settings
from app.core.logging import setup_logging
from app.database import engine
from app.services.exchange_rates import DailyRateLoader
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
    async with create_http_session() as http_session, create_redis() as redis, create_openai_client() as openai_client:
        # One loader for the process: requests share its loads of exchange rates
        rate_loader = DailyRateLoader(redis, http_session)
        yield AppState(http_session=http_session, redis=redis, openai=openai_client, rate_loader=rate_loader)
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
app.include_router(analytics_router, prefix="/api/v1", tags=["analytics"])
