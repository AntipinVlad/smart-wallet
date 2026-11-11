import logging
from datetime import date
from decimal import Decimal

import aiohttp

from app.core.config import settings
from app.core.exceptions import ExchangeRateNotFoundError, ExchangeServiceUnavailableError
from app.enums import CurrencyEnum

logger = logging.getLogger(__name__)


async def get_exchange_rate(http_session: aiohttp.ClientSession, base: CurrencyEnum, target: CurrencyEnum) -> Decimal:
    url = f"{settings.exchange_api_url}/currencies/{base}.json"
    try:
        # The session is shared: only the response is closed here
        async with http_session.get(url) as response:
            response.raise_for_status()
            data = await response.json()
    except (aiohttp.ClientError, TimeoutError, ValueError) as e:
        logger.exception("Failed to fetch exchange rate %s/%s", base, target)
        raise ExchangeServiceUnavailableError() from e

    rate = data.get(base, {}).get(target)
    if not isinstance(rate, (int, float)):
        logger.error("Exchange rate %s/%s not found, received: %r", base, target, rate)
        raise ExchangeRateNotFoundError(base, target)

    return Decimal(rate)


async def get_daily_rates(http_session: aiohttp.ClientSession, day: date) -> dict[CurrencyEnum, Decimal] | None:
    """Units of every currency for one US dollar on the day. None if the API has no rates for the day."""
    url = f"{settings.exchange_api_history_url.format(day=day.isoformat())}/currencies/usd.json"
    try:
        async with http_session.get(url) as response:
            if response.status == 404:
                logger.warning("Exchange API has no rates for %s", day)
                return None
            response.raise_for_status()
            data = await response.json()
    except (aiohttp.ClientError, TimeoutError, ValueError) as e:
        logger.exception("Failed to fetch exchange rates for %s", day)
        raise ExchangeServiceUnavailableError() from e

    if data.get("date") != day.isoformat():
        logger.warning("Exchange API returned rates of %r for %s", data.get("date"), day)
        return None

    usd_rates = data.get(CurrencyEnum.USD, {})
    rates = {}
    for currency in CurrencyEnum:
        rate = usd_rates.get(currency)
        if not isinstance(rate, int | float):
            logger.error("Exchange rate usd/%s for %s not found, received: %r", currency, day, rate)
            raise ExchangeRateNotFoundError(CurrencyEnum.USD, currency)
        # Through str: Decimal(41.53) keeps the binary error of the float
        rates[currency] = Decimal(str(rate))
    return rates
