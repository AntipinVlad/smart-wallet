import asyncio
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Self
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.search_query.service import SearchQueryAnswer
from app.core.config import settings
from app.database import SessionLocal, engine
from app.models import Operation, User
from evals.run_search import (
    EVAL_TODAY,
    Dataset,
    EvalQuery,
    Group,
    Intent,
    QueryResult,
    Split,
    evaluate,
    load_dataset,
    methods,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
    run,
    summarize,
)
from tests.conftest import at_angle


@pytest.fixture()
def anyio_backend():
    # SelectorEventLoop: async psycopg does not work with ProactorEventLoop on Windows
    return "asyncio", {"loop_factory": asyncio.SelectorEventLoop}


@pytest.fixture()
async def db():
    async with SessionLocal() as session:
        yield session
        await session.rollback()
    # Pooled connections belong to this test's event loop
    await engine.dispose()


@dataclass
class DatasetBuilder:
    """A valid dataset by default: every test changes only what it checks."""

    operations: list[dict[str, Any]] = field(default_factory=list)
    queries: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def coffee(cls) -> Self:
        return (
            cls()
            .operation("coffee_1", "кава")
            .operation("coffee_2", "лате в кав'ярні")
            .operation("taxi", "таксі додому", category="transport.taxi")
            .query("кава", ["coffee_1", "coffee_2"], split=Split.DEV)
            .query("coffee", ["coffee_1", "coffee_2"], split=Split.TEST)
        )

    def operation(self, key: str, description: str, category: str = "food.restaurants", **fields: Any) -> Self:
        row = {
            "key": key,
            "type": "expense",
            "amount": "85.00",
            "currency": "uah",
            "description": description,
            "category": category,
            "date": "2026-10-15",
        }
        self.operations.append(row | fields)
        return self

    def query(
        self,
        text: str,
        expected: list[str],
        group: Group = Group.BROAD,
        intent: Intent = Intent.ALL,
        split: Split = Split.DEV,
    ) -> Self:
        self.queries.append({"query": text, "group": group, "intent": intent, "split": split, "expected": expected})
        return self

    def load(self, tmp_path: Path) -> Dataset:
        operations_path, queries_path = tmp_path / "operations.jsonl", tmp_path / "queries.jsonl"
        for path, rows in ((operations_path, self.operations), (queries_path, self.queries)):
            path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")
        return load_dataset(operations_path, queries_path)


def by_description(*words: str):
    """A retriever that returns the user's operations whose description contains a word, in the order of the words."""

    async def retrieve(db: AsyncSession, user_id: int, query: str, limit: int) -> list[int]:
        rows = (await db.execute(select(Operation.id, Operation.description))).all()
        return [row.id for word in words for row in rows if word in (row.description or "")][:limit]

    return retrieve


def result(found: list[str], expected: set[str], group=Group.BROAD, intent=Intent.ALL, split=Split.DEV, k=5):
    query = EvalQuery(text="q", group=group, intent=intent, split=split, expected=frozenset(expected))
    return QueryResult(query=query, found=found, k=k)


class TestMetrics:
    def test_recall_is_share_of_expected_operations_found(self):
        assert recall_at_k(["a", "x", "b", "y", "z"], {"a", "b", "c", "d"}, k=5) == 0.5

    def test_recall_ignores_results_after_k(self):
        assert recall_at_k(["x", "y", "a"], {"a"}, k=2) == 0.0

    def test_reciprocal_rank_is_inverse_position_of_first_expected_operation(self):
        assert reciprocal_rank(["x", "y", "z", "a", "b"], {"a", "b"}) == 0.25

    def test_reciprocal_rank_is_zero_when_no_expected_operation_is_found(self):
        assert reciprocal_rank(["x", "y"], {"a"}) == 0.0

    def test_precision_is_share_of_returned_operations_that_are_expected(self):
        assert precision_at_k(["a", "x", "b", "y"], {"a", "b"}, k=10) == 0.5

    def test_precision_does_not_punish_a_short_answer(self):
        assert precision_at_k(["a"], {"a"}, k=10) == 1.0

    def test_precision_ignores_results_after_k(self):
        assert precision_at_k(["a", "x", "y"], {"a"}, k=1) == 1.0

    def test_precision_of_empty_answer_is_zero(self):
        # Otherwise returning nothing would beat an honest search on precision
        assert precision_at_k([], {"a"}, k=10) == 0.0


class TestLoadDataset:
    def test_loads_operations_and_queries(self, tmp_path):
        dataset = DatasetBuilder.coffee().load(tmp_path)

        assert [operation.key for operation in dataset.operations] == ["coffee_1", "coffee_2", "taxi"]
        query = dataset.queries[0]
        assert (query.text, query.group, query.intent, query.split) == ("кава", Group.BROAD, Intent.ALL, Split.DEV)
        assert query.expected == {"coffee_1", "coffee_2"}

    def test_query_expecting_unknown_operation_is_rejected(self, tmp_path):
        builder = DatasetBuilder.coffee().query("таксі", ["taxi_typo"])

        with pytest.raises(ValueError, match="taxi_typo"):
            builder.load(tmp_path)

    def test_duplicate_operation_key_is_rejected(self, tmp_path):
        builder = DatasetBuilder.coffee().operation("coffee_1", "ще кава")

        with pytest.raises(ValueError, match="coffee_1"):
            builder.load(tmp_path)

    def test_category_of_wrong_type_is_rejected(self, tmp_path):
        builder = DatasetBuilder.coffee().operation("salary", "зарплата", category="salary.salary")

        with pytest.raises(ValueError, match=r"salary.salary"):
            builder.load(tmp_path)

    def test_query_without_expected_operations_is_rejected(self, tmp_path):
        builder = DatasetBuilder.coffee().query("щось", [])

        with pytest.raises(ValueError, match="щось"):
            builder.load(tmp_path)

    def test_group_missing_from_one_split_is_rejected(self, tmp_path):
        builder = DatasetBuilder.coffee().query("віддпустка", ["taxi"], group=Group.SEMANTIC, split=Split.DEV)

        with pytest.raises(ValueError, match="semantic"):
            builder.load(tmp_path)

    def test_bundled_dataset_is_valid(self):
        dataset = load_dataset()

        assert {query.group for query in dataset.queries} == set(Group)
        assert {query.intent for query in dataset.queries} == set(Intent)


class TestEvaluate:
    @pytest.mark.anyio
    async def test_retriever_results_are_translated_to_operation_keys(self, db, tmp_path):
        dataset = DatasetBuilder.coffee().load(tmp_path)

        results = await evaluate(db, by_description("лате", "кава"), dataset, k=5)

        assert [result.found for result in results] == [["coffee_2", "coffee_1"], ["coffee_2", "coffee_1"]]
        assert results[0].recall == 1.0
        assert results[0].reciprocal_rank == 1.0

    @pytest.mark.anyio
    async def test_retriever_gets_eval_user_and_k_as_limit(self, db, tmp_path):
        dataset = DatasetBuilder.coffee().load(tmp_path)
        calls = []

        async def retrieve(db: AsyncSession, user_id: int, query: str, limit: int) -> list[int]:
            calls.append((user_id, query, limit))
            return []

        await evaluate(db, retrieve, dataset, k=3)

        user_ids = {user_id for user_id, _, _ in calls}
        assert len(user_ids) == 1
        owner = await db.scalar(select(func.count()).select_from(User).where(User.id.in_(user_ids)))
        assert owner == 1
        assert [(query, limit) for _, query, limit in calls] == [("кава", 3), ("coffee", 3)]

    @pytest.mark.anyio
    async def test_operation_of_another_user_fails_the_run(self, db, tmp_path, make_user, make_wallet, make_operation):
        foreign = make_operation(make_wallet(make_user("other")), description="кава")
        dataset = DatasetBuilder.coffee().load(tmp_path)

        async def retrieve(db: AsyncSession, user_id: int, query: str, limit: int) -> list[int]:
            return [foreign.id]

        with pytest.raises(ValueError, match="another user"):
            await evaluate(db, retrieve, dataset, k=5)

    @pytest.mark.anyio
    async def test_query_expecting_more_operations_than_k_is_rejected(self, db, tmp_path):
        dataset = DatasetBuilder.coffee().load(tmp_path)

        with pytest.raises(ValueError, match="кава"):
            await evaluate(db, by_description("кава"), dataset, k=1)

    @pytest.mark.anyio
    async def test_run_leaves_no_data_behind(self, tmp_path, db_session):
        dataset = DatasetBuilder.coffee().load(tmp_path)

        results = await run(by_description("кава"), dataset, k=5)

        assert len(results) == 2
        assert db_session.scalar(select(func.count()).select_from(Operation)) == 0
        assert db_session.scalar(select(func.count()).select_from(User)) == 0
        await engine.dispose()


def fake_openai():
    """Coffee texts point one way, everything else at a right angle: what a real model does with meaning."""

    async def create(model: str, input: list[str], dimensions: int):
        coffee = ("кав", "лате", "coffee")
        vectors = [at_angle(0 if any(word in text for word in coffee) else 90) for text in input]
        return SimpleNamespace(data=[SimpleNamespace(index=i, embedding=v) for i, v in enumerate(vectors)])

    return SimpleNamespace(embeddings=SimpleNamespace(create=AsyncMock(side_effect=create)))


class TestMethods:
    @pytest.mark.anyio
    async def test_baseline_is_the_search_of_the_endpoint(self, db, tmp_path):
        dataset = DatasetBuilder.coffee().load(tmp_path)

        results = await evaluate(db, methods(fake_openai())["baseline"].retrieve, dataset, k=5)

        assert results[0].found == ["coffee_1", "coffee_2"]

    @pytest.mark.anyio
    async def test_vector_search_runs_on_operations_embedded_for_the_run(self, db, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "search_max_vector_distance", 0.5)
        dataset = DatasetBuilder.coffee().load(tmp_path)
        method = methods(fake_openai())["vector"]

        results = await evaluate(db, method.retrieve, dataset, k=5, embed=method.embed)

        # "coffee" has no common letter with "кава": only the vectors find it
        assert {query.query.text: set(query.found) for query in results} == {
            "кава": {"coffee_1", "coffee_2"},
            "coffee": {"coffee_1", "coffee_2"},
        }

    @pytest.mark.anyio
    async def test_hybrid_finds_what_either_search_finds(self, db, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "search_max_vector_distance", 0.5)
        dataset = DatasetBuilder.coffee().load(tmp_path)
        method = methods(fake_openai())["hybrid"]

        results = await evaluate(db, method.retrieve, dataset, k=5, embed=method.embed)

        # "кава" is found by the text, "coffee" only by the vectors
        assert {query.query.text: set(query.found) for query in results} == {
            "кава": {"coffee_1", "coffee_2"},
            "coffee": {"coffee_1", "coffee_2"},
        }

    @pytest.mark.anyio
    async def test_understood_searches_the_subject_within_the_days_of_fixed_today(self, db, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "search_max_vector_distance", 0.5)
        dataset = (
            DatasetBuilder.coffee()
            .operation("coffee_september", "кава", date="2026-09-15")
            .query("скільки кави в жовтні", ["coffee_1", "coffee_2"], group=Group.QUESTION, split=Split.DEV)
            .query("кава у вересні", ["coffee_september"], group=Group.QUESTION, split=Split.TEST)  # noqa: RUF001
            .load(tmp_path)
        )
        client = fake_openai()
        answer = SearchQueryAnswer(text="кава", date_from=date(2026, 10, 1), date_to=date(2026, 10, 31))
        client.responses = SimpleNamespace(parse=AsyncMock(return_value=SimpleNamespace(output_parsed=answer)))
        method = methods(client)["understood"]

        results = await evaluate(db, method.retrieve, dataset, k=5, embed=method.embed)

        assert set(results[2].found) == {"coffee_1", "coffee_2"}
        # Fixed, so a run next month scores the same questions the same way
        assert client.responses.parse.await_args.kwargs["input"].startswith(f"Today: {EVAL_TODAY.isoformat()}")

    def test_baseline_needs_no_vectors(self):
        assert methods(fake_openai())["baseline"].embed is None


class TestSummarize:
    def test_scores_are_averaged_by_group_and_intent_within_split(self):
        results = [
            result(["a", "x"], {"a", "b"}, group=Group.BROAD, intent=Intent.ALL),
            result(["a", "b"], {"a", "b"}, group=Group.BROAD, intent=Intent.ALL),
            result(["x", "a"], {"a"}, group=Group.SEMANTIC, intent=Intent.ONE),
            result(["x"], {"a"}, group=Group.BROAD, intent=Intent.ALL, split=Split.TEST),
        ]

        scores = summarize(results, Split.DEV)

        assert [(s.group, s.intent, s.queries, s.recall, s.mrr, s.precision) for s in scores] == [
            (Group.BROAD, Intent.ALL, 2, 0.75, 1.0, 0.75),
            (Group.SEMANTIC, Intent.ONE, 1, 1.0, 0.5, 0.5),
        ]
