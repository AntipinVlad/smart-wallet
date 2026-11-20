from datetime import datetime

from sqlalchemy import ColumnElement, Select, and_, case, false, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Operation, Wallet

# No Ukrainian dictionary in Postgres: 'simple' only lowercases, so word forms are left to word_similarity
TEXT_SEARCH_CONFIG = "simple"


async def search_operations(
    db: AsyncSession,
    user_id: int,
    query: str,
    categories: list[tuple[str, str | None]],
    limit: int,
    min_word_similarity: float,
    word_match_bonus: float,
    category_match_bonus: float,
    wallet_id: int | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> list[Operation]:
    description = func.coalesce(Operation.description, "")
    similarity = func.word_similarity(query, description)
    word_match = func.to_tsvector(TEXT_SEARCH_CONFIG, description).bool_op("@@")(
        func.plainto_tsquery(TEXT_SEARCH_CONFIG, query)
    )
    category_match = _category_match(categories)
    score = (
        similarity
        + case((word_match, word_match_bonus), else_=0.0)
        + case((category_match, category_match_bonus), else_=0.0)
    )

    statement = _own_operations(user_id, wallet_id, date_from, date_to).where(
        or_(similarity >= min_word_similarity, word_match, category_match)
    )
    # Equal scores: the newer operation first, then id so the order never changes between requests
    statement = statement.order_by(score.desc(), Operation.created_at.desc(), Operation.id.desc()).limit(limit)
    return list((await db.scalars(statement)).all())


async def search_operations_by_vector(
    db: AsyncSession,
    user_id: int,
    query_vector: list[float],
    model: str,
    limit: int,
    max_distance: float,
    wallet_id: int | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> list[Operation]:
    # Exact distances over the user's operations, no HNSW: an approximate index searches the whole table
    # and the user filter afterwards can leave nothing, while one user has few enough rows to scan
    distance = Operation.embedding.cosine_distance(query_vector)
    statement = (
        _own_operations(user_id, wallet_id, date_from, date_to)
        # Vectors of another model live in another space: their distance to the query means nothing
        .where(Operation.embedding_model == model)
        .where(distance <= max_distance)
        .order_by(distance, Operation.created_at.desc(), Operation.id.desc())
        .limit(limit)
    )
    return list((await db.scalars(statement)).all())


def _own_operations(
    user_id: int, wallet_id: int | None, date_from: datetime | None, date_to: datetime | None
) -> Select[Operation]:
    statement = select(Operation).join(Wallet, Operation.wallet_id == Wallet.id).where(Wallet.user_id == user_id)
    if wallet_id is not None:
        statement = statement.where(Operation.wallet_id == wallet_id)
    if date_from is not None:
        statement = statement.where(Operation.created_at >= date_from)
    if date_to is not None:
        statement = statement.where(Operation.created_at <= date_to)
    return statement


def _category_match(categories: list[tuple[str, str | None]]) -> ColumnElement[bool]:
    if not categories:
        return false()
    return or_(
        *(
            Operation.category == category
            if subcategory is None
            else and_(Operation.category == category, Operation.subcategory == subcategory)
            for category, subcategory in categories
        )
    )
