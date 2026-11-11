"""Clients of external services, created the same way by the API and the worker."""

import aiohttp
from openai import AsyncOpenAI
from redis.asyncio import Redis

from app.core.config import settings


def create_http_session() -> aiohttp.ClientSession:
    # Called inside the event loop: aiohttp binds the session to the loop that creates it
    return aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=settings.exchange_api_timeout_seconds))


def create_redis() -> Redis:
    # Connects on the first command: the app starts even when Redis is down
    return Redis.from_url(
        settings.redis_url,
        socket_timeout=settings.redis_timeout_seconds,
        socket_connect_timeout=settings.redis_timeout_seconds,
    )


def create_openai_client() -> AsyncOpenAI:
    return AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value(), timeout=settings.openai_timeout_seconds)
