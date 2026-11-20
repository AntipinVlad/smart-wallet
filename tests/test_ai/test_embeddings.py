import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx2
import openai
import pytest

from app.ai.embeddings.backfill import backfill
from app.ai.embeddings.service import EMBEDDING_DIMENSIONS, embed_missing, embed_operation
from app.core.config import settings
from app.database import SessionLocal, engine
from app.enums import OperationType
from app.models import Operation

OLD_MODEL = "an-older-embedding-model"


@pytest.fixture()
def anyio_backend():
    # SelectorEventLoop: async psycopg does not work with ProactorEventLoop on Windows
    return "asyncio", {"loop_factory": asyncio.SelectorEventLoop}


@pytest.fixture()
async def db():
    async with SessionLocal() as session:
        yield session
    # Pooled connections belong to this test's event loop
    await engine.dispose()


def vector(value: float) -> list[float]:
    return [value] * EMBEDDING_DIMENSIONS


def fake_openai(error: Exception | None = None):
    """Answers every text with a vector of its position + 1, as the API keeps the order of the input."""

    async def create(model: str, input: list[str], dimensions: int):
        if error is not None:
            raise error
        return SimpleNamespace(
            data=[SimpleNamespace(index=i, embedding=vector(i + 1)) for i in range(len(input))],
            model=model,
        )

    return SimpleNamespace(embeddings=SimpleNamespace(create=AsyncMock(side_effect=create)))


def api_error() -> openai.APIError:
    return openai.APIConnectionError(request=httpx2.Request("POST", "https://api.openai.com/v1/embeddings"))


def stored(db_session, operation: Operation) -> tuple[list[float] | None, str | None]:
    db_session.refresh(operation)
    return operation.embedding, operation.embedding_model


class TestEmbedOperation:
    @pytest.mark.anyio
    async def test_description_gets_vector_of_current_model(
        self, db, db_session, make_user, make_wallet, make_operation
    ):
        operation = make_operation(make_wallet(make_user()), description="кава зранку")
        client = fake_openai()

        # Act
        await embed_operation(db, client, operation.id)

        # Assert
        assert stored(db_session, operation) == (vector(1), settings.openai_embedding_model)
        request = client.embeddings.create.await_args.kwargs
        assert request == {
            "model": settings.openai_embedding_model,
            "input": ["кава зранку"],
            "dimensions": EMBEDDING_DIMENSIONS,
        }

    @pytest.mark.anyio
    @pytest.mark.parametrize("description", [None, ""], ids=["none", "empty"])
    async def test_operation_without_description_is_not_sent(
        self, db, db_session, make_user, make_wallet, make_operation, description
    ):
        operation = make_operation(make_wallet(make_user()), description=description)
        client = fake_openai()

        # Act
        await embed_operation(db, client, operation.id)

        # Assert
        assert stored(db_session, operation) == (None, None)
        client.embeddings.create.assert_not_awaited()

    @pytest.mark.anyio
    async def test_transfer_is_not_sent(self, db, db_session, make_user, make_wallet, make_operation):
        # Its description is written by the app, not the user: "Transfer to cash"
        operation = make_operation(
            make_wallet(make_user()), type=OperationType.TRANSFER, description="Transfer to cash"
        )
        client = fake_openai()

        # Act
        await embed_operation(db, client, operation.id)

        # Assert
        assert stored(db_session, operation) == (None, None)
        client.embeddings.create.assert_not_awaited()

    @pytest.mark.anyio
    async def test_vector_of_current_model_is_not_requested_again(
        self, db, db_session, make_user, make_wallet, make_operation
    ):
        operation = make_operation(make_wallet(make_user()), description="кава")
        await embed_operation(db, fake_openai(), operation.id)
        client = fake_openai()

        # Act
        await embed_operation(db, client, operation.id)

        # Assert
        client.embeddings.create.assert_not_awaited()

    @pytest.mark.anyio
    async def test_api_error_leaves_operation_without_vector(
        self, db, db_session, make_user, make_wallet, make_operation, caplog
    ):
        operation = make_operation(make_wallet(make_user()), description="кава")

        # Act
        with caplog.at_level(logging.ERROR, logger="app.ai.embeddings.service"):
            await embed_operation(db, fake_openai(error=api_error()), operation.id)

        # Assert
        assert stored(db_session, operation) == (None, None)
        assert [record.levelno for record in caplog.records if record.name == "app.ai.embeddings.service"] == [
            logging.ERROR
        ]

    @pytest.mark.anyio
    async def test_deleted_operation_is_logged(self, db, caplog):
        # Act
        with caplog.at_level(logging.WARNING, logger="app.ai.embeddings.service"):
            await embed_operation(db, fake_openai(), 999)

        # Assert
        assert "999" in caplog.text


class TestEmbedMissing:
    @pytest.mark.anyio
    async def test_embeds_operations_without_vector_or_with_vector_of_old_model(
        self, db, db_session, make_user, make_wallet, make_operation
    ):
        wallet = make_wallet(make_user())
        missing = make_operation(wallet, description="кава")
        old = make_operation(wallet, description="таксі")
        old.embedding, old.embedding_model = vector(9), OLD_MODEL
        current = make_operation(wallet, description="аптека")
        current.embedding, current.embedding_model = vector(9), settings.openai_embedding_model
        db_session.commit()
        client = fake_openai()

        # Act
        embedded = await embed_missing(db, client, batch_size=10)

        # Assert
        assert embedded == 2
        assert stored(db_session, missing)[1] == settings.openai_embedding_model
        assert stored(db_session, old)[1] == settings.openai_embedding_model
        assert stored(db_session, current) == (vector(9), settings.openai_embedding_model)

    @pytest.mark.anyio
    async def test_sends_operations_in_batches_and_keeps_vectors_with_their_texts(
        self, db, db_session, make_user, make_wallet, make_operation
    ):
        wallet = make_wallet(make_user())
        operations = [make_operation(wallet, description=f"кава {i}") for i in range(3)]
        client = fake_openai()

        # Act
        await embed_missing(db, client, batch_size=2)

        # Assert
        batches = [call.kwargs["input"] for call in client.embeddings.create.await_args_list]
        assert batches == [["кава 0", "кава 1"], ["кава 2"]]
        # The fake gives each text the vector of its position in its batch
        assert [stored(db_session, operation)[0] for operation in operations] == [vector(1), vector(2), vector(1)]

    @pytest.mark.anyio
    async def test_skips_transfers_and_operations_without_description(
        self, db, db_session, make_user, make_wallet, make_operation
    ):
        wallet = make_wallet(make_user())
        make_operation(wallet, description=None)
        make_operation(wallet, type=OperationType.TRANSFER, description="Transfer to cash")
        client = fake_openai()

        # Act
        embedded = await embed_missing(db, client, batch_size=10)

        # Assert
        assert embedded == 0
        client.embeddings.create.assert_not_awaited()

    @pytest.mark.anyio
    async def test_api_error_stops_the_run_and_keeps_finished_batches(
        self, db, db_session, make_user, make_wallet, make_operation
    ):
        wallet = make_wallet(make_user())
        first = make_operation(wallet, description="кава")
        second = make_operation(wallet, description="таксі")
        client = fake_openai()
        client.embeddings.create.side_effect = [
            SimpleNamespace(data=[SimpleNamespace(index=0, embedding=vector(1))]),
            api_error(),
        ]

        # Act
        with pytest.raises(openai.APIError):
            await embed_missing(db, client, batch_size=1)

        # Assert
        assert stored(db_session, first)[1] == settings.openai_embedding_model
        assert stored(db_session, second) == (None, None)


class TestBackfill:
    @pytest.mark.anyio
    async def test_embeds_missing_operations_with_new_client_and_closes_it(
        self, db_session, make_user, make_wallet, make_operation, monkeypatch
    ):
        operation = make_operation(make_wallet(make_user()), description="кава")
        client = fake_openai()
        client.close = AsyncMock()
        monkeypatch.setattr("app.ai.embeddings.backfill.create_openai_client", lambda: client)

        # Act
        embedded = await backfill()

        # Assert
        assert embedded == 1
        assert stored(db_session, operation)[1] == settings.openai_embedding_model
        client.close.assert_awaited_once()
