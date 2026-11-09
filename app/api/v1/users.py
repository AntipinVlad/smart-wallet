from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.api.errors import error_responses
from app.core.exceptions import NotAuthenticatedError, UserAlreadyExistsError
from app.models import User
from app.schemas import UserCreateRequest, UserResponse, UserUpdateRequest
from app.services import users as user_service

router = APIRouter()


@router.post(
    "/users",
    response_model=UserResponse,
    status_code=201,
    responses=error_responses(UserAlreadyExistsError, validation=True),
)
async def create_user(payload: UserCreateRequest, db: AsyncSession = Depends(get_db)) -> UserResponse:
    return await user_service.create_user(db, payload)


@router.get("/users/me", response_model=UserResponse, responses=error_responses(NotAuthenticatedError))
async def get_me(current_user: User = Depends(get_current_user)) -> UserResponse:
    return UserResponse.model_validate(current_user)


@router.patch(
    "/users/me", response_model=UserResponse, responses=error_responses(NotAuthenticatedError, validation=True)
)
async def update_me(
    payload: UserUpdateRequest, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
) -> UserResponse:
    return await user_service.update_user(db, current_user, payload)
