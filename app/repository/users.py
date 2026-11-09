from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User


async def get_user(db: AsyncSession, login: str) -> User | None:
    return await db.scalar(select(User).where(User.login == login))


async def get_user_by_id(db: AsyncSession, user_id: int) -> User | None:
    return await db.get(User, user_id)


async def create_user(db: AsyncSession, login: str, password_hash: str, timezone: str) -> User:
    user = User(login=login, password_hash=password_hash, timezone=timezone)
    db.add(user)
    await db.flush()
    return user
