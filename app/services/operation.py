from uuid import uuid4

import aiohttp
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import InsufficientFundsError, WalletNotFoundError
from app.enums import OperationType, TransferDirection
from app.models import Operation, User
from app.repository import operations as operations_repository
from app.repository import wallets as wallet_repository
from app.repository.pagination import paginate
from app.schemas import OperationListQuery, OperationRequest, OperationResponse, Page, TransferRequest
from app.services.exchange_service import get_exchange_rate
from app.services.periods import local_day
from app.worker import enqueue_categorization, enqueue_exchange_rates

TRANSFER_CATEGORY = "transfer"


async def add_income(db: AsyncSession, current_user: User, operation: OperationRequest) -> OperationResponse:
    wallet = await wallet_repository.get_wallet_by_id_for_update(db, current_user.id, operation.wallet_id)
    if not wallet:
        raise WalletNotFoundError(operation.wallet_id)

    wallet.balance += operation.amount
    created = await operations_repository.create_operation(
        db=db,
        wallet_id=wallet.id,
        type=OperationType.INCOME,
        amount=operation.amount,
        currency=wallet.currency,
        description=operation.description,
        created_at=operation.created_at,
    )
    await db.commit()
    await _enqueue_background_work(created, current_user)

    return OperationResponse.model_validate(created)


async def add_expense(db: AsyncSession, current_user: User, operation: OperationRequest) -> OperationResponse:
    wallet = await wallet_repository.get_wallet_by_id_for_update(db, current_user.id, operation.wallet_id)
    if not wallet:
        raise WalletNotFoundError(operation.wallet_id)

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
        created_at=operation.created_at,
    )
    await db.commit()
    await _enqueue_background_work(created, current_user)

    return OperationResponse.model_validate(created)


async def get_operations_page(
    db: AsyncSession, current_user: User, query: OperationListQuery
) -> Page[OperationResponse]:
    if query.wallet_id is not None and not await wallet_repository.get_wallet_by_id(
        db, current_user.id, query.wallet_id
    ):
        raise WalletNotFoundError(query.wallet_id)

    operations = operations_repository.operations_query(
        current_user.id, query.sort, query.wallet_id, query.date_from, query.date_to
    )
    return await paginate(db, operations, query, OperationResponse.model_validate)


async def transfer_between_wallets(
    db: AsyncSession, http_session: aiohttp.ClientSession, current_user: User, transfer: TransferRequest
) -> list[OperationResponse]:
    from_wallet = await wallet_repository.get_wallet_by_id(db, current_user.id, transfer.from_wallet_id)
    to_wallet = await wallet_repository.get_wallet_by_id(db, current_user.id, transfer.to_wallet_id)

    if not from_wallet or not to_wallet:
        raise WalletNotFoundError()

    # Before locking: waiting for the external API would hold the locks
    target_amount = transfer.amount
    if from_wallet.currency != to_wallet.currency:
        exchange_rate = await get_exchange_rate(http_session, from_wallet.currency, to_wallet.currency)
        target_amount = round(transfer.amount * exchange_rate, 2)

    # Reloads from_wallet and to_wallet with the locked, current balances
    await wallet_repository.get_wallets_by_ids_for_update(db, current_user.id, [from_wallet.id, to_wallet.id])

    if from_wallet.balance < transfer.amount:
        raise InsufficientFundsError(from_wallet.balance, from_wallet.currency)

    from_wallet.balance = round(from_wallet.balance - transfer.amount, 2)
    to_wallet.balance = round(to_wallet.balance + target_amount, 2)
    transfer_id = str(uuid4())
    outgoing = await operations_repository.create_operation(
        db=db,
        wallet_id=from_wallet.id,
        type=OperationType.TRANSFER,
        amount=transfer.amount,
        currency=from_wallet.currency,
        category=TRANSFER_CATEGORY,
        subcategory=TransferDirection.OUTGOING,
        transfer_id=transfer_id,
        description=f"Transfer to {to_wallet.name}",
        created_at=transfer.created_at,
    )
    incoming = await operations_repository.create_operation(
        db=db,
        wallet_id=to_wallet.id,
        type=OperationType.TRANSFER,
        amount=target_amount,
        currency=to_wallet.currency,
        category=TRANSFER_CATEGORY,
        subcategory=TransferDirection.INCOMING,
        transfer_id=transfer_id,
        description=f"Transfer from {from_wallet.name}",
        created_at=transfer.created_at,
    )
    await db.commit()
    return [OperationResponse.model_validate(outgoing), OperationResponse.model_validate(incoming)]


async def _enqueue_background_work(operation: Operation, current_user: User) -> None:
    # After commit: before it the worker's own connection cannot see the operation yet
    await enqueue_categorization(operation.id)
    # Ahead of the first report that needs them
    await enqueue_exchange_rates(local_day(operation.created_at, current_user.timezone))
