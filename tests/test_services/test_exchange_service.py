from datetime import date
from decimal import Decimal

import aiohttp
import pytest

from app.core.config import settings
from app.core.exceptions import ExchangeRateNotFoundError, ExchangeServiceUnavailableError
from app.enums import CurrencyEnum
from app.services.exchange_service import get_daily_rates, get_exchange_rate


class FakeResponse:
    def __init__(self, data: dict, status: int = 200):
        self.data = data
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def raise_for_status(self):
        if self.status >= 400:
            raise aiohttp.ClientError(f"HTTP {self.status}")

    async def json(self):
        return self.data


class FakeSession:
    # No __aenter__/__aexit__: the shared session must not be opened or closed by the service
    def __init__(self, data: dict | None = None, status: int = 200):
        self.data = data
        self.status = status
        self.urls = []

    def get(self, url: str):
        self.urls.append(url)
        if self.data is None:
            raise aiohttp.ClientError("Network is unreachable")
        return FakeResponse(self.data, self.status)


@pytest.fixture()
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_get_exchange_rate_from_api():
    session = FakeSession({"usd": {"uah": 41.5}})

    # Act
    rate = await get_exchange_rate(session, CurrencyEnum.USD, CurrencyEnum.UAH)

    # Assert
    assert rate == Decimal("41.5")
    assert session.urls[0] == f"{settings.exchange_api_url}/currencies/usd.json"


@pytest.mark.anyio
async def test_get_exchange_rate_api_unavailable():
    session = FakeSession(None)

    # Act
    with pytest.raises(ExchangeServiceUnavailableError):
        await get_exchange_rate(session, CurrencyEnum.USD, CurrencyEnum.UAH)


@pytest.mark.anyio
@pytest.mark.parametrize("data", [{}, {"usd": {}}, {"usd": {"uah": "41.5"}}])
async def test_get_exchange_rate_not_found(data):
    session = FakeSession(data)

    # Act
    with pytest.raises(ExchangeRateNotFoundError):
        await get_exchange_rate(session, CurrencyEnum.USD, CurrencyEnum.UAH)


DAY = date(2025, 3, 14)
# Every supported currency, plus one the app does not support: it is skipped
API_RATES = {
    "usd": 1,
    "uah": 41.53,
    "eur": 0.92,
    "gbp": 0.77,
    "jpy": 149.3,
    "chf": 0.88,
    "cny": 7.24,
    "cad": 1.44,
    "aud": 1.59,
    "hkd": 7.77,
    "sgd": 1.34,
    "sek": 10.12,
    "nok": 10.68,
    "krw": 1452.6,
    "nzd": 1.75,
}
DAILY_RATES = {"date": "2025-03-14", "usd": API_RATES | {"btc": 0.000012}}


@pytest.mark.anyio
async def test_get_daily_rates_from_api():
    session = FakeSession(DAILY_RATES)

    # Act
    rates = await get_daily_rates(session, DAY)

    # Assert
    assert rates == {CurrencyEnum(code): Decimal(str(rate)) for code, rate in API_RATES.items()}
    assert session.urls == [f"{settings.exchange_api_history_url.format(day='2025-03-14')}/currencies/usd.json"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "session",
    [FakeSession({}, status=404), FakeSession(DAILY_RATES | {"date": "2025-03-13"})],
    ids=["day_not_published", "other_day_returned"],
)
async def test_get_daily_rates_for_day_without_rates(session):
    # Act
    rates = await get_daily_rates(session, DAY)

    # Assert
    assert rates is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    "session",
    [FakeSession(None), FakeSession({}, status=500)],
    ids=["network_error", "server_error"],
)
async def test_get_daily_rates_api_unavailable(session):
    # Act
    with pytest.raises(ExchangeServiceUnavailableError):
        await get_daily_rates(session, DAY)


@pytest.mark.anyio
async def test_get_daily_rates_without_currency():
    session = FakeSession({"date": "2025-03-14", "usd": {"usd": 1, "uah": 41.53}})

    # Act
    with pytest.raises(ExchangeRateNotFoundError):
        await get_daily_rates(session, DAY)
