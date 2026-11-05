import aiohttp
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import error_responses
from app.core.exceptions import (
    ExchangeRateNotFoundError,
    ExchangeServiceUnavailableError,
    NotAuthenticatedError,
    WalletAlreadyExistsError,
)
from app.dependency import get_db, get_current_user, get_http_session
from app.models import User
from app.schemas import CreateWalletRequest, WalletResponse, TotalBalance
from app.services import wallets as wallets_service

router = APIRouter()


@router.get("/balance", response_model=TotalBalance,
            responses=error_responses(NotAuthenticatedError, ExchangeRateNotFoundError,
                                      ExchangeServiceUnavailableError))
async def get_balance(db: AsyncSession = Depends(get_db),
                      http_session: aiohttp.ClientSession = Depends(get_http_session),
                      current_user: User = Depends(get_current_user)):
    return await wallets_service.get_total_balance(db, http_session, current_user)


@router.post("/wallets", response_model=WalletResponse, status_code=201,
             responses=error_responses(NotAuthenticatedError, WalletAlreadyExistsError, validation=True))
async def create_wallet(wallet: CreateWalletRequest, db: AsyncSession = Depends(get_db),
                        current_user: User = Depends(get_current_user)):
    return await wallets_service.create_wallet(db, current_user, wallet)


@router.get("/wallets", response_model=list[WalletResponse], responses=error_responses(NotAuthenticatedError))
async def get_wallets(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    return await wallets_service.get_all_wallets(db, current_user)
