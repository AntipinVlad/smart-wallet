import argparse
import asyncio
import json
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from statistics import mean
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.categorization.categories import ExpenseCategory, IncomeCategory
from app.ai.embeddings.service import request_embeddings
from app.core.clients import create_openai_client
from app.core.config import settings
from app.database import SessionLocal, engine
from app.enums import CurrencyEnum, OperationType
from app.models import Operation, User, Wallet
from app.services.search import (
    find_operations,
    find_operations_by_meaning,
    find_operations_hybrid,
    understand_search,
)

OPERATIONS = Path(__file__).with_name("search_operations.jsonl")
QUERIES = Path(__file__).with_name("search_queries.jsonl")
# Above the largest expected set, so every query can reach recall 1.0
K = 10
EVAL_TIMEZONE = "Europe/Kyiv"
# The day after the last operation of the dataset: "в жовтні" means October 2026 in every run
EVAL_TODAY = date(2026, 10, 15)
# Not a hash of any password: nobody can log in as the eval user
UNUSABLE_PASSWORD_HASH = "!"
CATEGORIES: dict[OperationType, set[str]] = {
    OperationType.EXPENSE: set(ExpenseCategory),
    OperationType.INCOME: set(IncomeCategory),
}

# The same call the search endpoint makes: (db, user_id, query, limit) -> operation ids, best first
Retriever = Callable[[AsyncSession, int, str, int], Awaitable[list[int]]]
# Descriptions -> vectors, in their order
Embedder = Callable[[list[str]], Awaitable[list[list[float]]]]


@dataclass(frozen=True)
class Method:
    retrieve: Retriever
    # Set when the method searches by vectors: the operations of the dataset get them before the queries
    embed: Embedder | None = None


def methods(openai_client: AsyncOpenAI) -> dict[str, Method]:
    async def baseline(db: AsyncSession, user_id: int, query: str, limit: int) -> list[int]:
        return [operation.id for operation in await find_operations(db, user_id, query, limit)]

    async def vector(db: AsyncSession, user_id: int, query: str, limit: int) -> list[int]:
        operations = await find_operations_by_meaning(db, openai_client, user_id, query, limit)
        return [operation.id for operation in operations]

    async def hybrid(db: AsyncSession, user_id: int, query: str, limit: int) -> list[int]:
        operations = await find_operations_hybrid(db, openai_client, user_id, query, limit)
        return [operation.id for operation in operations]

    async def understood(db: AsyncSession, user_id: int, query: str, limit: int) -> list[int]:
        text, date_from, date_to = await understand_search(
            openai_client, query, EVAL_TODAY, ZoneInfo(EVAL_TIMEZONE), None, None
        )
        operations = await find_operations_hybrid(db, openai_client, user_id, text, limit, None, date_from, date_to)
        return [operation.id for operation in operations]

    async def embed(texts: list[str]) -> list[list[float]]:
        return await request_embeddings(openai_client, texts)

    return {
        "baseline": Method(baseline),
        "vector": Method(vector, embed),
        "hybrid": Method(hybrid, embed),
        "understood": Method(understood, embed),
    }


class Group(StrEnum):
    BROAD = "broad"
    SEMANTIC = "semantic"
    CROSS_LANGUAGE = "cross_language"
    TYPO = "typo"
    # Asked as a question, not typed as a search: "скільки я витратив на каву в жовтні"
    QUESTION = "question"


class Intent(StrEnum):
    # The user wants every matching operation: recall matters
    ALL = "all"
    # The user looks for one operation: its position matters, so MRR
    ONE = "one"


class Split(StrEnum):
    # Thresholds are tuned here
    DEV = "dev"
    # Run once at the end: an honest score
    TEST = "test"


@dataclass(frozen=True)
class EvalOperation:
    key: str
    type: OperationType
    amount: Decimal
    currency: CurrencyEnum
    description: str
    category: str
    day: date


@dataclass(frozen=True)
class EvalQuery:
    text: str
    group: Group
    intent: Intent
    split: Split
    expected: frozenset[str]


@dataclass(frozen=True)
class Dataset:
    operations: list[EvalOperation]
    queries: list[EvalQuery]


@dataclass(frozen=True)
class QueryResult:
    query: EvalQuery
    found: list[str]
    k: int

    @property
    def recall(self) -> float:
        return recall_at_k(self.found, self.query.expected, self.k)

    @property
    def reciprocal_rank(self) -> float:
        return reciprocal_rank(self.found, self.query.expected)

    @property
    def precision(self) -> float:
        return precision_at_k(self.found, self.query.expected, self.k)


@dataclass(frozen=True)
class GroupScore:
    group: Group
    intent: Intent
    queries: int
    recall: float
    mrr: float
    precision: float


def recall_at_k(found: list[str], expected: set[str] | frozenset[str], k: int) -> float:
    return len(set(found[:k]) & expected) / len(expected)


def reciprocal_rank(found: list[str], expected: set[str] | frozenset[str]) -> float:
    for position, key in enumerate(found, start=1):
        if key in expected:
            return 1 / position
    return 0.0


def precision_at_k(found: list[str], expected: set[str] | frozenset[str], k: int) -> float:
    # Divided by what was returned, not by k: a query with one right operation can still score 1.0
    returned = found[:k]
    if not returned:
        # An empty answer must not beat an honest one on precision
        return 0.0
    return len(set(returned) & expected) / len(returned)


def load_dataset(operations_path: Path = OPERATIONS, queries_path: Path = QUERIES) -> Dataset:
    """Fails on any mistake in the files: a typo in a key would be counted as a search mistake."""
    operations = [_operation(row, line) for line, row in _rows(operations_path)]
    keys = [operation.key for operation in operations]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        raise ValueError(f"{operations_path.name}: duplicate keys {duplicates}")

    queries = [_query(row, line, set(keys)) for line, row in _rows(queries_path)]
    splits_by_group: dict[Group, set[Split]] = defaultdict(set)
    for query in queries:
        splits_by_group[query.group].add(query.split)
    one_split = sorted(group for group, splits in splits_by_group.items() if splits != set(Split))
    if one_split:
        raise ValueError(f"{queries_path.name}: groups {one_split} must have queries in both dev and test")
    return Dataset(operations=operations, queries=queries)


async def evaluate(
    db: AsyncSession, retriever: Retriever, dataset: Dataset, k: int, embed: Embedder | None = None
) -> list[QueryResult]:
    """Adds the operations as one new user and runs every query; the caller owns the transaction."""
    too_many = [query.text for query in dataset.queries if len(query.expected) > k]
    if too_many:
        # Such a query can never reach recall 1.0: its score would say nothing about the search
        raise ValueError(f"Queries {too_many} expect more than k={k} operations")

    user = User(login=f"search-evals-{uuid4().hex}", password_hash=UNUSABLE_PASSWORD_HASH, timezone=EVAL_TIMEZONE)
    db.add(user)
    await db.flush()
    wallets = {}
    for currency in {operation.currency for operation in dataset.operations}:
        wallets[currency] = Wallet(name=f"evals {currency}", balance=Decimal(0), user_id=user.id, currency=currency)
    db.add_all(wallets.values())
    await db.flush()

    rows = {operation.key: _row(operation, wallets[operation.currency]) for operation in dataset.operations}
    if embed is not None:
        # Set on the rows, not through embed_missing: that one commits, and the eval data must roll back
        vectors = await embed([operation.description for operation in dataset.operations])
        for row, vector in zip(rows.values(), vectors, strict=True):
            row.embedding, row.embedding_model = vector, settings.openai_embedding_model
    db.add_all(rows.values())
    await db.flush()
    key_by_id = {row.id: key for key, row in rows.items()}

    results = []
    for query in dataset.queries:
        ids = await retriever(db, user.id, query.text, k)
        foreign = [operation_id for operation_id in ids if operation_id not in key_by_id]
        if foreign:
            raise ValueError(f"Query {query.text!r} returned operations {foreign} of another user")
        results.append(QueryResult(query=query, found=[key_by_id[operation_id] for operation_id in ids], k=k))
    return results


async def run(retriever: Retriever, dataset: Dataset, k: int, embed: Embedder | None = None) -> list[QueryResult]:
    async with SessionLocal() as db:
        try:
            return await evaluate(db, retriever, dataset, k, embed)
        finally:
            # The eval data never stays in the database, whichever one the settings point to
            await db.rollback()


def summarize(results: list[QueryResult], split: Split) -> list[GroupScore]:
    by_group: dict[tuple[Group, Intent], list[QueryResult]] = defaultdict(list)
    for result in results:
        if result.query.split == split:
            by_group[(result.query.group, result.query.intent)].append(result)
    return [
        GroupScore(
            group=group,
            intent=intent,
            queries=len(group_results),
            recall=mean(result.recall for result in group_results),
            mrr=mean(result.reciprocal_rank for result in group_results),
            precision=mean(result.precision for result in group_results),
        )
        for group in Group
        for intent in Intent
        if (group_results := by_group.get((group, intent)))
    ]


def _rows(path: Path) -> list[tuple[int, dict[str, Any]]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [(number, json.loads(line)) for number, line in enumerate(lines, start=1) if line.strip()]


def _operation(row: dict[str, Any], line: int) -> EvalOperation:
    operation_type = OperationType(row["type"])
    if row["category"] not in CATEGORIES[operation_type]:
        raise ValueError(f"Operations line {line}: {row['category']!r} is not a {operation_type} category")
    return EvalOperation(
        key=row["key"],
        type=operation_type,
        amount=Decimal(row["amount"]),
        currency=CurrencyEnum(row["currency"]),
        description=row["description"],
        category=row["category"],
        day=date.fromisoformat(row["date"]),
    )


def _query(row: dict[str, Any], line: int, keys: set[str]) -> EvalQuery:
    expected = frozenset(row["expected"])
    if not expected:
        raise ValueError(f"Queries line {line}: {row['query']!r} expects no operations")
    unknown = sorted(expected - keys)
    if unknown:
        raise ValueError(f"Queries line {line}: {row['query']!r} expects unknown operations {unknown}")
    return EvalQuery(
        text=row["query"],
        group=Group(row["group"]),
        intent=Intent(row["intent"]),
        split=Split(row["split"]),
        expected=expected,
    )


def _row(operation: EvalOperation, wallet: Wallet) -> Operation:
    category, _, subcategory = operation.category.partition(".")
    return Operation(
        wallet_id=wallet.id,
        type=operation.type,
        amount=operation.amount,
        currency=operation.currency,
        description=operation.description,
        category=category,
        subcategory=subcategory,
        created_at=datetime.combine(operation.day, time(12), tzinfo=UTC),
    )


def report(results: list[QueryResult], split: Split, retriever: str) -> None:
    print(f"Retriever: {retriever}, split: {split}, k={K}\n")
    print(f"{'group':<16}{'intent':<8}{'queries':>8}{'recall':>8}{'MRR':>8}{'precision':>11}")
    for score in summarize(results, split):
        print(
            f"{score.group:<16}{score.intent:<8}{score.queries:>8}"
            f"{score.recall:>8.2f}{score.mrr:>8.2f}{score.precision:>11.2f}"
        )

    print("\nQueries below recall 1.0 or precision 1.0:")
    for result in results:
        if result.query.split != split or (result.recall == 1.0 and result.precision == 1.0):
            continue
        missed = sorted(result.query.expected - set(result.found))
        extra = [key for key in result.found if key not in result.query.expected]
        print(f"  [{result.query.group}] {result.query.text!r}: missed {missed}, extra {extra}")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Search quality on the labelled queries")
    parser.add_argument("--retriever", choices=["baseline", "vector", "hybrid", "understood"], default="baseline")
    # Tune on dev; test is run once at the end, or it stops being an honest score
    parser.add_argument("--split", type=Split, choices=list(Split), default=Split.DEV)
    args = parser.parse_args()

    openai_client = create_openai_client()
    try:
        method = methods(openai_client)[args.retriever]
        results = await run(method.retrieve, load_dataset(), K, method.embed)
    finally:
        await openai_client.close()
        await engine.dispose()
    report(results, args.split, args.retriever)


if __name__ == "__main__":
    # SelectorEventLoop: async psycopg does not work with ProactorEventLoop on Windows
    asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)
