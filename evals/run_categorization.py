import asyncio
import json
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import openai
from openai import AsyncOpenAI

from app.ai.categorization.service import RESULT_FORMATS, request_category
from app.core.config import settings
from app.enums import CurrencyEnum, OperationType
from app.models import Operation

DATASET = Path(__file__).with_name("categorization.jsonl")
# Several requests at once, but not so many that a new account hits its rate limit
CONCURRENCY = 5


@dataclass
class Example:
    lang: str
    operation: Operation
    expected: str


@dataclass
class Outcome:
    example: Example
    predicted: str | None
    error: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0


def load_examples() -> list[Example]:
    examples = []
    for line_number, line in enumerate(DATASET.read_text(encoding="utf-8").splitlines(), start=1):
        row = json.loads(line)
        operation_type = OperationType(row["type"])
        allowed = {category.value for category in RESULT_FORMATS[operation_type].model_fields["category"].annotation}
        # A typo in the dataset would be counted as a model mistake
        if row["expected"] not in allowed:
            raise ValueError(f"Line {line_number}: {row['expected']!r} is not a {operation_type} category")
        operation = Operation(
            type=operation_type,
            amount=Decimal(row["amount"]),
            currency=CurrencyEnum(row["currency"]),
            description=row["description"],
        )
        examples.append(Example(lang=row["lang"], operation=operation, expected=row["expected"]))
    return examples


async def evaluate(client: AsyncOpenAI, example: Example, limit: asyncio.Semaphore) -> Outcome:
    async with limit:
        try:
            response = await request_category(client, example.operation, RESULT_FORMATS[example.operation.type])
        except openai.APIError as e:
            return Outcome(example, predicted=None, error=repr(e))
    result = response.output_parsed
    usage = response.usage
    return Outcome(
        example,
        predicted=result.category.value if result else None,
        error=None if result else "no answer",
        input_tokens=usage.input_tokens if usage else 0,
        output_tokens=usage.output_tokens if usage else 0,
    )


def accuracy(outcomes: list[Outcome], top_level: bool = False) -> str:
    def same(outcome: Outcome) -> bool:
        if outcome.predicted is None:
            return False
        if top_level:
            return outcome.predicted.split(".")[0] == outcome.example.expected.split(".")[0]
        return outcome.predicted == outcome.example.expected

    correct = sum(same(outcome) for outcome in outcomes)
    return f"{correct}/{len(outcomes)} ({correct / len(outcomes):.0%})"


def report(outcomes: list[Outcome]) -> None:
    print(f"Model: {settings.openai_model}, examples: {len(outcomes)}\n")
    print(f"Exact category:     {accuracy(outcomes)}")
    print(f"Top-level category: {accuracy(outcomes, top_level=True)}\n")

    by_lang: dict[str, list[Outcome]] = defaultdict(list)
    for outcome in outcomes:
        by_lang[outcome.example.lang].append(outcome)
    for lang, lang_outcomes in sorted(by_lang.items()):
        print(f"{lang}: exact {accuracy(lang_outcomes)}, top-level {accuracy(lang_outcomes, top_level=True)}")

    mistakes = [outcome for outcome in outcomes if outcome.predicted != outcome.example.expected]
    print(f"\nMistakes ({len(mistakes)}):")
    for outcome in mistakes:
        example = outcome.example
        got = outcome.predicted or f"ERROR {outcome.error}"
        print(f"  [{example.lang}] {example.operation.description!r}: expected {example.expected}, got {got}")

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
