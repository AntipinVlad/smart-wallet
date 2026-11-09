import asyncio
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import openai
from openai import AsyncOpenAI
from pydantic import ValidationError

from app.ai.parsing.service import describe, request_parse
from app.ai.parsing.tools import (
    ARGUMENTS_BY_TOOL,
    OperationArguments,
    TransferArguments,
    build_tools,
)
from app.core.config import settings
from app.enums import CurrencyEnum
from app.models import Wallet

DATASET = Path(__file__).with_name("parsing.jsonl")
CONCURRENCY = 5
# Fixed, so "yesterday" or "on Saturday" always have the same right answer. A Monday
TODAY = date(2026, 11, 9)
WALLETS = [
    Wallet(id=1, name="Monobank", currency=CurrencyEnum.UAH),
    Wallet(id=2, name="Cash", currency=CurrencyEnum.UAH),
    Wallet(id=3, name="Savings", currency=CurrencyEnum.USD),
]
WALLET_BY_ID = {wallet.id: wallet for wallet in WALLETS}
WALLET_BY_NAME = {wallet.name: wallet for wallet in WALLETS}
FIELDS = ("tool", "wallet", "from", "to", "amount", "currency", "days_ago")


@dataclass
class Example:
    lang: str
    text: str
    # A field holds one right value or a list of right values
    expected: list[dict[str, Any]]


@dataclass
class Outcome:
    example: Example
    predicted: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0


def load_examples() -> list[Example]:
    examples = []
    for line_number, line in enumerate(DATASET.read_text(encoding="utf-8").splitlines(), start=1):
        row = json.loads(line)
        for call in row["expected"]:
            # A typo in the dataset would be counted as a model mistake
            unknown = set(call) - set(FIELDS)
            names = [call.get(key) for key in ("wallet", "from", "to")]
            if unknown or any(name is not None and name not in WALLET_BY_NAME for name in names):
                raise ValueError(f"Line {line_number}: unknown fields {unknown} or wallet in {call}")
        examples.append(Example(lang=row["lang"], text=row["text"], expected=row["expected"]))
    return examples


def wallet_name(wallet_id: int | None) -> str | None:
    return WALLET_BY_ID[wallet_id].name if wallet_id is not None else None


def normalize(name: str, arguments: str) -> dict[str, Any]:
    """The call as the app turns it into a draft: the currency is the one of the wallet unless another is named."""
    arguments_model = ARGUMENTS_BY_TOOL.get(name)
    if arguments_model is None:
        raise ValueError(f"unknown function {name}")
    parsed = arguments_model.model_validate_json(arguments)
    match parsed:
        case OperationArguments():
            wallet_id = parsed.wallet_id
            wallets = {"wallet": wallet_name(wallet_id)}
        case TransferArguments():
            wallet_id = parsed.from_wallet_id
            wallets = {"from": wallet_name(wallet_id), "to": wallet_name(parsed.to_wallet_id)}
    currency = parsed.currency
    if currency is None and wallet_id is not None:
        currency = WALLET_BY_ID[wallet_id].currency
    return {
        "tool": name,
        **wallets,
        "amount": parsed.amount,
        "currency": currency,
        "days_ago": (TODAY - parsed.date).days,
    }


async def evaluate(client: AsyncOpenAI, example: Example, limit: asyncio.Semaphore) -> Outcome:
    tools = build_tools(list(WALLET_BY_ID))
    async with limit:
        try:
            response = await request_parse(client, describe(example.text, WALLETS, TODAY), tools)
        except openai.APIError as e:
            return Outcome(example, error=repr(e))
    usage = response.usage
    outcome = Outcome(
        example,
        input_tokens=usage.input_tokens if usage else 0,
        output_tokens=usage.output_tokens if usage else 0,
    )
    try:
        outcome.predicted = [
            normalize(item.name, item.arguments) for item in response.output if item.type == "function_call"
        ]
    except (ValidationError, KeyError, ValueError) as e:
        # KeyError: a wallet id outside the list
        outcome.error = f"invalid call: {e!r}"
    return outcome


def field_matches(expected: Any, predicted: Any, key: str) -> bool:
    allowed = expected if isinstance(expected, list) else [expected]
    if key == "amount":
        return any(predicted == Decimal(value) for value in allowed)
    return predicted in allowed


def call_matches(expected: dict[str, Any], predicted: dict[str, Any]) -> bool:
    return all(field_matches(value, predicted.get(key), key) for key, value in expected.items())


def is_correct(outcome: Outcome) -> bool:
    if outcome.error is not None or len(outcome.predicted) != len(outcome.example.expected):
        return False
    # Order does not matter: each expected call needs its own predicted call
    unused = list(outcome.predicted)
    for expected in outcome.example.expected:
        match = next((predicted for predicted in unused if call_matches(expected, predicted)), None)
        if match is None:
            return False
        unused.remove(match)
    return True


def share(correct: int, total: int) -> str:
    return f"{correct}/{total} ({correct / total:.0%})" if total else "-"


def report(outcomes: list[Outcome]) -> None:
    print(f"Model: {settings.openai_model}, examples: {len(outcomes)}\n")
    print(f"Exact result:       {share(sum(is_correct(o) for o in outcomes), len(outcomes))}")
    right_count = [o for o in outcomes if o.error is None and len(o.predicted) == len(o.example.expected)]
    print(f"Operations count:   {share(len(right_count), len(outcomes))}")
    without = [o for o in outcomes if not o.example.expected]
    print(f"No operation found: {share(sum(is_correct(o) for o in without), len(without))} (texts without one)\n")

    # Per field, for the examples with the right number of calls, paired in the order of the text
    print("Fields:")
    for key in FIELDS:
        pairs = [
            (expected[key], predicted.get(key))
            for outcome in right_count
            for expected, predicted in zip(outcome.example.expected, outcome.predicted, strict=True)
            if key in expected
        ]
        correct = sum(field_matches(expected, predicted, key) for expected, predicted in pairs)
        print(f"  {key:<9} {share(correct, len(pairs))}")

    by_lang: dict[str, list[Outcome]] = defaultdict(list)
    for outcome in outcomes:
        by_lang[outcome.example.lang].append(outcome)
    print()
    for lang, lang_outcomes in sorted(by_lang.items()):
        print(f"{lang}: exact {share(sum(is_correct(o) for o in lang_outcomes), len(lang_outcomes))}")

    mistakes = [outcome for outcome in outcomes if not is_correct(outcome)]
    print(f"\nMistakes ({len(mistakes)}):")
    for outcome in mistakes:
        got = outcome.error or json.dumps(outcome.predicted, ensure_ascii=False, default=str)
        print(f"  [{outcome.example.lang}] {outcome.example.text!r}")
        print(f"      expected {json.dumps(outcome.example.expected, ensure_ascii=False)}")
        print(f"      got      {got}")

    input_tokens = sum(outcome.input_tokens for outcome in outcomes)
    output_tokens = sum(outcome.output_tokens for outcome in outcomes)
    print(
        f"\nTokens: {input_tokens} input, {output_tokens} output. Multiply by the prices of the model to get the cost"
    )


async def main() -> None:
    examples = load_examples()
    limit = asyncio.Semaphore(CONCURRENCY)
    async with AsyncOpenAI(
        api_key=settings.openai_api_key.get_secret_value(), timeout=settings.openai_timeout_seconds
    ) as client:
        outcomes = await asyncio.gather(*(evaluate(client, example, limit) for example in examples))
    report(list(outcomes))


if __name__ == "__main__":
    asyncio.run(main())
