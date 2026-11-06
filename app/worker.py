import asyncio
import logging

from openai import AsyncOpenAI
from taskiq import Context, SendTaskError, TaskiqDepends, TaskiqEvents, TaskiqState
from taskiq_redis import RedisStreamBroker

from app.ai.categorization import service as categorization
from app.core.config import settings
from app.core.logging import request_id_var, setup_logging
from app.database import SessionLocal, engine

logger = logging.getLogger(__name__)

QUEUE_NAME = "taskiq"
# Stored in every queued message: renaming the function must not orphan tasks already in Redis
CATEGORIZE_OPERATION_TASK = "categorize_operation"

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
    state.openai = AsyncOpenAI(
        api_key=settings.openai_api_key.get_secret_value(), timeout=settings.openai_timeout_seconds
    )


@broker.on_event(TaskiqEvents.WORKER_SHUTDOWN)
async def shutdown(state: TaskiqState) -> None:
    await state.openai.close()
    await engine.dispose()


@broker.task(task_name=CATEGORIZE_OPERATION_TASK)
async def categorize_operation_task(operation_id: int, context: Context = TaskiqDepends()) -> None:
    openai_client: AsyncOpenAI = context.state.openai
    token = request_id_var.set(context.message.labels.get("request_id"))
    try:
        async with SessionLocal() as db:
            await categorization.categorize_operation(db, openai_client, operation_id)
    finally:
        request_id_var.reset(token)


async def enqueue_categorization(operation_id: int) -> None:
    kicker = categorize_operation_task.kicker()
    request_id = request_id_var.get()
    if request_id is not None:
        kicker = kicker.with_labels(request_id=request_id)
    try:
        async with asyncio.timeout(settings.redis_timeout_seconds):
            await kicker.kiq(operation_id)
    # taskiq wraps broker errors such as a refused Redis connection in SendTaskError
    except (SendTaskError, TimeoutError) as e:
        # Fail open: the operation is saved, it only stays without a category
        logger.error("Operation %s not queued for categorization: %r", operation_id, e)
