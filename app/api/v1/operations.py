from datetime import datetime

import aiohttp
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import error_responses
from app.core.exceptions import (
    ExchangeRateNotFoundError,
    ExchangeServiceUnavailableError,
    InsufficientFundsError,
    NotAuthenticatedError,
    WalletNotFoundError,
)
from app.dependency import get_db, get_current_user, get_http_session
from app.models import User
from app.schemas import OperationRequest, OperationResponse, TransferCreateSchema
from app.services import operation as operation_service

router = APIRouter()


@router.post("/operations/income", response_model=OperationResponse,
             responses=error_responses(NotAuthenticatedError, WalletNotFoundError, validation=True))
async def add_income(operation: OperationRequest, db: AsyncSession = Depends(get_db),
                     current_user: User = Depends(get_current_user)):
    return await operation_service.add_income(db, current_user, operation)


@router.post("/operations/expense", response_model=OperationResponse,
             responses=error_responses(NotAuthenticatedError, WalletNotFoundError, InsufficientFundsError,
                                       validation=True))
async def add_expense(operation: OperationRequest, db: AsyncSession = Depends(get_db),
                      current_user: User = Depends(get_current_user)):
    return await operation_service.add_expense(db, current_user, operation)


@router.get("/operations", response_model=list[OperationResponse],
            responses=error_responses(NotAuthenticatedError, WalletNotFoundError, validation=True))
async def get_operations(
        wallet_id: int | None = Query(None),
        date_from: datetime | None = Query(None),
        date_to: datetime | None = Query(None),
        db: AsyncSession = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    return await operation_service.get_operations_list(db, current_user, wallet_id, date_from, date_to)


@router.post("/operations/transfers", response_model=list[OperationResponse],
             responses=error_responses(NotAuthenticatedError, WalletNotFoundError, InsufficientFundsError,
                                       ExchangeRateNotFoundError, ExchangeServiceUnavailableError,
                                       validation=True))
async def create_transfer(
        payload: TransferCreateSchema,
        db: AsyncSession = Depends(get_db),
        http_session: aiohttp.ClientSession = Depends(get_http_session),
        current_user: User = Depends(get_current_user)
):
    return await operation_service.transfer_between_wallets(
        db, http_session, current_user.id,
        payload.from_wallet_id,
        payload.to_wallet_id,
        payload.amount
    )
