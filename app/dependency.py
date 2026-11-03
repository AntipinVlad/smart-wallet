import logging
from typing import Generator

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import APIKeyCookie
from sqlalchemy.orm import Session
from app.core.cookies import ACCESS_TOKEN_COOKIE
from app.core.security import decode_access_token
from app.database import SessionLocal
from app.models import User
from app.repository import users as users_repository

logger = logging.getLogger(__name__)

# auto_error=False: a missing cookie gets the same 401 as an invalid token, instead of FastAPI's default error
access_token_cookie = APIKeyCookie(name=ACCESS_TOKEN_COOKIE, auto_error=False)

def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_current_user(access_token: str | None = Depends(access_token_cookie),
                     db: Session = Depends(get_db)) -> User:
    if access_token is None:
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        user_id = decode_access_token(access_token)
    except (jwt.InvalidTokenError, ValueError) as e:
        logger.info("Rejected access token: %s", e)
        raise HTTPException(status_code=401, detail="Unauthorized") from e

    user = users_repository.get_user_by_id(db, user_id)

    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    return user