import logging
from collections.abc import AsyncIterator
from typing import TypedDict

import aiohttp
import jwt
from fastapi import Depends, Request
from fastapi.security import APIKeyCookie
from openai import AsyncOpenAI
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cookies import ACCESS_TOKEN_COOKIE
from app.core.exceptions import NotAuthenticatedError
from app.core.security import decode_access_token
from app.database import SessionLocal
from app.models import User
from app.repository import users as users_repository

logger = logging.getLogger(__name__)

# auto_error=False: a missing cookie gets our 401, not FastAPI's default error
access_token_cookie = APIKeyCookie(name=ACCESS_TOKEN_COOKIE, auto_error=False)


class AppState(TypedDict):
    http_session: aiohttp.ClientSession
    redis: Redis
    openai: AsyncOpenAI


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as db:
        yield db


def get_http_session(request: Request) -> aiohttp.ClientSession:
    http_session: aiohttp.ClientSession = request.state.http_session
    return http_session


def get_redis(request: Request) -> Redis:
    redis: Redis = request.state.redis
    return redis


def get_openai_client(request: Request) -> AsyncOpenAI:
    openai_client: AsyncOpenAI = request.state.openai
    return openai_client


async def get_current_user(
    access_token: str | None = Depends(access_token_cookie), db: AsyncSession = Depends(get_db)
) -> User:
    if access_token is None:
        raise NotAuthenticatedError()

    try:
        user_id = decode_access_token(access_token)
    except (jwt.InvalidTokenError, ValueError) as e:
        logger.info("Rejected access token: %s", e)
        raise NotAuthenticatedError() from e

    user = await users_repository.get_user_by_id(db, user_id)

    if not user:
        raise NotAuthenticatedError()

    return user
