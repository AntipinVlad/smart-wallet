from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

from app.enums import CurrencyEnum
from app.schemas.common import Money


class CreateWalletRequest(BaseModel):
    name: str = Field(..., max_length=127)
    initial_balance: Money = Decimal("0")
    currency: CurrencyEnum = CurrencyEnum.UAH

    @field_validator("initial_balance")
    def balance_not_negative(cls, v: Decimal) -> Decimal:
        if v < 0:
            raise ValueError("Initial balance cannot be negative")

        return v

    @field_validator("name")
    def name_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Wallet name cannot be empty")

        return v


class WalletResponse(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    name: str
    balance: Decimal
    currency: CurrencyEnum


class TotalBalance(BaseModel):
    total_balance: Decimal
