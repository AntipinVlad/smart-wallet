from typing import Annotated

import aiohttp
from fastapi import APIRouter, Depends, Query
from openai import AsyncOpenAI
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.parsing import service as parsing_service
from app.api.deps import get_current_user, get_db, get_http_session, get_openai_client, get_redis
from app.api.errors import error_responses
from app.core.config import settings
from app.core.exceptions import (
    AiInvalidResponseError,
    AiServiceUnavailableError,
    ExchangeRateNotFoundError,
    ExchangeServiceUnavailableError,
    InsufficientFundsError,
    NotAuthenticatedError,
    OperationNotRecognizedError,
    RateLimitExceededError,
    WalletNotFoundError,
)
from app.models import User
from app.schemas import (
    Draft,
    OperationListQuery,
    OperationRequest,
    OperationResponse,
    Page,
    ParseRequest,
    TransferRequest,
)
from app.services import operation as operation_service
from app.services import rate_limit

router = APIRouter()


async def limit_parse_requests(
    redis: Redis = Depends(get_redis), current_user: User = Depends(get_current_user)
) -> None:
    limits = [rate_limit.Limit("parse_user", str(current_user.id), settings.parse_rate_limit_per_user)]
    await rate_limit.hit(redis, "parse", limits, settings.parse_rate_limit_window_seconds)


@router.post(
    "/operations/income",
    response_model=OperationResponse,
    status_code=201,
    responses=error_responses(NotAuthenticatedError, WalletNotFoundError, validation=True),
)
async def add_income(
    operation: OperationRequest, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
) -> OperationResponse:
    return await operation_service.add_income(db, current_user, operation)


@router.post(
    "/operations/expense",
    response_model=OperationResponse,
    status_code=201,
    responses=error_responses(NotAuthenticatedError, WalletNotFoundError, InsufficientFundsError, validation=True),
)
async def add_expense(
    operation: OperationRequest, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
) -> OperationResponse:
    return await operation_service.add_expense(db, current_user, operation)


@router.get(
    "/operations",
    response_model=Page[OperationResponse],
    responses=error_responses(NotAuthenticatedError, WalletNotFoundError, validation=True),
)
async def get_operations(
    query: Annotated[OperationListQuery, Query()],
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Page[OperationResponse]:
    return await operation_service.get_operations_page(db, current_user, query)


@router.post(
    "/operations/transfers",
    response_model=list[OperationResponse],
    status_code=201,
    responses=error_responses(
        NotAuthenticatedError,
        WalletNotFoundError,
        InsufficientFundsError,
        ExchangeRateNotFoundError,
        ExchangeServiceUnavailableError,
        validation=True,
    ),
)
async def create_transfer(
    payload: TransferRequest,
    db: AsyncSession = Depends(get_db),
    http_session: aiohttp.ClientSession = Depends(get_http_session),
    current_user: User = Depends(get_current_user),
) -> list[OperationResponse]:
    return await operation_service.transfer_between_wallets(db, http_session, current_user, payload)


@router.post(
    "/operations/parse",
    response_model=list[Draft],
    dependencies=[Depends(limit_parse_requests)],
    responses=error_responses(
        NotAuthenticatedError,
        OperationNotRecognizedError,
        RateLimitExceededError,
        AiInvalidResponseError,
        ExchangeRateNotFoundError,
        AiServiceUnavailableError,
        ExchangeServiceUnavailableError,
        validation=True,
    ),
)
async def parse_operations(
    payload: ParseRequest,
    db: AsyncSession = Depends(get_db),
    http_session: aiohttp.ClientSession = Depends(get_http_session),
    openai_client: AsyncOpenAI = Depends(get_openai_client),
    current_user: User = Depends(get_current_user),
) -> list[Draft]:
    """Turns a text into operation drafts. Nothing is saved: the client confirms each draft."""
    return await parsing_service.parse_operations(db, openai_client, http_session, current_user, payload.text)
