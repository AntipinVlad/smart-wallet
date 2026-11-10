import asyncio
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from typing import Any

from openai import AsyncOpenAI
from taskiq import AsyncTaskiqDecoratedTask, Context, SendTaskError, TaskiqDepends, TaskiqEvents, TaskiqState
from taskiq_redis import RedisStreamBroker

from app.ai.categorization import service as categorization
from app.core.clients import create_http_session, create_openai_client, create_redis
from app.core.config import settings
from app.core.logging import request_id_var, setup_logging
from app.database import SessionLocal, engine
from app.services.exchange_rates import DailyRateLoader

logger = logging.getLogger(__name__)

QUEUE_NAME = "taskiq"
# Stored in every queued message: renaming the function must not orphan tasks already in Redis
CATEGORIZE_OPERATION_TASK = "categorize_operation"
LOAD_EXCHANGE_RATES_TASK = "load_exchange_rates"

broker = RedisStreamBroker(
    settings.redis_url,
    queue_name=QUEUE_NAME,
    # A new consumer group reads the stream from the start, so tasks queued before the first worker run are kept
    consumer_id="0",
    maxlen=10_000,
    # No socket_timeout: the worker blocks on XREADGROUP for 2 s and the broker does not retry a timed out read
    socket_connect_timeout=settings.redis_timeout_seconds,
)


@broker.on_event(TaskiqEvents.WORKER_STARTUP)
async def startup(state: TaskiqState) -> None:
    setup_logging(settings.log_level, settings.log_format)
    state.openai = create_openai_client()
    state.http_session = create_http_session()
    state.redis = create_redis()
    # The same loader as in the API: its Redis lock keeps the worker and the API from loading one day twice
    state.rate_loader = DailyRateLoader(state.redis, state.http_session)


@broker.on_event(TaskiqEvents.WORKER_SHUTDOWN)
async def shutdown(state: TaskiqState) -> None:
    await state.openai.close()
    await state.http_session.close()
    await state.redis.aclose()
    await engine.dispose()


@broker.task(task_name=CATEGORIZE_OPERATION_TASK)
async def categorize_operation_task(operation_id: int, context: Context = TaskiqDepends()) -> None:
    openai_client: AsyncOpenAI = context.state.openai
    with _request_id_of(context):
        async with SessionLocal() as db:
            await categorization.categorize_operation(db, openai_client, operation_id)


@broker.task(task_name=LOAD_EXCHANGE_RATES_TASK)
async def load_exchange_rates_task(day: str, context: Context = TaskiqDepends()) -> None:
    rate_loader: DailyRateLoader = context.state.rate_loader
    with _request_id_of(context):
        async with SessionLocal() as db:
            await rate_loader.ensure_rates(db, {date.fromisoformat(day)})


@contextmanager
def _request_id_of(context: Context) -> Iterator[None]:
    """Logs of the task carry the request id of the API request that queued it."""
    token = request_id_var.set(context.message.labels.get("request_id"))
    try:
        yield
    finally:
        request_id_var.reset(token)


async def enqueue_categorization(operation_id: int) -> None:
    error = await _enqueue(categorize_operation_task, operation_id)
    if error is not None:
        # Fail open: the operation is saved, it only stays without a category
        logger.error("Operation %s not queued for categorization: %r", operation_id, error)


async def enqueue_exchange_rates(day: date) -> None:
    error = await _enqueue(load_exchange_rates_task, day.isoformat())
    if error is not None:
        # Fail open: the first report of this day loads the rates itself
        logger.error("Exchange rates of %s not queued for loading: %r", day, error)


async def _enqueue(task: AsyncTaskiqDecoratedTask[Any, Any], *args: Any) -> Exception | None:
    """Queues the task; returns the error instead of raising it, since callers go on without the task."""
    kicker = task.kicker()
    request_id = request_id_var.get()
    if request_id is not None:
        kicker = kicker.with_labels(request_id=request_id)
    try:
        async with asyncio.timeout(settings.redis_timeout_seconds):
            await kicker.kiq(*args)
    # taskiq wraps broker errors such as a refused Redis connection in SendTaskError
    except (SendTaskError, TimeoutError) as e:
        return e
    return None
