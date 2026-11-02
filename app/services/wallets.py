import logging
from decimal import Decimal

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.enum import CurrencyEnum
from app.models import User, Wallet
from app.repository import wallets as wallet_repository
from app.schemas import CreateWalletRequest, WalletResponse, TotalBalance
from app.services import exchange_service

logger = logging.getLogger(__name__)


async def get_total_balance(db: Session, current_user: User) -> TotalBalance:
    wallets = wallet_repository.get_all_wallets(db, current_user.id)
    total_balance = Decimal(0)

    for wallet in wallets:
        if wallet.currency == CurrencyEnum.UAH:
            total_balance += wallet.balance
        else:
            exchange_rate = await exchange_service.get_exchange_rate(wallet.currency, CurrencyEnum.UAH)
            total_balance += round((wallet.balance * exchange_rate), 2)

    return TotalBalance(total_balance=total_balance)


def create_wallet(db: Session, current_user: User, wallet: CreateWalletRequest) -> WalletResponse:
    if wallet_repository.is_wallet_exist(db, current_user.id, wallet.name):
        raise HTTPException(status_code=400, detail=f"Wallet '{wallet.name}' already exists")

    try:
        wallet = wallet_repository.create_wallet(db, current_user.id, wallet.name, wallet.initial_balance,
                                                 wallet.currency)
        db.commit()
    except IntegrityError as e:
        db.rollback()
        logger.warning("Wallet '%s' for user %s was created by a concurrent request", wallet.name, current_user.id)
        raise HTTPException(status_code=400, detail=f"Wallet '{wallet.name}' already exists") from e

    return WalletResponse.model_validate(wallet)


def get_all_wallets(db: Session, current_user: User) -> list[WalletResponse]:
    wallets = wallet_repository.get_all_wallets(db, current_user.id)
    return [WalletResponse.model_validate(wallet) for wallet in wallets]
