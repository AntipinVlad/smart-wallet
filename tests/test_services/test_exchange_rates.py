import asyncio
from collections.abc import Awaitable, Callable
from datetime import date
from decimal import Decimal

import aiohttp
import pytest
from redis.asyncio import Redis
from sqlalchemy import select

from app.core.config import settings
from app.core.exceptions import ExchangeServiceUnavailableError
from app.database import SessionLocal, engine
from app.enums import CurrencyEnum
from app.models import ExchangeRate
from app.services.exchange_rates import LOCK_KEY, DailyRateLoader
from tests.conftest import REDIS_URL

DAY = date(2025, 3, 14)
OTHER_DAY = date(2025, 3, 15)
RATES = {CurrencyEnum.USD: Decimal(1), CurrencyEnum.UAH: Decimal("41.53"), CurrencyEnum.EUR: Decimal("0.92")}


class FakeRatesApi:
    """Stands in for exchange_service.get_daily_rates: counts requests and can hold or fail them."""

    def __init__(self) -> None:
        self.days: list[date] = []
        self.requested = asyncio.Event()
        self.gate: asyncio.Event | None = None
        self.errors: list[Exception] = []
        self.published = True
        self.during_request: Callable[[], Awaitable[None]] | None = None

    async def __call__(self, http_session: aiohttp.ClientSession, day: date) -> dict[CurrencyEnum, Decimal] | None:
        self.days.append(day)
        self.requested.set()
        if self.during_request is not None:
            await self.during_request()
        if self.gate is not None:
            await self.gate.wait()
        if self.errors:
            raise self.errors.pop(0)
        return RATES if self.published else None


@pytest.fixture()
def anyio_backend():
    # SelectorEventLoop: async psycopg does not work with ProactorEventLoop on Windows
    return "asyncio", {"loop_factory": asyncio.SelectorEventLoop}


@pytest.fixture()
def api(monkeypatch) -> FakeRatesApi:
    fake = FakeRatesApi()
    monkeypatch.setattr("app.services.exchange_service.get_daily_rates", fake)
    return fake


@pytest.fixture()
async def redis():
    client = Redis.from_url(REDIS_URL)
    yield client
    await client.aclose()


@pytest.fixture()
async def loader(redis):
    yield DailyRateLoader(redis, http_session=None)
    # Pooled connections belong to this test's event loop
    await engine.dispose()


async def ensure(loader: DailyRateLoader, *days: date) -> None:
    # A session per call: one AsyncSession cannot serve concurrent requests
    async with SessionLocal() as db:
        await loader.ensure_rates(db, set(days))


def stored(db_session) -> dict[tuple[date, CurrencyEnum], Decimal]:
    db_session.expire_all()
    return {(rate.day, rate.currency): rate.per_usd for rate in db_session.scalars(select(ExchangeRate))}


@pytest.mark.anyio
async def test_missing_day_is_loaded_with_all_currencies(loader, api, db_session):
    # Act
    await ensure(loader, DAY)

    # Assert
    assert api.days == [DAY]
    assert stored(db_session) == {(DAY, currency): rate for currency, rate in RATES.items()}


@pytest.mark.anyio
async def test_stored_day_is_not_loaded_again(loader, api):
    await ensure(loader, DAY)

    # Act
    await ensure(loader, DAY)

    # Assert
    assert api.days == [DAY]


@pytest.mark.anyio
async def test_only_missing_days_are_loaded(loader, api):
    await ensure(loader, DAY)

    # Act
    await ensure(loader, DAY, OTHER_DAY)

    # Assert
    assert api.days == [DAY, OTHER_DAY]


@pytest.mark.anyio
async def test_day_without_rates_is_not_requested_again(loader, api, db_session):
    api.published = False
    await ensure(loader, DAY)

    # Act
    await ensure(loader, DAY)

    # Assert
    assert api.days == [DAY]
    assert stored(db_session) == {}


@pytest.mark.anyio
async def test_day_before_history_loads_first_published_day(loader, api):
    # Act
    await ensure(loader, date(2023, 6, 1), settings.exchange_api_history_start)

    # Assert
    assert api.days == [settings.exchange_api_history_start]


class TestSingleFlight:
    """Requests in one process share one load of a day."""

    @pytest.mark.anyio
    async def test_concurrent_requests_load_day_once(self, loader, api, db_session):
        api.gate = asyncio.Event()
        requests = [asyncio.create_task(ensure(loader, DAY)) for _ in range(10)]
        await asyncio.wait_for(api.requested.wait(), timeout=5)

        # Act
        api.gate.set()
        await asyncio.gather(*requests)

        # Assert
        assert api.days == [DAY]
        assert len(stored(db_session)) == len(RATES)

    @pytest.mark.anyio
    async def test_failed_load_is_retried_by_next_request(self, loader, api, db_session):
        api.errors = [ExchangeServiceUnavailableError()]
        with pytest.raises(ExchangeServiceUnavailableError):
            await ensure(loader, DAY)

        # Act
        await ensure(loader, DAY)

        # Assert
        assert api.days == [DAY, DAY]
        assert len(stored(db_session)) == len(RATES)

    @pytest.mark.anyio
    async def test_cancelled_request_does_not_cancel_others(self, loader, api, db_session):
        api.gate = asyncio.Event()
        first = asyncio.create_task(ensure(loader, DAY))
        await asyncio.wait_for(api.requested.wait(), timeout=5)
        second = asyncio.create_task(ensure(loader, DAY))
        await asyncio.sleep(0.05)

        # Act
        first.cancel()
        api.gate.set()
        await second

        # Assert
        assert api.days == [DAY]
        assert len(stored(db_session)) == len(RATES)


class TestRedisLock:
    """Processes share one load of a day through a lock in Redis."""

    @pytest.mark.anyio
    async def test_waits_for_another_process_holding_lock(self, loader, api, redis, db_session):
        key = LOCK_KEY.format(day=DAY.isoformat())
        await redis.set(key, "other-process", ex=30)

        async def other_process_finishes():
            await asyncio.sleep(0.3)
            db_session.add_all(
                ExchangeRate(day=DAY, currency=currency, per_usd=rate) for currency, rate in RATES.items()
            )
            db_session.commit()
            await redis.delete(key)

        finishing = asyncio.create_task(other_process_finishes())

        # Act
        await ensure(loader, DAY)

        # Assert
        await finishing
        assert api.days == []

    @pytest.mark.anyio
    async def test_loads_day_when_lock_of_failed_process_expires(self, loader, api, redis, db_session):
        await redis.set(LOCK_KEY.format(day=DAY.isoformat()), "failed-process", px=300)

        # Act
        await ensure(loader, DAY)

        # Assert
        assert api.days == [DAY]
        assert len(stored(db_session)) == len(RATES)

    @pytest.mark.anyio
    async def test_loads_day_itself_when_lock_holder_is_too_slow(self, loader, api, redis, monkeypatch):
        monkeypatch.setattr(settings, "exchange_rate_wait_seconds", 0.3)
        await redis.set(LOCK_KEY.format(day=DAY.isoformat()), "slow-process", ex=30)

        # Act
        await ensure(loader, DAY)

        # Assert
        assert api.days == [DAY]

    @pytest.mark.anyio
    async def test_waiting_checks_database_less_and_less_often(self, api, redis, monkeypatch):
        monkeypatch.setattr(settings, "exchange_rate_wait_seconds", 1.5)
        await redis.set(LOCK_KEY.format(day=DAY.isoformat()), "slow-process", ex=30)
        sessions = 0

        def counting_session_factory():
            nonlocal sessions
            sessions += 1
            return SessionLocal()

        loader = DailyRateLoader(redis, http_session=None, session_factory=counting_session_factory)

        # Act
        await ensure(loader, DAY)

        # Assert: 15 checks at a fixed 0.1 seconds; 0.1, 0.2, 0.4, 0.8 seconds make 5, plus one to store the day
        assert sessions <= 7
        await engine.dispose()

    @pytest.mark.anyio
    async def test_lock_taken_by_another_process_is_not_released(self, loader, api, redis):
        key = LOCK_KEY.format(day=DAY.isoformat())

        async def lock_expires_and_another_process_takes_it():
            await redis.set(key, "other-process", ex=30)

        api.during_request = lock_expires_and_another_process_takes_it

        # Act
        await ensure(loader, DAY)

        # Assert
        assert await redis.get(key) == b"other-process"

    @pytest.mark.anyio
    async def test_loads_day_without_redis(self, api, db_session):
        unavailable = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.2, socket_timeout=0.2)
        loader = DailyRateLoader(unavailable, http_session=None)

        # Act
        await ensure(loader, DAY)

        # Assert
        assert api.days == [DAY]
        assert len(stored(db_session)) == len(RATES)
        await unavailable.aclose()
        await engine.dispose()
