from datetime import datetime
from decimal import Decimal

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import CurrencyEnum, OperationType
from app.models import Operation, Wallet
from app.repository.pagination import apply_sort


async def create_operation(
    db: AsyncSession,
    wallet_id: int,
    type: OperationType,
    amount: Decimal,
    currency: CurrencyEnum,
    category: str | None = None,
    subcategory: str | None = None,
    transfer_id: str | None = None,
    description: str | None = None,
) -> Operation:
    operation = Operation(
        wallet_id=wallet_id,
        type=type,
        amount=amount,
        currency=currency,
        category=category,
        subcategory=subcategory,
        transfer_id=transfer_id,
        description=description,
    )
    db.add(operation)
    await db.flush()
    return operation


async def get_operation_by_id(db: AsyncSession, operation_id: int) -> Operation | None:
    return await db.get(Operation, operation_id)


SORT_COLUMNS = {"created_at": Operation.created_at, "amount": Operation.amount}


def operations_query(
    user_id: int,
    sort: str,
    wallet_id: int | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> Select[Operation]:
    query = select(Operation).join(Wallet, Operation.wallet_id == Wallet.id).where(Wallet.user_id == user_id)

    if wallet_id is not None:
        query = query.where(Operation.wallet_id == wallet_id)

    if date_from is not None:
        query = query.where(Operation.created_at >= date_from)

    if date_to is not None:
        query = query.where(Operation.created_at <= date_to)

    return apply_sort(query, sort, SORT_COLUMNS, tiebreaker=Operation.id)
