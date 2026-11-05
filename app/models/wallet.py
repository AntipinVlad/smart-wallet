from decimal import Decimal

from sqlalchemy import CheckConstraint, Enum, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.enums import CurrencyEnum


class Wallet(Base):
    __tablename__ = "wallet"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_wallet_user_id_name"),
        CheckConstraint("balance >= 0", name="ck_wallet_balance_not_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    balance: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=False)
    currency: Mapped[CurrencyEnum] = mapped_column(Enum(CurrencyEnum, native_enum=False))
