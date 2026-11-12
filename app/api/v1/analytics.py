from typing import Annotated

from fastapi import APIRouter, Depends, Query
from openai import AsyncOpenAI
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, get_openai_client, get_rate_loader, get_redis
from app.api.errors import error_responses
from app.core.config import settings
from app.core.exceptions import (
    AiInvalidResponseError,
    AiServiceUnavailableError,
    ExchangeRateNotFoundError,
    ExchangeServiceUnavailableError,
    InvalidPeriodError,
    NotAuthenticatedError,
    RateLimitExceededError,
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
    TipsResponse,
)
from app.services import analytics as analytics_service
from app.services import rate_limit
from app.services import tips as tips_service
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


async def limit_tips_requests(
    redis: Redis = Depends(get_redis), current_user: User = Depends(get_current_user)
) -> None:
    limits = [rate_limit.Limit("tips_user", str(current_user.id), settings.tips_rate_limit_per_user)]
    await rate_limit.hit(redis, "tips", limits, settings.tips_rate_limit_window_seconds)


@router.get(
    "/analytics/tips",
    response_model=TipsResponse,
    dependencies=[Depends(limit_tips_requests)],
    responses=error_responses(
        NotAuthenticatedError,
        WalletNotFoundError,
        InvalidPeriodError,
        RateLimitExceededError,
        AiInvalidResponseError,
        ExchangeRateNotFoundError,
        AiServiceUnavailableError,
        ExchangeServiceUnavailableError,
        validation=True,
    ),
)
async def get_tips(
    query: Annotated[AnalyticsQuery, Query()],
    db: AsyncSession = Depends(get_db),
    rate_loader: DailyRateLoader = Depends(get_rate_loader),
    redis: Redis = Depends(get_redis),
    openai_client: AsyncOpenAI = Depends(get_openai_client),
    current_user: User = Depends(get_current_user),
) -> TipsResponse:
    """AI tips about the summary of a period, in the language of the user. Every number is checked against it."""
    return await tips_service.get_tips(db, rate_loader, redis, openai_client, current_user, query)
