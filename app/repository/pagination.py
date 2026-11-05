from collections.abc import Callable, Mapping
from typing import Any

from fastapi_pagination.ext.sqlalchemy import apaginate
from sqlalchemy import ColumnElement, Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.schemas.pagination import Page, PageParams

SortColumn = InstrumentedAttribute[Any] | ColumnElement[Any]


def apply_sort[M](query: Select[M], sort: str, columns: Mapping[str, SortColumn], tiebreaker: SortColumn) -> Select[M]:
    field, _, direction = sort.partition(",")
    column = columns[field]
    # The tiebreaker keeps rows with equal values in one order, so pages do not repeat or skip them
    if direction == "desc":
        return query.order_by(column.desc(), tiebreaker.desc())
    return query.order_by(column.asc(), tiebreaker.asc())


async def paginate[M, T](db: AsyncSession, query: Select[M], params: PageParams, item: Callable[[M], T]) -> Page[T]:
    page: Page[T] = await apaginate(db, query, params, transformer=lambda rows: [item(row) for row in rows])
    return page
