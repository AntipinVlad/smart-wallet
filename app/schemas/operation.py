from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, Field, ValidationInfo, field_validator

from app.enums import CurrencyEnum, OperationType
from app.schemas.common import PastDatetime, PositiveMoney
from app.schemas.pagination import PageParams, sort_pattern


class OperationRequest(BaseModel):
    wallet_id: int
    amount: PositiveMoney
    description: str | None = Field(None, max_length=255)
    created_at: PastDatetime | None = None


class OperationResponse(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    wallet_id: int
    type: OperationType
    amount: Decimal
    currency: CurrencyEnum
    category: str | None
    subcategory: str | None
    transfer_id: str | None
    created_at: datetime
    description: str | None


class OperationSortField(StrEnum):
    CREATED_AT = "created_at"
    AMOUNT = "amount"


class OperationListQuery(PageParams):
    sort: str = Field(
        "created_at,desc",
        pattern=sort_pattern(OperationSortField),
        description="field,asc or field,desc. Fields: " + ", ".join(OperationSortField),
    )
    wallet_id: int | None = None
    # A time zone is required: a naive time is ambiguous
    date_from: AwareDatetime | None = None
    date_to: AwareDatetime | None = None


class TransferRequest(BaseModel):
    from_wallet_id: int
    to_wallet_id: int
    amount: PositiveMoney
    created_at: PastDatetime | None = None

    @field_validator("to_wallet_id")
    @classmethod
    def wallets_must_be_different(cls, v: int, info: ValidationInfo) -> int:
        if "from_wallet_id" in info.data and v == info.data["from_wallet_id"]:
            raise ValueError("Wallet id must be different")
        return v


class ParseRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class OriginalAmount(BaseModel):
    amount: Decimal
    currency: CurrencyEnum


class DraftAmount(BaseModel):
    # In the wallet currency: the confirming endpoint takes the amount as is
    amount: Decimal
    # None until the wallet is known
    currency: CurrencyEnum | None
    # What the text said, when it was in another currency
    original: OriginalAmount | None
    exchange_rate: Decimal | None
    created_at: datetime


class OperationDraft(DraftAmount):
    type: Literal[OperationType.EXPENSE, OperationType.INCOME]
    wallet_id: int | None
    description: str = Field(max_length=255)


class TransferDraft(DraftAmount):
    type: Literal[OperationType.TRANSFER]
    from_wallet_id: int | None
    to_wallet_id: int | None


Draft = Annotated[OperationDraft | TransferDraft, Field(discriminator="type")]
