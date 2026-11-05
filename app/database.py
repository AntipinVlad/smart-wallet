from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

# The same postgresql+psycopg URL works for async: psycopg 3 has both modes, Alembic keeps using the sync one
engine = create_async_engine(settings.database_url)

# expire_on_commit=False: after commit the objects keep their values. Otherwise reading any attribute
# would load it from the database again, and in async code that implicit query fails
SessionLocal = async_sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass
