import logging

from fastapi.concurrency import run_in_threadpool
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import UserAlreadyExistsError
from app.core.security import hash_password
from app.schemas import UserResponse
from app.repository import users as user_repository

logger = logging.getLogger(__name__)


async def create_user(db: AsyncSession, login: str, password: str) -> UserResponse:
    if await user_repository.get_user(db, login):
        raise UserAlreadyExistsError()

    # Hashing takes tens of milliseconds of CPU on purpose. In a separate thread it does not stop the event loop
    password_hash = await run_in_threadpool(hash_password, password)
    try:
        user = await user_repository.create_user(db, login, password_hash)
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        logger.warning("User '%s' was created by a concurrent request", login)
        raise UserAlreadyExistsError() from e
    return UserResponse.model_validate(user)