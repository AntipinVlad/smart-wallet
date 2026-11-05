import tomllib
from pathlib import Path

import pytest

from app.core.config import settings
from app.main import app, lifespan


@pytest.fixture()
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_lifespan_opens_shared_http_session_and_closes_it_on_shutdown():
    async with lifespan(app) as state:
        http_session = state["http_session"]

        # Assert
        assert not http_session.closed
        assert http_session.timeout.total == settings.exchange_api_timeout_seconds

    # Assert
    assert http_session.closed


def test_openapi_shows_name_and_version_from_pyproject():
    project = tomllib.loads((Path(__file__).parent.parent / "pyproject.toml").read_text(encoding="utf-8"))["project"]

    # Act
    info = app.openapi()["info"]

    # Assert
    assert (info["title"], info["version"]) == ("Smart Wallet", project["version"])
    assert project["name"] == "smart-wallet"
