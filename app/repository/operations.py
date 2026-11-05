from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import CurrencyEnum
from app.models import Operation


async def create_operation(
    db: AsyncSession,
    wallet_id: int,
    type: str,
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


async def get_operations_list(
    db: AsyncSession, wallets_ids: list[int], date_from: datetime | None = None, date_to: datetime | None = None
) -> list[Operation]:
    query = select(Operation).where(Operation.wallet_id.in_(wallets_ids))

    if date_from:
        query = query.where(Operation.created_at >= date_from)

    if date_to:
        query = query.where(Operation.created_at <= date_to)

    return list(await db.scalars(query))
