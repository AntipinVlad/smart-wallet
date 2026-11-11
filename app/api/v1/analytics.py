from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, get_rate_loader
from app.api.errors import error_responses
from app.core.exceptions import (
    ExchangeRateNotFoundError,
    ExchangeServiceUnavailableError,
    InvalidPeriodError,
    NotAuthenticatedError,
    WalletNotFoundError,
)
from app.models import User
from app.schemas import (
    AnalyticsQuery,
    CategoriesQuery,
    CategoriesResponse,
    SummaryResponse,
    TimelineQuery,
    TimelineResponse,
)
from app.services import analytics as analytics_service
from app.services.exchange_rates import DailyRateLoader

router = APIRouter()

REPORT_ERRORS = error_responses(
    NotAuthenticatedError,
    WalletNotFoundError,
    InvalidPeriodError,
    ExchangeRateNotFoundError,
    ExchangeServiceUnavailableError,
    validation=True,
)


@router.get("/analytics/summary", response_model=SummaryResponse, responses=REPORT_ERRORS)
async def get_summary(
    query: Annotated[AnalyticsQuery, Query()],
    db: AsyncSession = Depends(get_db),
    rate_loader: DailyRateLoader = Depends(get_rate_loader),
    current_user: User = Depends(get_current_user),
) -> SummaryResponse:
    """Income and expenses of a period by category, converted at the exchange rate of each operation's day."""
    return await analytics_service.get_summary(db, rate_loader, current_user, query)


@router.get("/analytics/categories", response_model=CategoriesResponse, responses=REPORT_ERRORS)
async def get_categories(
    query: Annotated[CategoriesQuery, Query()],
    db: AsyncSession = Depends(get_db),
    rate_loader: DailyRateLoader = Depends(get_rate_loader),
    current_user: User = Depends(get_current_user),
) -> CategoriesResponse:
    """Expenses or income of a period and of the previous one by category, for a chart."""
    return await analytics_service.get_categories(db, rate_loader, current_user, query)


@router.get("/analytics/timeline", response_model=TimelineResponse, responses=REPORT_ERRORS)
async def get_timeline(
    query: Annotated[TimelineQuery, Query()],
    db: AsyncSession = Depends(get_db),
    rate_loader: DailyRateLoader = Depends(get_rate_loader),
    current_user: User = Depends(get_current_user),
) -> TimelineResponse:
    """Income and expenses of a period by day, week or month, for a chart."""
    return await analytics_service.get_timeline(db, rate_loader, current_user, query)
