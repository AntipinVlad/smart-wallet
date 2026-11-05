from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, Field, field_validator

from app.core.exceptions import ErrorCode
from app.enum import CurrencyEnum

Money = Annotated[Decimal, Field(max_digits=18, decimal_places=2)]


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


class CreateWalletRequest(BaseModel):
    name: str = Field(..., max_length=127)
    initial_balance: Money = 0
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


class UserRequest(BaseModel):
    login: str = Field(..., max_length=127)


class UserCreateRequest(UserRequest):
    password: str = Field(..., min_length=8, max_length=128)


class LoginRequest(UserRequest):
    password: str = Field(..., max_length=128)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str


class UserResponse(UserRequest):
    model_config = {"from_attributes": True}
    id: int


class WalletResponse(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    name: str
    balance: Decimal
    currency: CurrencyEnum


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

class TransferCreateSchema(BaseModel):
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

class TotalBalance(BaseModel):
    total_balance: Decimal

class ErrorDetail(BaseModel):
    field: str = Field(..., examples=["body.login"])
    message: str = Field(..., examples=["Field required"])


class ErrorBody(BaseModel):
    code: ErrorCode = Field(..., description="Stable error code. Check it instead of the message")
    message: str = Field(..., description="Human readable description, may change")
    details: list[ErrorDetail] | None = Field(None, description="Invalid fields, only for VALIDATION_ERROR")


class ErrorResponse(BaseModel):
    error: ErrorBody
