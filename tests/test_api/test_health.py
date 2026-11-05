import asyncio
import logging

import pytest
from sqlalchemy.exc import OperationalError

from app.api.deps import get_db
from app.core.config import settings
from app.main import app
from tests.conftest import assert_error


class BrokenDatabaseSession:
    async def execute(self, *args, **kwargs):
        raise OperationalError("SELECT 1", {}, ConnectionRefusedError("connection refused"))


class SlowDatabaseSession:
    async def execute(self, *args, **kwargs):
        await asyncio.sleep(5)


@pytest.fixture()
def database_session(client):
    def _use(session) -> None:
        app.dependency_overrides[get_db] = lambda: session

    yield _use
    app.dependency_overrides.pop(get_db, None)


def test_live_does_not_need_database(client, database_session):
    # Liveness only says the process answers. If it checked the database, a database outage would make
    # Kubernetes restart every app container, which does not help and makes the recovery slower
    database_session(BrokenDatabaseSession())

    # Act
    response = client.get("/health/live")

    # Assert
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_when_database_answers(client):
    # Act
    response = client.get("/health/ready")

    # Assert
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize("session, logged_reason", [
    (BrokenDatabaseSession(), "connection refused"),
    (SlowDatabaseSession(), "TimeoutError"),
], ids=["database_is_down", "database_does_not_answer_in_time"])
def test_ready_when_database_is_unavailable(client, database_session, monkeypatch, caplog, session, logged_reason):
    monkeypatch.setattr(settings, "health_check_timeout_seconds", 0.05)
    database_session(session)

    # Act
    with caplog.at_level(logging.WARNING, logger="app.services.health"):
        response = client.get("/health/ready")

    # Assert
    assert_error(response, 503, "DATABASE_UNAVAILABLE")
    assert logged_reason not in response.text
    assert any(logged_reason in record.getMessage() for record in caplog.records)


@pytest.mark.parametrize("path", ["/health/live", "/health/ready"])
def test_health_checks_are_not_in_access_log(client, caplog, path):
    # Act
    with caplog.at_level(logging.INFO, logger="app.access"):
        client.get(path)

    # Assert
    assert [record for record in caplog.records if record.name == "app.access"] == []
