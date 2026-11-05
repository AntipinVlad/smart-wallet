from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.api.errors import error_responses
from app.core.exceptions import DatabaseUnavailableError
from app.schemas import HealthResponse
from app.services import health as health_service

# Not under /api/v1: these are for Docker, Kubernetes and load balancers, not for clients, and do not change
# with API versions
router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live", response_model=HealthResponse)
async def live() -> HealthResponse:
    """The process is running and answers. Does not check the database on purpose."""
    return HealthResponse(status="ok")


@router.get("/ready", response_model=HealthResponse, responses=error_responses(DatabaseUnavailableError))
async def ready(db: AsyncSession = Depends(get_db)) -> HealthResponse:
    """The app can handle requests: the database answers. Until then traffic should not be sent here."""
    await health_service.check_database(db)
    return HealthResponse(status="ok")
