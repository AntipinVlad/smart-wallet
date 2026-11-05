import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import DatabaseUnavailableError

logger = logging.getLogger(__name__)


async def check_database(db: AsyncSession) -> None:
    try:
        # Without a timeout a hanging database would make the check hang too, and the probe would only
        # see its own timeout instead of a clear 503
        async with asyncio.timeout(settings.health_check_timeout_seconds):
            await db.execute(text("SELECT 1"))
    except (SQLAlchemyError, OSError, TimeoutError) as e:
        logger.warning("Database health check failed: %r", e)
        raise DatabaseUnavailableError() from e
