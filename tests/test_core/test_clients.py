import pytest

from app.core.clients import create_http_session, create_openai_client, create_redis
from app.core.config import settings


@pytest.fixture()
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_http_session_times_out_like_exchange_api():
    # Act
    async with create_http_session() as http_session:
        # Assert
        assert http_session.timeout.total == settings.exchange_api_timeout_seconds


@pytest.mark.anyio
async def test_redis_times_out_on_connect_and_on_commands():
    # Act
    redis = create_redis()

    # Assert
    options = redis.connection_pool.connection_kwargs
    assert options["socket_timeout"] == settings.redis_timeout_seconds
    assert options["socket_connect_timeout"] == settings.redis_timeout_seconds
    await redis.aclose()


@pytest.mark.anyio
async def test_openai_client_has_timeout():
    # Act
    async with create_openai_client() as openai_client:
        # Assert
        assert openai_client.timeout == settings.openai_timeout_seconds
