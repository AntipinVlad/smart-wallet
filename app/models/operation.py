from datetime import datetime
from decimal import Decimal

from sqlalchemy import Enum, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.enums import CurrencyEnum


class Operation(Base):
    __tablename__ = "operation"
    id: Mapped[int] = mapped_column(primary_key=True)
    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallet.id"))
    type: Mapped[str]
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    currency: Mapped[CurrencyEnum] = mapped_column(Enum(CurrencyEnum, native_enum=False))
    category: Mapped[str | None] = mapped_column(String(255), default=None)
    subcategory: Mapped[str | None] = mapped_column(String(255), default=None)
    transfer_id: Mapped[str | None] = mapped_column(default=None, index=True)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now())
    description: Mapped[str | None] = mapped_column(String(255), default=None)
