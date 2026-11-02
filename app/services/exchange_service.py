import logging
from decimal import Decimal

import aiohttp
from fastapi import HTTPException

from app.core.config import settings
from app.enum import CurrencyEnum

logger = logging.getLogger(__name__)


async def get_exchange_rate(base: CurrencyEnum, target: CurrencyEnum) -> Decimal:
    url = f"{settings.exchange_api_url}/currencies/{base}.json"
    timeout = aiohttp.ClientTimeout(total=settings.exchange_api_timeout_seconds)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as response:
                response.raise_for_status()
                data = await response.json()
    except (aiohttp.ClientError, TimeoutError, ValueError) as e:
        logger.exception("Failed to fetch exchange rate %s/%s", base, target)
        raise HTTPException(status_code=503, detail="Exchange rate service unavailable") from e

    rate = data.get(base, {}).get(target)
    if not isinstance(rate, (int, float)):
        logger.error("Exchange rate %s/%s not found, received: %r", base, target, rate)
        raise HTTPException(status_code=404, detail=f"Exchange rate {base}/{target} not found")

    return Decimal(rate)
