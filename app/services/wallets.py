import asyncio
import logging
from decimal import Decimal

import aiohttp
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import WalletAlreadyExistsError
from app.enums import CurrencyEnum
from app.models import User
from app.repository import wallets as wallet_repository
from app.schemas import CreateWalletRequest, TotalBalance, WalletResponse
from app.services import exchange_service

logger = logging.getLogger(__name__)


async def get_total_balance(
    db: AsyncSession, http_session: aiohttp.ClientSession, current_user: User, currency: CurrencyEnum
) -> TotalBalance:
    wallets = await wallet_repository.get_all_wallets(db, current_user.id)
    others = list({wallet.currency for wallet in wallets} - {currency})
    # TaskGroup would raise ExceptionGroup, which the error handlers do not know
    fetched = await asyncio.gather(
        *(exchange_service.get_exchange_rate(http_session, other, currency) for other in others)
    )
    rates = {currency: Decimal(1), **dict(zip(others, fetched, strict=True))}

    total_balance = sum((round(wallet.balance * rates[wallet.currency], 2) for wallet in wallets), Decimal(0))
    return TotalBalance(total_balance=total_balance, currency=currency)


async def create_wallet(db: AsyncSession, current_user: User, wallet: CreateWalletRequest) -> WalletResponse:
    # A failed insert expires current_user, and async code cannot reload it
    user_id = current_user.id
    if await wallet_repository.is_wallet_exist(db, user_id, wallet.name):
        raise WalletAlreadyExistsError(wallet.name)

    try:
        created = await wallet_repository.create_wallet(
            db, user_id, wallet.name, wallet.initial_balance, wallet.currency
        )
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        logger.warning("Wallet '%s' for user %s was created by a concurrent request", wallet.name, user_id)
        raise WalletAlreadyExistsError(wallet.name) from e

    return WalletResponse.model_validate(created)


async def get_all_wallets(db: AsyncSession, current_user: User) -> list[WalletResponse]:
    wallets = await wallet_repository.get_all_wallets(db, current_user.id)
    return [WalletResponse.model_validate(wallet) for wallet in wallets]
