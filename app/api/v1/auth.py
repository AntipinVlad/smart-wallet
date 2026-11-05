from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.api.errors import error_responses
from app.core.cookies import RefreshTokenCookie, delete_auth_cookies, set_auth_cookies
from app.core.exceptions import InvalidCredentialsError, InvalidRefreshTokenError
from app.schemas import LoginRequest
from app.services import auth as auth_service

router = APIRouter()


@router.post("/auth/login", status_code=204, responses=error_responses(InvalidCredentialsError, validation=True))
async def login(payload: LoginRequest, response: Response, db: AsyncSession = Depends(get_db)) -> None:
    tokens = await auth_service.login(db, payload.login, payload.password)
    set_auth_cookies(response, tokens)


@router.post("/auth/refresh", status_code=204, responses=error_responses(InvalidRefreshTokenError, validation=True))
async def refresh(
    response: Response, refresh_token: RefreshTokenCookie = None, db: AsyncSession = Depends(get_db)
) -> None:
    if refresh_token is None:
        raise InvalidRefreshTokenError()

    tokens = await auth_service.refresh_tokens(db, refresh_token)
    set_auth_cookies(response, tokens)


@router.post("/auth/logout", status_code=204, responses=error_responses(validation=True))
async def logout(
    response: Response, refresh_token: RefreshTokenCookie = None, db: AsyncSession = Depends(get_db)
) -> None:
    if refresh_token is not None:
        await auth_service.logout(db, refresh_token)

    delete_auth_cookies(response)
