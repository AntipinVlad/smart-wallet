from datetime import datetime
from decimal import Decimal

from sqlalchemy import Select, or_, select
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
    created_at: datetime | None = None,
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
    # Without a date the column default sets the current time
    if created_at is not None:
        operation.created_at = created_at
    db.add(operation)
    await db.flush()
    return operation


async def get_operation_by_id(db: AsyncSession, operation_id: int) -> Operation | None:
    return await db.get(Operation, operation_id)


async def operations_to_embed(db: AsyncSession, model: str, limit: int) -> list[Operation]:
    """Operations whose description has no vector of this model yet; transfers have descriptions the app wrote."""
    statement = (
        select(Operation)
        .where(Operation.description.is_not(None), Operation.description != "")
        .where(Operation.type != OperationType.TRANSFER)
        .where(or_(Operation.embedding.is_(None), Operation.embedding_model != model))
        .order_by(Operation.id)
        .limit(limit)
    )
    return list((await db.scalars(statement)).all())


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
