from datetime import datetime
from decimal import Decimal
from uuid import uuid4

import aiohttp
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import InsufficientFundsError, WalletNotFoundError
from app.enums import OperationType
from app.models import User
from app.repository import operations as operations_repository
from app.repository import wallets as wallet_repository
from app.schemas import OperationRequest, OperationResponse
from app.services.exchange_service import get_exchange_rate


async def add_income(db: AsyncSession, current_user: User, operation: OperationRequest) -> OperationResponse:
    wallet = await wallet_repository.get_wallet_by_name_for_update(db, current_user.id, operation.wallet_name)
    if not wallet:
        raise WalletNotFoundError(operation.wallet_name)

    wallet.balance += operation.amount
    created = await operations_repository.create_operation(
        db=db,
        wallet_id=wallet.id,
        type=OperationType.INCOME,
        amount=operation.amount,
        currency=wallet.currency,
        description=operation.description,
    )
    await db.commit()

    return OperationResponse.model_validate(created)


async def add_expense(db: AsyncSession, current_user: User, operation: OperationRequest) -> OperationResponse:
    wallet = await wallet_repository.get_wallet_by_name_for_update(db, current_user.id, operation.wallet_name)
    if not wallet:
        raise WalletNotFoundError(operation.wallet_name)

    if wallet.balance < operation.amount:
        raise InsufficientFundsError(wallet.balance, wallet.currency)

    wallet.balance -= operation.amount
    created = await operations_repository.create_operation(
        db=db,
        wallet_id=wallet.id,
        type=OperationType.EXPENSE,
        amount=operation.amount,
        currency=wallet.currency,
        description=operation.description,
    )
    await db.commit()

    return OperationResponse.model_validate(created)


async def get_operations_list(
    db: AsyncSession,
    current_user: User,
    wallet_id: int | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> list[OperationResponse]:
    if wallet_id:
        wallet = await wallet_repository.get_wallet_by_id(db, current_user.id, wallet_id)
        if not wallet:
            raise WalletNotFoundError(wallet_id)

        wallets_ids = [wallet.id]
    else:
        wallets = await wallet_repository.get_all_wallets(db, current_user.id)
        wallets_ids = [wallet.id for wallet in wallets]

    operations = await operations_repository.get_operations_list(db, wallets_ids, date_from, date_to)
    return [OperationResponse.model_validate(operation) for operation in operations]


async def transfer_between_wallets(
    db: AsyncSession,
    http_session: aiohttp.ClientSession,
    user_id: int,
    from_wallet_id: int,
    to_wallet_id: int,
    amount: Decimal,
) -> list[OperationResponse]:
    from_wallet = await wallet_repository.get_wallet_by_id(db, user_id, from_wallet_id)
    to_wallet = await wallet_repository.get_wallet_by_id(db, user_id, to_wallet_id)

    if not from_wallet or not to_wallet:
        raise WalletNotFoundError()

    # The rate is requested before locking: waiting for an external API while holding the locks
    # would block every other operation on both wallets
    target_amount = amount
    if from_wallet.currency != to_wallet.currency:
        exchange_rate = await get_exchange_rate(http_session, from_wallet.currency, to_wallet.currency)
        target_amount = round(amount * exchange_rate, 2)

    # Balances read above may already be outdated. Locking loads the same session objects again
    # with the current row values, so from_wallet and to_wallet are up to date after this line
    await wallet_repository.get_wallets_by_ids_for_update(db, user_id, [from_wallet.id, to_wallet.id])

    if from_wallet.balance < amount:
        raise InsufficientFundsError(from_wallet.balance, from_wallet.currency)

    from_wallet.balance = round(from_wallet.balance - amount, 2)
    to_wallet.balance = round(to_wallet.balance + target_amount, 2)
    transfer_id = str(uuid4())
    outgoing = await operations_repository.create_operation(
        db=db,
        wallet_id=from_wallet.id,
        type=OperationType.TRANSFER,
        amount=amount,
        currency=from_wallet.currency,
        category="transfer",
        subcategory="outgoing",
        transfer_id=transfer_id,
        description=f"Transfer to {to_wallet.name}",
    )
    incoming = await operations_repository.create_operation(
        db=db,
        wallet_id=to_wallet.id,
        type=OperationType.TRANSFER,
        amount=target_amount,
        currency=to_wallet.currency,
        category="transfer",
        subcategory="incoming",
        transfer_id=transfer_id,
        description=f"Transfer from {from_wallet.name}",
    )
    await db.commit()
    return [OperationResponse.model_validate(outgoing), OperationResponse.model_validate(incoming)]
