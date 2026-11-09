import datetime
from typing import Annotated, Any, Literal

import openai
from openai.types.responses import FunctionToolParam
from pydantic import BaseModel, Field, JsonValue, WithJsonSchema, create_model

from app.enums import CurrencyEnum, OperationType
from app.schemas.common import PositiveMoney

EXPENSE_TOOL = "add_expense"
INCOME_TOOL = "add_income"
TRANSFER_TOOL = "create_transfer"

WALLET_HINT = "Id of a wallet from the list. null if the message does not say which wallet"
AMOUNT_HINT = (
    "Amount in the currency of the message, as a number: '412,50' is 412.5, 'півтори тисячі' or '1,5к' is 1500"
)
CURRENCY_HINT = "Currency named in the message. null if none is named"

# Decimal allows a string in its schema, and then the model copies the amount from the text as is
ToolAmount = Annotated[PositiveMoney, WithJsonSchema({"type": "number", "exclusiveMinimum": 0})]


class OperationArguments(BaseModel):
    wallet_id: int | None = Field(description=WALLET_HINT)
    amount: ToolAmount = Field(description=AMOUNT_HINT)
    currency: CurrencyEnum | None = Field(description=CURRENCY_HINT)
    date: datetime.date = Field(description="Day of the operation. Today unless the message names another day")
    description: str = Field(description="What the money was for, short, in the language of the message")

    def wallet_ids(self) -> list[int | None]:
        return [self.wallet_id]


class TransferArguments(BaseModel):
    from_wallet_id: int | None = Field(description=WALLET_HINT)
    to_wallet_id: int | None = Field(description=WALLET_HINT)
    amount: ToolAmount = Field(description=AMOUNT_HINT)
    currency: CurrencyEnum | None = Field(description=CURRENCY_HINT)
    date: datetime.date = Field(description="Day of the transfer. Today unless the message names another day")

    def wallet_ids(self) -> list[int | None]:
        return [self.from_wallet_id, self.to_wallet_id]


OPERATION_TYPE_BY_TOOL: dict[str, Literal[OperationType.EXPENSE, OperationType.INCOME]] = {
    EXPENSE_TOOL: OperationType.EXPENSE,
    INCOME_TOOL: OperationType.INCOME,
}

ARGUMENTS_BY_TOOL: dict[str, type[OperationArguments | TransferArguments]] = {
    EXPENSE_TOOL: OperationArguments,
    INCOME_TOOL: OperationArguments,
    TRANSFER_TOOL: TransferArguments,
}


def _wallet_field(wallet_ids: list[int]) -> Any:
    # The schema lists only the user's wallets: the model cannot name another one
    allowed: list[JsonValue] = list(wallet_ids)
    wallet_type = Annotated[int, Field(json_schema_extra={"enum": allowed})] | None if wallet_ids else None
    return (wallet_type, Field(description=WALLET_HINT))


def build_tools(wallet_ids: list[int]) -> list[FunctionToolParam]:
    wallet = _wallet_field(wallet_ids)
    operation = create_model("OperationArguments", __base__=OperationArguments, wallet_id=wallet)
    transfer = create_model("TransferArguments", __base__=TransferArguments, from_wallet_id=wallet, to_wallet_id=wallet)
    return [
        openai.pydantic_responses_function_tool(
            operation, name=EXPENSE_TOOL, description="Record money the user spent"
        ),
        openai.pydantic_responses_function_tool(
            operation, name=INCOME_TOOL, description="Record money the user received"
        ),
        openai.pydantic_responses_function_tool(
            transfer, name=TRANSFER_TOOL, description="Move money between two wallets of the user"
        ),
    ]
