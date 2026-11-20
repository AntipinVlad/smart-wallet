import logging

import openai
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.enums import OperationType
from app.models import Operation
from app.models.operation import EMBEDDING_DIMENSIONS
from app.repository import operations as operations_repository

__all__ = ["EMBEDDING_DIMENSIONS", "embed_missing", "embed_operation", "request_embeddings"]

logger = logging.getLogger(__name__)


async def request_embeddings(openai_client: AsyncOpenAI, texts: list[str]) -> list[list[float]]:
    # The only place that talks to the model: the evals send exactly what production sends
    response = await openai_client.embeddings.create(
        model=settings.openai_embedding_model, input=texts, dimensions=EMBEDDING_DIMENSIONS
    )
    # By index, not by position in the list: each vector must land on its own text
    return [item.embedding for item in sorted(response.data, key=lambda item: item.index)]


def needs_embedding(operation: Operation) -> bool:
    return (
        bool(operation.description)
        and operation.type != OperationType.TRANSFER
        and operation.embedding_model != settings.openai_embedding_model
    )


async def embed_operation(db: AsyncSession, openai_client: AsyncOpenAI, operation_id: int) -> None:
    operation = await operations_repository.get_operation_by_id(db, operation_id)
    if operation is None:
        logger.warning("Operation %s to embed no longer exists", operation_id)
        return
    if not needs_embedding(operation):
        return

    try:
        [vector] = await request_embeddings(openai_client, [operation.description or ""])
    except openai.APIError as e:
        # Fail open: search finds the operation by text until the backfill embeds it
        logger.error("Operation %s not embedded, OpenAI request failed: %r", operation_id, e)
        return

    operation.embedding, operation.embedding_model = vector, settings.openai_embedding_model
    await db.commit()
    logger.info("Operation %s embedded with %s", operation_id, settings.openai_embedding_model)


async def embed_missing(db: AsyncSession, openai_client: AsyncOpenAI, batch_size: int) -> int:
    """Embeds every operation without a vector of the current model; an API error stops the run, done batches stay."""
    embedded = 0
    while operations := await operations_repository.operations_to_embed(
        db, settings.openai_embedding_model, batch_size
    ):
        vectors = await request_embeddings(openai_client, [operation.description or "" for operation in operations])
        for operation, vector in zip(operations, vectors, strict=True):
            operation.embedding, operation.embedding_model = vector, settings.openai_embedding_model
        # Per batch: a failure later does not lose what is already paid for
        await db.commit()
        embedded += len(operations)
        logger.info("Embedded %s operations, %s so far", len(operations), embedded)
    return embedded
