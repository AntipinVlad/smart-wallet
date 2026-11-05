from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import error_responses
from app.core.exceptions import NotAuthenticatedError, UserAlreadyExistsError
from app.api.deps import get_db, get_current_user
from app.models import User
from app.services import users as user_service
from app.schemas import UserCreateRequest, UserResponse

router = APIRouter()


@router.post("/users", response_model=UserResponse, status_code=201,
             responses=error_responses(UserAlreadyExistsError, validation=True))
async def create_user(payload: UserCreateRequest, db: AsyncSession = Depends(get_db)):
    return await user_service.create_user(db, payload.login, payload.password)

@router.get("/users/me", response_model=UserResponse, responses=error_responses(NotAuthenticatedError))
async def get_me(current_user: User = Depends(get_current_user)):
    return UserResponse.model_validate(current_user)
