"""Embeds operations created before 1.5.0, missed while OpenAI was down, or embedded by an older model.

Run: uv run python -m app.ai.embeddings.backfill
"""

import asyncio
import logging

from app.ai.embeddings.service import embed_missing
from app.core.clients import create_openai_client
from app.core.config import settings
from app.core.logging import setup_logging
from app.database import SessionLocal, engine

logger = logging.getLogger(__name__)


async def backfill() -> int:
    openai_client = create_openai_client()
    try:
        async with SessionLocal() as db:
            embedded = await embed_missing(db, openai_client, settings.embedding_backfill_batch_size)
    finally:
        await openai_client.close()
    logger.info("Backfill done: %s operations embedded with %s", embedded, settings.openai_embedding_model)
    return embedded


async def main() -> None:
    setup_logging(settings.log_level, settings.log_format)
    try:
        await backfill()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    # SelectorEventLoop: async psycopg does not work with ProactorEventLoop on Windows
    asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)
