from decimal import Decimal

import aiohttp
import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.enum import CurrencyEnum
from app.services.exchange_service import get_exchange_rate


class FakeResponse:
    def __init__(self, data: dict):
        self.data = data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def raise_for_status(self):
        pass

    async def json(self):
        return self.data


class FakeSession:
    # No __aenter__/__aexit__: the session is shared by the whole app, the service must not open or close it
    def __init__(self, data: dict | None = None):
        self.data = data
        self.urls = []

    def get(self, url: str):
        self.urls.append(url)
        if self.data is None:
            raise aiohttp.ClientError("Network is unreachable")
        return FakeResponse(self.data)


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
    with pytest.raises(HTTPException) as error:
        await get_exchange_rate(session, CurrencyEnum.USD, CurrencyEnum.UAH)

    # Assert
    assert error.value.status_code == 503


@pytest.mark.anyio
@pytest.mark.parametrize("data", [{}, {"usd": {}}, {"usd": {"uah": "41.5"}}])
async def test_get_exchange_rate_not_found(data):
    session = FakeSession(data)

    # Act
    with pytest.raises(HTTPException) as error:
        await get_exchange_rate(session, CurrencyEnum.USD, CurrencyEnum.UAH)

    # Assert
    assert error.value.status_code == 404
