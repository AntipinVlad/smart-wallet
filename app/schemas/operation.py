from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

from app.enums import CurrencyEnum
from app.schemas.common import Money


class OperationRequest(BaseModel):
    wallet_name: str = Field(..., max_length=127)
    amount: Money
    description: str | None = Field(None, max_length=255)

    @field_validator("amount")
    def amount_must_be_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("Amount must be greater than 0")

        return v

    @field_validator("wallet_name")
    def wallet_name_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Wallet name cannot be empty")

        return v


class OperationResponse(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    wallet_id: int
    type: str
    amount: Decimal
    currency: CurrencyEnum
    category: str | None
    subcategory: str | None
    transfer_id: str | None
    created_at: datetime
    description: str | None


class TransferRequest(BaseModel):
    from_wallet_id: int
    to_wallet_id: int
    amount: Money

    @field_validator("to_wallet_id")
    @classmethod
    def wallets_must_be_different(cls, v: int, info) -> int:
        if "from_wallet_id" in info.data and v == info.data["from_wallet_id"]:
            raise ValueError("Wallet id must be different")
        return v

    @field_validator("amount")
    @classmethod
    def amount_must_be_positive(cls, v: int) -> Decimal:
        if v <= 0:
            raise ValueError("Amount must be greater than 0")
        return v
