import logging
from datetime import UTC, datetime, timedelta

from fastapi.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import InvalidCredentialsError, InvalidRefreshTokenError
from app.core.security import (
    DUMMY_PASSWORD_HASH,
    create_access_token,
    generate_refresh_token,
    hash_refresh_token,
    verify_password,
)
from app.repository import refresh_tokens as refresh_token_repository
from app.repository import users as user_repository
from app.schemas import TokenPair

logger = logging.getLogger(__name__)


async def login(db: AsyncSession, user_login: str, password: str) -> TokenPair:
    user = await user_repository.get_user(db, user_login)
    # Checking takes tens of milliseconds of CPU on purpose. In a separate thread it does not stop the event loop
    is_password_valid = await run_in_threadpool(
        verify_password, password, user.password_hash if user else DUMMY_PASSWORD_HASH
    )

    if not user or not is_password_valid:
        logger.warning("Failed login attempt for '%s'", user_login)
        raise InvalidCredentialsError()

    logger.info("User %s logged in", user.id)
    return await _issue_tokens(db, user.id)


async def refresh_tokens(db: AsyncSession, refresh_token: str) -> TokenPair:
    token_hash = hash_refresh_token(refresh_token)
    stored_token = await refresh_token_repository.get_refresh_token_for_update(db, token_hash)
    if not stored_token:
        raise InvalidRefreshTokenError()

    now = datetime.now(UTC)
    if stored_token.revoked_at:
        # A revoked token coming back means it may have been stolen, so every session of the user ends
        logger.warning("Revoked refresh token reused, ending all sessions of user %s", stored_token.user_id)
        await refresh_token_repository.revoke_all_user_tokens(db, stored_token.user_id, now)
        await db.commit()
        raise InvalidRefreshTokenError()

    if stored_token.expires_at <= now:
        raise InvalidRefreshTokenError()

    stored_token.revoked_at = now
    return await _issue_tokens(db, stored_token.user_id)


async def logout(db: AsyncSession, refresh_token: str) -> None:
    token_hash = hash_refresh_token(refresh_token)
    stored_token = await refresh_token_repository.get_refresh_token_for_update(db, token_hash)
    if stored_token and not stored_token.revoked_at:
        stored_token.revoked_at = datetime.now(UTC)
        await db.commit()


async def _issue_tokens(db: AsyncSession, user_id: int) -> TokenPair:
    refresh_token = generate_refresh_token()
    await refresh_token_repository.create_refresh_token(
        db,
        user_id,
        hash_refresh_token(refresh_token),
        datetime.now(UTC) + timedelta(days=settings.refresh_token_expire_days),
    )
    await db.commit()

    return TokenPair(access_token=create_access_token(user_id), refresh_token=refresh_token)
