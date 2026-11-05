from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.api.errors import error_responses
from app.core.exceptions import DatabaseUnavailableError
from app.schemas import HealthResponse
from app.services import health as health_service

# Outside /api/v1: used by Docker, Kubernetes and load balancers, not by clients
router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live", response_model=HealthResponse)
async def live() -> HealthResponse:
    """The process is running. The database is not checked: a slow database must not restart the app."""
    return HealthResponse(status="ok")


@router.get("/ready", response_model=HealthResponse, responses=error_responses(DatabaseUnavailableError))
async def ready(db: AsyncSession = Depends(get_db)) -> HealthResponse:
    """The app can handle requests: the database answers."""
    await health_service.check_database(db)
    return HealthResponse(status="ok")
