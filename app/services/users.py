import logging

from fastapi.concurrency import run_in_threadpool
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import UserAlreadyExistsError
from app.core.security import hash_password
from app.models import User
from app.repository import users as user_repository
from app.schemas import UserCreateRequest, UserResponse, UserUpdateRequest

logger = logging.getLogger(__name__)


async def create_user(db: AsyncSession, payload: UserCreateRequest) -> UserResponse:
    login = payload.login
    if await user_repository.get_user(db, login):
        raise UserAlreadyExistsError()

    # Slow on purpose: in a thread it does not block the event loop
    password_hash = await run_in_threadpool(hash_password, payload.password)
    try:
        user = await user_repository.create_user(db, login, password_hash, payload.timezone, payload.language)
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        logger.warning("User '%s' was created by a concurrent request", login)
        raise UserAlreadyExistsError() from e
    return UserResponse.model_validate(user)


async def update_user(db: AsyncSession, current_user: User, payload: UserUpdateRequest) -> UserResponse:
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(current_user, field, value)
    await db.commit()
    logger.info("User %s changed %s", current_user.id, changes)
    return UserResponse.model_validate(current_user)
