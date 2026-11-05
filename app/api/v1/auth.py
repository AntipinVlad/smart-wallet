from fastapi import APIRouter, Depends, Request, Response
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_redis
from app.api.errors import error_responses
from app.core.config import settings
from app.core.cookies import RefreshTokenCookie, delete_auth_cookies, set_auth_cookies
from app.core.exceptions import InvalidCredentialsError, InvalidRefreshTokenError, RateLimitExceededError
from app.schemas import LoginRequest
from app.services import auth as auth_service
from app.services import rate_limit

router = APIRouter()


async def limit_login_attempts(request: Request, payload: LoginRequest, redis: Redis = Depends(get_redis)) -> None:
    # Behind nginx this is the client address only with uvicorn --proxy-headers
    ip = request.client.host if request.client else "unknown"
    window = settings.login_rate_limit_window_seconds
    await rate_limit.hit(redis, "login_ip", ip, settings.login_rate_limit_per_ip, window)
    # Not per login alone: then anyone could lock another user out
    await rate_limit.hit(redis, "login_ip_login", f"{ip}:{payload.login}", settings.login_rate_limit_per_login, window)


@router.post(
    "/auth/login",
    status_code=204,
    dependencies=[Depends(limit_login_attempts)],
    responses=error_responses(InvalidCredentialsError, RateLimitExceededError, validation=True),
)
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
