from datetime import date
from decimal import Decimal

from sqlalchemy import Date, Enum, Numeric
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.enums import CurrencyEnum


class ExchangeRate(Base):
    __tablename__ = "exchange_rate"

    # Currency first: the nearest earlier day of one currency is one range of the key
    currency: Mapped[CurrencyEnum] = mapped_column(Enum(CurrencyEnum, native_enum=False), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    # Units of the currency for one US dollar: any pair is the ratio of two rows of one day
    per_usd: Mapped[Decimal] = mapped_column(Numeric(20, 10))
