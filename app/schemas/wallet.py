from decimal import Decimal

from pydantic import BaseModel

from app.enums import CurrencyEnum
from app.schemas.common import Name, NonNegativeMoney


class CreateWalletRequest(BaseModel):
    name: Name
    initial_balance: NonNegativeMoney = Decimal("0")
    currency: CurrencyEnum = CurrencyEnum.UAH


class WalletResponse(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    name: str
    balance: Decimal
    currency: CurrencyEnum


class TotalBalance(BaseModel):
    total_balance: Decimal
    currency: CurrencyEnum
