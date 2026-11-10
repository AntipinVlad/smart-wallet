import asyncio
import json
import logging
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from openai import AsyncOpenAI
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import select
from taskiq import TaskiqState

from app.ai.categorization.categories import ExpenseCategory
from app.ai.categorization.service import ExpenseCategorization
from app.core.config import settings
from app.database import engine
from app.models import ExchangeRate
from app.services.exchange_rates import DailyRateLoader
from app.worker import (
    CATEGORIZE_OPERATION_TASK,
    LOAD_EXCHANGE_RATES_TASK,
    QUEUE_NAME,
    broker,
    categorize_operation_task,
    enqueue_categorization,
    enqueue_exchange_rates,
    load_exchange_rates_task,
    shutdown,
    startup,
)
from tests.conftest import REDIS_URL, sync_redis


@pytest.fixture()
def anyio_backend():
    # SelectorEventLoop: async psycopg does not work with ProactorEventLoop on Windows
    return "asyncio", {"loop_factory": asyncio.SelectorEventLoop}


@pytest.fixture()
async def worker_resources():
    yield
    # Pooled connections belong to this test's event loop
    await engine.dispose()
    await broker.shutdown()


async def hang(*_):
    await asyncio.sleep(5)


def queued_messages(task_name: str) -> list[dict]:
    messages = [json.loads(fields[b"data"]) for _, fields in sync_redis.xrange(QUEUE_NAME)]
    return [message for message in messages if message["task_name"] == task_name]


def test_new_operation_is_queued_with_request_id(client, make_user, make_wallet, auth):
    user = make_user()
    wallet = make_wallet(user)

    # Act
    response = client.post(
        "/api/v1/operations/expense",
        json={"wallet_id": wallet.id, "amount": 50, "description": "Uber"},
        headers=auth(user) | {"X-Request-ID": "request-1"},
    )

    # Assert
    assert response.status_code == 201
    [message] = queued_messages(CATEGORIZE_OPERATION_TASK)
    assert message["args"] == [response.json()["id"]]
    assert message["labels"]["request_id"] == "request-1"


def test_new_operation_queues_exchange_rates_of_its_day_in_time_zone_of_user(client, make_user, make_wallet, auth):
    user = make_user()
    wallet = make_wallet(user)

    # Act
    response = client.post(
        "/api/v1/operations/income",
        # November 1, 00:30 in Kyiv
        json={"wallet_id": wallet.id, "amount": 50, "created_at": "2026-10-31T22:30:00Z"},
        headers=auth(user),
    )

    # Assert
    assert response.status_code == 201
    [message] = queued_messages(LOAD_EXCHANGE_RATES_TASK)
    assert message["args"] == ["2026-11-01"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "kick",
    [AsyncMock(side_effect=RedisConnectionError("Connection refused")), AsyncMock(side_effect=hang)],
    ids=["redis_unavailable", "redis_hangs"],
)
@pytest.mark.parametrize(
    "enqueue",
    [lambda: enqueue_categorization(7), lambda: enqueue_exchange_rates(date(2026, 10, 5))],
    ids=["categorization", "exchange_rates"],
)
async def test_queue_failure_is_logged_not_raised(monkeypatch, caplog, worker_resources, kick, enqueue):
    monkeypatch.setattr(broker, "kick", kick)
    monkeypatch.setattr(settings, "redis_timeout_seconds", 0.1)

    # Act
    with caplog.at_level(logging.ERROR, logger="app.worker"):
        await enqueue()

    # Assert
    assert [record.levelno for record in caplog.records if record.name == "app.worker"] == [logging.ERROR]


@pytest.mark.anyio
async def test_task_categorizes_with_worker_client_and_request_id(
    db_session, make_user, make_wallet, make_operation, caplog, worker_resources
):
    operation = make_operation(make_wallet(make_user()), description="Сільпо")
    parse = AsyncMock(
        return_value=SimpleNamespace(output_parsed=ExpenseCategorization(category=ExpenseCategory.FOOD_GROCERIES))
    )
    context = SimpleNamespace(
        state=SimpleNamespace(openai=SimpleNamespace(responses=SimpleNamespace(parse=parse))),
        message=SimpleNamespace(labels={"request_id": "request-1"}),
    )

    # Act
    with caplog.at_level(logging.INFO, logger="app.ai.categorization.service"):
        await categorize_operation_task(operation.id, context)

    # Assert
    db_session.refresh(operation)
    assert (operation.category, operation.subcategory) == ("food", "groceries")
    [record] = [record for record in caplog.records if record.name == "app.ai.categorization.service"]
    assert record.request_id == "request-1"


@pytest.mark.anyio
async def test_worker_startup_creates_openai_client_and_shutdown_closes_it(worker_resources):
    state = TaskiqState()

    # Act
    await startup(state)
    client = state.openai
    await shutdown(state)

    # Assert
    assert isinstance(client, AsyncOpenAI)
    assert client.timeout == settings.openai_timeout_seconds
    assert client.is_closed()


@pytest.mark.anyio
async def test_worker_startup_creates_rate_loader_and_shutdown_closes_its_session(worker_resources):
    state = TaskiqState()

    # Act
    await startup(state)
    loader, http_session = state.rate_loader, state.http_session
    await shutdown(state)

    # Assert
    assert isinstance(loader, DailyRateLoader)
    assert http_session.closed


@pytest.mark.anyio
async def test_task_loads_exchange_rates_of_day(db_session, daily_rates, worker_resources):
    redis = Redis.from_url(REDIS_URL)
    context = SimpleNamespace(
        state=SimpleNamespace(rate_loader=DailyRateLoader(redis, http_session=None)),
        message=SimpleNamespace(labels={}),
    )

    # Act
    await load_exchange_rates_task("2026-10-05", context)

    # Assert
    assert daily_rates.days == [date(2026, 10, 5)]
    assert {rate.day for rate in db_session.scalars(select(ExchangeRate))} == {date(2026, 10, 5)}
    await redis.aclose()
