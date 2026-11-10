from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import CurrencyEnum
from app.models import ExchangeRate


async def get_stored_days(db: AsyncSession, days: set[date]) -> set[date]:
    result = await db.scalars(select(ExchangeRate.day).where(ExchangeRate.day.in_(days)).distinct())
    return set(result)


async def add_rates(db: AsyncSession, day: date, rates: dict[CurrencyEnum, Decimal]) -> None:
    values = [{"day": day, "currency": currency, "per_usd": rate} for currency, rate in rates.items()]
    # Another process may store the same day: the rates of a past day are the same
    await db.execute(insert(ExchangeRate).values(values).on_conflict_do_nothing())
