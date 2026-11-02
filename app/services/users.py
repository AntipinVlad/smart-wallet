import logging

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.schemas import UserResponse
from app.repository import users as user_repository

logger = logging.getLogger(__name__)


def create_user(db: Session, login: str) -> UserResponse:
    if user_repository.get_user(db, login):
        raise HTTPException(status_code=400, detail="User already exists")

    try:
        user = user_repository.create_user(db, login)
        db.commit()
    except IntegrityError as e:
        db.rollback()
        logger.warning("User '%s' was created by a concurrent request", login)
        raise HTTPException(status_code=400, detail="User already exists") from e
    return UserResponse.model_validate(user)