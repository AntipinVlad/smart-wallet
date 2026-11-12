"""Tips by the facts they explain: the same numbers get the same answer without a paid model call."""

import hashlib
import json
import logging

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.ai.tips.facts import TipFacts
from app.ai.tips.service import INSTRUCTIONS, TipsAnswer
from app.core.config import settings
from app.enums import Language

logger = logging.getLogger(__name__)


def cache_key(user_id: int, facts: TipFacts, language: Language) -> str:
    # A new operation changes the facts, a new prompt or model changes the answer: either one is a new key
    source = {
        "model": settings.openai_model,
        "instructions": INSTRUCTIONS,
        "language": language,
        "facts": facts.model_dump(mode="json"),
    }
    digest = hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest()
    return f"tips:{user_id}:{digest}"


async def cached_tips(redis: Redis, key: str) -> TipsAnswer | None:
    try:
        value = await redis.get(key)
    except RedisError as e:
        # Fail open: without Redis every request asks the model
        logger.error("Tips cache not read, asking the model: %s", e)
        return None
    return None if value is None else TipsAnswer.model_validate_json(value)


async def cache_tips(redis: Redis, key: str, tips: TipsAnswer) -> None:
    try:
        await redis.set(key, tips.model_dump_json(), ex=settings.tips_cache_ttl_seconds)
    except RedisError as e:
        logger.error("Tips cache not written: %s", e)
