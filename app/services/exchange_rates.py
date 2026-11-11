import asyncio
import logging
import uuid
from datetime import UTC, date, datetime, timedelta

import aiohttp
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.database import SessionLocal
from app.repository import exchange_rates as exchange_rate_repository
from app.services import exchange_service

logger = logging.getLogger(__name__)

LOCK_KEY = "exchange_rate_lock:{day}"
MISSING_KEY = "exchange_rate_missing:{day}"
# Doubles while another process loads the day: a slow load is not checked in the database 10 times a second
POLL_FIRST_SECONDS = 0.1
POLL_MAX_SECONDS = 1.0
# A recent day may still be published; an old day without rates will stay without them
RECENT_DAYS = timedelta(days=2)
RECENT_MISSING_SECONDS = 60 * 60
OLD_MISSING_SECONDS = 30 * 24 * 60 * 60

# Deletes the lock only while it is ours: after it expires, another process may hold it
_RELEASE_SCRIPT = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
    return redis.call("DEL", KEYS[1])
end
return 0
"""


class DailyRateLoader:
    """Stores the exchange rates of days in the database, loading each day once for all requests and processes."""

    def __init__(
        self,
        redis: Redis,
        http_session: aiohttp.ClientSession,
        session_factory: async_sessionmaker[AsyncSession] = SessionLocal,
    ) -> None:
        self._redis = redis
        self._http_session = http_session
        self._session_factory = session_factory
        self._loading: dict[date, asyncio.Task[None]] = {}
        self._api_slots = asyncio.Semaphore(settings.exchange_api_max_concurrency)

    async def ensure_rates(self, db: AsyncSession, days: set[date]) -> None:
        days = {max(day, settings.exchange_api_history_start) for day in days}
        missing = days - await exchange_rate_repository.get_stored_days(db, days)
        await asyncio.gather(*(self._load_once(day) for day in sorted(missing)))

    async def _load_once(self, day: date) -> None:
        # Single flight: concurrent requests of this process wait for one load of the day
        task = self._loading.get(day)
        if task is None:
            task = asyncio.create_task(self._load(day))
            self._loading[day] = task
            # On failure too: the next request tries again
            task.add_done_callback(lambda done: self._forget(day, done))
        # A cancelled request must not cancel the load that other requests wait for
        await asyncio.shield(task)

    def _forget(self, day: date, task: asyncio.Task[None]) -> None:
        if self._loading.get(day) is task:
            del self._loading[day]

    async def _load(self, day: date) -> None:
        # Redis lock: processes wait for one load of the day
        loop = asyncio.get_running_loop()
        deadline = loop.time() + settings.exchange_rate_wait_seconds
        delay = POLL_FIRST_SECONDS
        while not await self._is_loaded(day):
            token = uuid.uuid4().hex
            try:
                locked = await self._redis.set(
                    LOCK_KEY.format(day=day), token, nx=True, ex=settings.exchange_rate_lock_seconds
                )
            except RedisError as e:
                logger.error("Lock for exchange rates of %s not taken, loading without it: %s", day, e)
                await self._fetch_and_store(day)
                return
            if locked:
                try:
                    await self._fetch_and_store(day)
                finally:
                    await self._release_lock(day, token)
                return
            if loop.time() >= deadline:
                logger.warning("Exchange rates of %s are loaded by another process too long, loading them here", day)
                await self._fetch_and_store(day)
                return
            await asyncio.sleep(min(delay, deadline - loop.time()))
            delay = min(delay * 2, POLL_MAX_SECONDS)

    async def _is_loaded(self, day: date) -> bool:
        async with self._session_factory() as db:
            if await exchange_rate_repository.get_stored_days(db, {day}):
                return True
        try:
            return bool(await self._redis.exists(MISSING_KEY.format(day=day)))
        except RedisError:
            # Logged when the lock is taken: Redis is asked for both at once
            return False

    async def _fetch_and_store(self, day: date) -> None:
        async with self._api_slots:
            rates = await exchange_service.get_daily_rates(self._http_session, day)
        if rates is None:
            await self._mark_missing(day)
            return
        async with self._session_factory() as db:
            await exchange_rate_repository.add_rates(db, day, rates)
            await db.commit()
        logger.info("Exchange rates of %s stored", day)

    async def _mark_missing(self, day: date) -> None:
        recent = day >= datetime.now(UTC).date() - RECENT_DAYS
        seconds = RECENT_MISSING_SECONDS if recent else OLD_MISSING_SECONDS
        try:
            await self._redis.set(MISSING_KEY.format(day=day), 1, ex=seconds)
        except RedisError as e:
            logger.error("Day %s without exchange rates not remembered, it will be requested again: %s", day, e)

    async def _release_lock(self, day: date, token: str) -> None:
        try:
            await self._redis.eval(_RELEASE_SCRIPT, 1, LOCK_KEY.format(day=day), token)
        except RedisError as e:
            # The lock expires by itself
            logger.error("Lock for exchange rates of %s not released: %s", day, e)
