import hashlib
import logging
from collections.abc import Sequence
from typing import NamedTuple

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.exceptions import RateLimitExceededError

logger = logging.getLogger(__name__)

# One atomic call for all keys: the first hit starts the window, later hits do not extend it
_HIT_SCRIPT = """
local result = {}
for _, key in ipairs(KEYS) do
    local count = redis.call("INCR", key)
    if count == 1 then
        redis.call("EXPIRE", key, ARGV[1])
    end
    table.insert(result, count)
    table.insert(result, redis.call("TTL", key))
end
return result
"""


class Limit(NamedTuple):
    name: str
    identity: str
    max_hits: int


async def hit(redis: Redis, label: str, limits: Sequence[Limit], window_seconds: int) -> None:
    """Counts one request for every limit in a fixed window; raises RateLimitExceededError if one is over."""
    # Hashed: logins and IP addresses are personal data
    keys = [f"rate_limit:{limit.name}:{hashlib.sha256(limit.identity.encode()).hexdigest()}" for limit in limits]
    try:
        values = await redis.eval(_HIT_SCRIPT, len(keys), *keys, window_seconds)
    except RedisError as e:
        # Fail open: without Redis the app keeps working, only without the limit
        logger.error("Rate limit %s not checked, request allowed: %s", label, e)
        return

    exceeded = [
        (limit.name, ttl)
        for limit, count, ttl in zip(limits, values[::2], values[1::2], strict=True)
        if count > limit.max_hits
    ]
    if exceeded:
        logger.warning("Rate limit exceeded: %s", ", ".join(name for name, _ in exceeded))
        raise RateLimitExceededError(retry_after_seconds=max(ttl for _, ttl in exceeded))
