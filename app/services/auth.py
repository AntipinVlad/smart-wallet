import logging
from datetime import datetime, timedelta, UTC

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
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


def login(db: Session, user_login: str, password: str) -> TokenPair:
    user = user_repository.get_user(db, user_login)
    is_password_valid = verify_password(password, user.password_hash if user else DUMMY_PASSWORD_HASH)

    if not user or not is_password_valid:
        logger.warning("Failed login attempt for '%s'", user_login)
        raise HTTPException(status_code=401, detail="Invalid login or password")

    logger.info("User %s logged in", user.id)
    return _issue_tokens(db, user.id)


def refresh_tokens(db: Session, refresh_token: str) -> TokenPair:
    stored_token = refresh_token_repository.get_refresh_token_for_update(db, hash_refresh_token(refresh_token))
    if not stored_token:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    now = datetime.now(UTC)
    if stored_token.revoked_at:
        # A revoked token coming back means it may have been stolen, so every session of the user ends
        logger.warning("Revoked refresh token reused, ending all sessions of user %s", stored_token.user_id)
        refresh_token_repository.revoke_all_user_tokens(db, stored_token.user_id, now)
        db.commit()
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    if stored_token.expires_at <= now:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    stored_token.revoked_at = now
    return _issue_tokens(db, stored_token.user_id)


def logout(db: Session, refresh_token: str) -> None:
    stored_token = refresh_token_repository.get_refresh_token_for_update(db, hash_refresh_token(refresh_token))
    if stored_token and not stored_token.revoked_at:
        stored_token.revoked_at = datetime.now(UTC)
        db.commit()


def _issue_tokens(db: Session, user_id: int) -> TokenPair:
    refresh_token = generate_refresh_token()
    refresh_token_repository.create_refresh_token(
        db,
        user_id,
        hash_refresh_token(refresh_token),
        datetime.now(UTC) + timedelta(days=settings.refresh_token_expire_days)
    )
    db.commit()

    return TokenPair(access_token=create_access_token(user_id), refresh_token=refresh_token)
