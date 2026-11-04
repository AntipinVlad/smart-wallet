from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RefreshToken


async def create_refresh_token(db: AsyncSession, user_id: int, token_hash: str, expires_at: datetime) -> RefreshToken:
    refresh_token = RefreshToken(user_id=user_id, token_hash=token_hash, expires_at=expires_at)
    db.add(refresh_token)
    await db.flush()
    return refresh_token


async def get_refresh_token_for_update(db: AsyncSession, token_hash: str) -> RefreshToken | None:
    return await db.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash).with_for_update())


async def revoke_all_user_tokens(db: AsyncSession, user_id: int, revoked_at: datetime) -> None:
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=revoked_at)
    )
