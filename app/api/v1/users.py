from fastapi import APIRouter
from fastapi.params import Depends
from sqlalchemy.orm import Session

from app.dependency import get_db, get_current_user
from app.models import User
from app.services import users as user_service
from app.schemas import UserRequest, UserResponse

router = APIRouter()


@router.post("/users", response_model=UserResponse)
def create_user(payload: UserRequest, db: Session = Depends(get_db)):
    return user_service.create_user(db, payload.login)

@router.get("/users/me", response_model=UserResponse)
def get_current_user(current_user: User = Depends(get_current_user)):
    return UserResponse.model_validate(current_user)
