from datetime import datetime

from sqlalchemy.orm import Session

from app.models import RefreshToken


def create_refresh_token(db: Session, user_id: int, token_hash: str, expires_at: datetime) -> RefreshToken:
    refresh_token = RefreshToken(user_id=user_id, token_hash=token_hash, expires_at=expires_at)
    db.add(refresh_token)
    db.flush()
    return refresh_token


def get_refresh_token_for_update(db: Session, token_hash: str) -> RefreshToken | None:
    return db.query(RefreshToken).filter(RefreshToken.token_hash == token_hash).with_for_update().first()


def revoke_all_user_tokens(db: Session, user_id: int, revoked_at: datetime) -> None:
    db.query(RefreshToken).filter(
        RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)
    ).update({"revoked_at": revoked_at})
