from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import NamedTuple

from sqlalchemy import ColumnElement, Date, Select, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import CurrencyEnum, OperationType
from app.models import ExchangeRate, Operation, Wallet

COUNTED_TYPES = (OperationType.EXPENSE, OperationType.INCOME)


@dataclass(frozen=True)
class OperationFilter:
    user_id: int
    start: datetime
    end: datetime
    timezone: str
    wallet_id: int | None = None


class OperationGroup(NamedTuple):
    """Operations of one day, currency, type and category, with their sum in the currency of the report."""

    day: date
    currency: CurrencyEnum
    type: OperationType
    category: str | None
    subcategory: str | None
    operations: int
    amount: Decimal | None


def _local_day(timezone: str) -> ColumnElement[date]:
    return cast(func.timezone(timezone, Operation.created_at), Date)


def _counted[*Ts](statement: Select[*Ts], operation_filter: OperationFilter) -> Select[*Ts]:
    # Transfers move money between own wallets: they are neither income nor expenses
    statement = (
        statement.join(Wallet, Wallet.id == Operation.wallet_id)
        .where(Wallet.user_id == operation_filter.user_id)
        .where(Operation.type.in_(COUNTED_TYPES))
        # By the stored instant, not the local day: the index on created_at works only this way
        .where(Operation.created_at >= operation_filter.start, Operation.created_at < operation_filter.end)
    )
    if operation_filter.wallet_id is not None:
        statement = statement.where(Operation.wallet_id == operation_filter.wallet_id)
    return statement


async def first_operation_day(db: AsyncSession, user_id: int, timezone: str, wallet_id: int | None) -> date | None:
    """Local day of the first operation of any type: before it the user has no data, not zero spending."""
    statement = (
        select(cast(func.timezone(timezone, func.min(Operation.created_at)), Date))
        .join(Wallet, Wallet.id == Operation.wallet_id)
        .where(Wallet.user_id == user_id)
    )
    if wallet_id is not None:
        statement = statement.where(Operation.wallet_id == wallet_id)
    return await db.scalar(statement)


async def days_in_other_currencies(
    db: AsyncSession, operation_filter: OperationFilter, currency: CurrencyEnum
) -> set[date]:
    """Local days with operations that need the exchange rates of that day."""
    day = _local_day(operation_filter.timezone)
    statement = _counted(select(day).distinct(), operation_filter).where(Operation.currency != currency)
    return set(await db.scalars(statement))


def _per_usd(currency: CurrencyEnum | ColumnElement[CurrencyEnum], day: ColumnElement[date]) -> ColumnElement[Decimal]:
    """Rate of the day; for a day the API has no rates for, the nearest earlier day, else the nearest later one."""
    earlier = (
        select(ExchangeRate.per_usd)
        .where(ExchangeRate.currency == currency, ExchangeRate.day <= day)
        .order_by(ExchangeRate.day.desc())
        .limit(1)
        .scalar_subquery()
    )
    later = (
        select(ExchangeRate.per_usd)
        .where(ExchangeRate.currency == currency, ExchangeRate.day > day)
        .order_by(ExchangeRate.day)
        .limit(1)
        .scalar_subquery()
    )
    return func.coalesce(earlier, later)


async def operation_groups(
    db: AsyncSession, operation_filter: OperationFilter, currency: CurrencyEnum, history_start: date
) -> list[OperationGroup]:
    day = _local_day(operation_filter.timezone).label("day")
    group_columns = (Operation.currency, Operation.type, Operation.category, Operation.subcategory)
    # Summed first: a rate is looked up once per day and currency, not once per operation
    groups = (
        _counted(
            select(
                day,
                *group_columns,
                func.count().label("operations"),
                func.sum(Operation.amount).label("amount"),
            ),
            operation_filter,
        )
        .group_by(day, *group_columns)
        .subquery()
    )

    rate_day = func.greatest(groups.c.day, history_start)
    converted = case(
        (groups.c.currency == currency, groups.c.amount),
        else_=groups.c.amount * _per_usd(currency, rate_day) / _per_usd(groups.c.currency, rate_day),
    ).label("amount")
    statement = select(
        groups.c.day,
        groups.c.currency,
        groups.c.type,
        groups.c.category,
        groups.c.subcategory,
        groups.c.operations,
        converted,
    )
    return [OperationGroup(**row._mapping) for row in await db.execute(statement)]
