import hashlib
import logging

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.exceptions import RateLimitExceededError

logger = logging.getLogger(__name__)

# One atomic step in Redis: the first hit starts the window, later hits do not extend it
_HIT_SCRIPT = """
local count = redis.call("INCR", KEYS[1])
if count == 1 then
    redis.call("EXPIRE", KEYS[1], ARGV[1])
end
return {count, redis.call("TTL", KEYS[1])}
"""


async def hit(redis: Redis, name: str, identity: str, limit: int, window_seconds: int) -> None:
    """Counts one request of `identity` in a fixed window; raises RateLimitExceededError over `limit`."""
    # Hashed: logins and IP addresses are personal data
    key = f"rate_limit:{name}:{hashlib.sha256(identity.encode()).hexdigest()}"
    try:
        count, ttl = await redis.eval(_HIT_SCRIPT, 1, key, window_seconds)
    except RedisError:
        # Fail open: without Redis the app keeps working, only without the limit
        logger.exception("Rate limit %s not checked, request allowed", name)
        return

    if count > limit:
        logger.warning("Rate limit %s exceeded", name)
        raise RateLimitExceededError(retry_after_seconds=ttl)
