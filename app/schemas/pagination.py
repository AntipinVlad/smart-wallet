import re
from enum import StrEnum

from fastapi import Query
from fastapi_pagination import Page as LibraryPage
from fastapi_pagination import Params

from app.core.config import settings


class PageParams(Params):
    # Sizes from Settings, not the library defaults
    page: int = Query(1, ge=1, description="Page number, from 1")
    size: int = Query(settings.default_page_size, ge=1, le=settings.max_page_size, description="Page size")


class Page[T](LibraryPage[T]):
    __params_type__ = PageParams


def sort_pattern(fields: type[StrEnum]) -> str:
    names = "|".join(re.escape(field) for field in fields)
    return rf"^({names})(,(asc|desc))?$"
