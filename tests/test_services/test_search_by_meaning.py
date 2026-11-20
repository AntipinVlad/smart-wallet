import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.ai.embeddings.service import EMBEDDING_DIMENSIONS
from app.core.config import settings
from app.database import SessionLocal, engine
from app.models import Operation
from app.services.search import find_operations_by_meaning, find_operations_hybrid, fuse_rankings
from tests.conftest import at_angle

QUERY = "кава"


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


def fake_openai():
    data = [SimpleNamespace(index=0, embedding=at_angle(0))]
    return SimpleNamespace(embeddings=SimpleNamespace(create=AsyncMock(return_value=SimpleNamespace(data=data))))


@pytest.fixture()
def make_embedded(db_session, make_operation):
    def _make_embedded(wallet, degrees: float, model: str | None = None, description: str | None = None, **fields):
        operation = make_operation(wallet, description=description or f"{degrees} degrees", **fields)
        operation.embedding = at_angle(degrees)
        operation.embedding_model = model or settings.openai_embedding_model
        db_session.commit()
        return operation

    return _make_embedded


@pytest.fixture()
def max_distance(monkeypatch):
    # 60 degrees: cosine distance 0.5
    monkeypatch.setattr(settings, "search_max_vector_distance", 0.5)


async def found(db, user, client=None, **params) -> list[int]:
    operations = await find_operations_by_meaning(db, client or fake_openai(), user.id, QUERY, limit=10, **params)
    return [operation.id for operation in operations]


@pytest.mark.anyio
@pytest.mark.usefixtures("max_distance")
async def test_closest_meaning_comes_first(db, make_user, make_wallet, make_embedded):
    user = make_user()
    wallet = make_wallet(user)
    far = make_embedded(wallet, 50)
    close = make_embedded(wallet, 10)
    middle = make_embedded(wallet, 30)

    # Act / Assert
    assert await found(db, user) == [close.id, middle.id, far.id]


@pytest.mark.anyio
@pytest.mark.usefixtures("max_distance")
async def test_operation_beyond_max_distance_is_not_found(db, make_user, make_wallet, make_embedded):
    user = make_user()
    wallet = make_wallet(user)
    close = make_embedded(wallet, 10)
    make_embedded(wallet, 80)

    # Act / Assert
    assert await found(db, user) == [close.id]


@pytest.mark.anyio
@pytest.mark.usefixtures("max_distance")
async def test_query_is_embedded_by_the_model_of_the_operations(db, make_user, make_wallet, make_embedded):
    user = make_user()
    make_embedded(make_wallet(user), 10)
    client = fake_openai()

    # Act
    await found(db, user, client)

    # Assert
    assert client.embeddings.create.await_args.kwargs == {
        "model": settings.openai_embedding_model,
        "input": [QUERY],
        "dimensions": EMBEDDING_DIMENSIONS,
    }


@pytest.mark.anyio
@pytest.mark.usefixtures("max_distance")
async def test_vectors_of_another_model_and_missing_vectors_are_skipped(
    db, make_user, make_wallet, make_embedded, make_operation
):
    user = make_user()
    wallet = make_wallet(user)
    current = make_embedded(wallet, 20)
    make_embedded(wallet, 10, model="an-older-embedding-model")
    make_operation(wallet, description="no vector yet")

    # Act / Assert
    assert await found(db, user) == [current.id]


@pytest.mark.anyio
@pytest.mark.usefixtures("max_distance")
async def test_operations_of_other_users_are_not_found(db, make_user, make_wallet, make_embedded):
    user = make_user(login="owner")
    own = make_embedded(make_wallet(user), 20)
    make_embedded(make_wallet(make_user(login="other")), 10)

    # Act / Assert
    assert await found(db, user) == [own.id]


@pytest.mark.anyio
@pytest.mark.usefixtures("max_distance")
async def test_wallet_and_date_filters_apply(db, make_user, make_wallet, make_embedded):
    user = make_user()
    card = make_wallet(user, name="card")
    inside = make_embedded(card, 20, created_at=datetime(2026, 1, 15, tzinfo=UTC))
    make_embedded(card, 10, created_at=datetime(2026, 2, 15, tzinfo=UTC))
    make_embedded(make_wallet(user, name="cash"), 10, created_at=datetime(2026, 1, 15, tzinfo=UTC))

    # Act
    ids = await found(
        db,
        user,
        wallet_id=card.id,
        date_from=datetime(2026, 1, 1, tzinfo=UTC),
        date_to=datetime(2026, 1, 31, tzinfo=UTC),
    )

    # Assert
    assert ids == [inside.id]


@pytest.mark.parametrize(
    ("rankings", "expected"),
    [
        # 3 is found by both: 1/63 + 1/61 beats 1st place in one list
        ([[1, 2, 3], [3, 4]], [3, 1, 2, 4]),
        # Equal scores keep the order of the first list
        ([[1], [2]], [1, 2]),
        ([[1, 2], []], [1, 2]),
    ],
    ids=["found_by_both_goes_up", "tie_keeps_first_list", "one_list_keeps_its_order"],
)
def test_rankings_are_fused_by_reciprocal_rank(rankings, expected):
    operations = {i: Operation(id=i) for ranking in rankings for i in ranking}

    # Act
    fused = fuse_rankings([[operations[i] for i in ranking] for ranking in rankings], k=60)

    # Assert
    assert [operation.id for operation in fused] == expected


@pytest.mark.anyio
@pytest.mark.usefixtures("max_distance")
async def test_hybrid_puts_operation_found_by_text_and_meaning_first(db, make_user, make_wallet, make_embedded):
    user = make_user()
    wallet = make_wallet(user)
    both = make_embedded(wallet, 10, description=QUERY, created_at=datetime(2026, 1, 10, tzinfo=UTC))
    # Newer, so the text search alone puts it above `both`
    text_only = make_embedded(
        wallet, 80, description=f"{QUERY} з молоком", created_at=datetime(2026, 1, 20, tzinfo=UTC)
    )
    meaning_only = make_embedded(wallet, 20, description="лате")
    make_embedded(wallet, 85, description="таксі")

    # Act
    operations = await find_operations_hybrid(db, fake_openai(), user.id, QUERY, limit=10)

    # Assert
    assert [operation.id for operation in operations] == [both.id, text_only.id, meaning_only.id]


@pytest.mark.anyio
@pytest.mark.usefixtures("max_distance")
async def test_hybrid_returns_no_more_than_limit(db, make_user, make_wallet, make_embedded):
    user = make_user()
    wallet = make_wallet(user)
    closest = make_embedded(wallet, 10)
    make_embedded(wallet, 20)

    # Act
    operations = await find_operations_hybrid(db, fake_openai(), user.id, QUERY, limit=1)

    # Assert
    assert [operation.id for operation in operations] == [closest.id]
