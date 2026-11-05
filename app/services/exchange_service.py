import logging
from decimal import Decimal

import aiohttp

from app.core.config import settings
from app.core.exceptions import ExchangeRateNotFoundError, ExchangeServiceUnavailableError
from app.enums import CurrencyEnum

logger = logging.getLogger(__name__)


async def get_exchange_rate(http_session: aiohttp.ClientSession, base: CurrencyEnum,
                            target: CurrencyEnum) -> Decimal:
    url = f"{settings.exchange_api_url}/currencies/{base}.json"
    try:
        # The session is shared and closed in lifespan, so only the response is closed here
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
