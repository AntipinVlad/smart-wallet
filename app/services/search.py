import hashlib
import logging
from array import array
from datetime import UTC, date, datetime
from typing import cast
from zoneinfo import ZoneInfo

import openai
from openai import AsyncOpenAI
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.embeddings.service import request_embeddings
from app.ai.search_query.service import needs_understanding, understand_query, understood_bounds
from app.core.config import settings
from app.core.exceptions import AiInvalidResponseError, RateLimitExceededError, WalletNotFoundError
from app.models import Operation, User
from app.repository import search as search_repository
from app.repository import wallets as wallet_repository
from app.schemas import OperationResponse, OperationSearchQuery
from app.services import rate_limit
from app.services.category_labels import match_categories
from app.services.periods import local_day

logger = logging.getLogger(__name__)


async def search(
    db: AsyncSession, openai_client: AsyncOpenAI, redis: Redis, current_user: User, query: OperationSearchQuery
) -> list[OperationResponse]:
    if query.wallet_id is not None and not await wallet_repository.get_wallet_by_id(
        db, current_user.id, query.wallet_id
    ):
        raise WalletNotFoundError(query.wallet_id)

    filters = (query.wallet_id, query.date_from, query.date_to)
    limits = [rate_limit.Limit("search_user", str(current_user.id), settings.search_rate_limit_per_user)]
    try:
        await rate_limit.hit(redis, "search", limits, settings.search_rate_limit_window_seconds)
    except RateLimitExceededError:
        # Not a 429: the text search costs nothing, so a frequent searcher only loses the meaning, not the answer
        operations = await find_operations(db, current_user.id, query.q, query.limit, *filters)
        return [OperationResponse.model_validate(operation) for operation in operations]

    # Without the model the search still works as typed: a long wait or a retry only delays the answer
    understanding_client = openai_client.with_options(
        timeout=settings.search_understanding_timeout_seconds, max_retries=0
    )
    text, date_from, date_to = query.q, query.date_from, query.date_to
    try:
        text, date_from, date_to = await understand_search(
            understanding_client,
            query.q,
            local_day(datetime.now(UTC), current_user.timezone),
            ZoneInfo(current_user.timezone),
            date_from,
            date_to,
        )
    except (openai.APIError, AiInvalidResponseError) as e:
        logger.error("Search of user %s not understood, searched as typed: %r", current_user.id, e)

    # The text search answers in milliseconds without OpenAI: waiting longer or retrying only delays it
    search_client = openai_client.with_options(timeout=settings.search_embedding_timeout_seconds, max_retries=0)
    try:
        operations = await find_operations_hybrid(
            db, search_client, current_user.id, text, query.limit, query.wallet_id, date_from, date_to, redis=redis
        )
    except openai.APIError as e:
        # Caught here, not in find_operations_hybrid: evals must see the failure, not a silent text search
        logger.error("Search of user %s fell back to text, OpenAI request failed: %r", current_user.id, e)
        operations = await find_operations(db, current_user.id, text, query.limit, query.wallet_id, date_from, date_to)
    return [OperationResponse.model_validate(operation) for operation in operations]


async def understand_search(
    openai_client: AsyncOpenAI,
    query: str,
    today: date,
    timezone: ZoneInfo,
    date_from: datetime | None,
    date_to: datetime | None,
) -> tuple[str, datetime | None, datetime | None]:
    """What to search and between which moments. Errors of the model are raised, as in find_operations_hybrid."""
    if not needs_understanding(query):
        return query, date_from, date_to
    answer = await understand_query(openai_client, query, today)
    understood_from, understood_to = understood_bounds(answer, timezone)
    # Both the filter and the question limit the days: the narrower side of each wins
    return answer.text, _latest(date_from, understood_from), _earliest(date_to, understood_to)


async def find_operations(
    db: AsyncSession,
    user_id: int,
    query: str,
    limit: int,
    wallet_id: int | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> list[Operation]:
    """The search itself: the endpoint and evals/run_search.py both call it."""
    categories = match_categories(query)
    operations = await search_repository.search_operations(
        db,
        user_id,
        query,
        categories,
        limit,
        min_word_similarity=settings.search_min_word_similarity,
        word_match_bonus=settings.search_word_match_bonus,
        category_match_bonus=settings.search_category_match_bonus,
        wallet_id=wallet_id,
        date_from=date_from,
        date_to=date_to,
    )
    # The query text stays out of the logs: it is what the user spends money on
    logger.info("Search of user %s matched categories %s, found %s operations", user_id, categories, len(operations))
    return operations


async def find_operations_by_meaning(
    db: AsyncSession,
    openai_client: AsyncOpenAI,
    user_id: int,
    query: str,
    limit: int,
    wallet_id: int | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    redis: Redis | None = None,
) -> list[Operation]:
    """Search by vectors of the descriptions; an OpenAI error is raised, so the caller decides what to fall back to.

    Without redis the query is embedded every time: evals measure fresh vectors.
    """
    query_vector = (
        await _query_vector(openai_client, query)
        if redis is None
        else await _cached_query_vector(openai_client, redis, query)
    )
    operations = await search_repository.search_operations_by_vector(
        db,
        user_id,
        query_vector,
        settings.openai_embedding_model,
        limit,
        max_distance=settings.search_max_vector_distance,
        wallet_id=wallet_id,
        date_from=date_from,
        date_to=date_to,
    )
    logger.info("Search by meaning of user %s found %s operations", user_id, len(operations))
    return operations


async def find_operations_hybrid(
    db: AsyncSession,
    openai_client: AsyncOpenAI,
    user_id: int,
    query: str,
    limit: int,
    wallet_id: int | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    redis: Redis | None = None,
) -> list[Operation]:
    """Both searches fused by rank; an OpenAI error is raised, as in find_operations_by_meaning."""
    by_text = await find_operations(db, user_id, query, limit, wallet_id, date_from, date_to)
    by_meaning = await find_operations_by_meaning(
        db, openai_client, user_id, query, limit, wallet_id, date_from, date_to, redis=redis
    )
    return fuse_rankings([by_text, by_meaning], settings.search_rrf_k)[:limit]


def fuse_rankings(rankings: list[list[Operation]], k: int) -> list[Operation]:
    """Reciprocal rank fusion: sum of 1 / (k + position) over the lists that found the operation.

    Positions, not scores: word_similarity and cosine distance have different scales and directions.
    """
    scores: dict[int, float] = {}
    operations: dict[int, Operation] = {}
    for ranking in rankings:
        for position, operation in enumerate(ranking, start=1):
            scores[operation.id] = scores.get(operation.id, 0.0) + 1 / (k + position)
            operations.setdefault(operation.id, operation)
    # sorted() is stable: equal scores keep the order of the first list
    return [operations[operation_id] for operation_id in sorted(scores, key=scores.__getitem__, reverse=True)]


async def _query_vector(openai_client: AsyncOpenAI, query: str) -> list[float]:
    [vector] = await request_embeddings(openai_client, [query])
    return vector


async def _cached_query_vector(openai_client: AsyncOpenAI, redis: Redis, query: str) -> list[float]:
    """Shared by all users: the vector depends on the text and the model, not on whose operations it is compared to."""
    # Hashed: the query text is personal; the model in the key keeps vectors of an older model out of the search
    digest = hashlib.sha256(f"{settings.openai_embedding_model}\n{query}".encode()).hexdigest()
    key = f"search:query_vector:{digest}"
    try:
        cached = await redis.get(key)
    except RedisError as e:
        # Fail open: without the cache the search only costs one more OpenAI call
        logger.error("Query vector cache not read, embedding the query: %s", e)
        cached = None
    if cached is not None:
        stored: array[float] = array("f")
        # create_redis does not decode responses: values come back as bytes
        stored.frombytes(cast(bytes, cached))
        return stored.tolist()

    vector = await _query_vector(openai_client, query)
    try:
        # float32, as pgvector stores it: a quarter of the size of JSON, nothing lost in the search
        await redis.set(key, array("f", vector).tobytes(), ex=settings.search_query_vector_ttl_seconds)
    except RedisError as e:
        logger.error("Query vector not cached: %s", e)
    return vector


def _latest(*moments: datetime | None) -> datetime | None:
    known = [moment for moment in moments if moment is not None]
    return max(known) if known else None


def _earliest(*moments: datetime | None) -> datetime | None:
    known = [moment for moment in moments if moment is not None]
    return min(known) if known else None
