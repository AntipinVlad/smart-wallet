import logging

import pytest
from redis.asyncio import Redis

from app.core.config import settings
from tests.conftest import PASSWORD, assert_error

LOGIN_URL = "/api/v1/auth/login"


def login(client, user_login: str, password: str = PASSWORD):
    return client.post(LOGIN_URL, json={"login": user_login, "password": password})


@pytest.fixture()
def limits(monkeypatch):
    monkeypatch.setattr(settings, "login_rate_limit_per_ip", 4)
    monkeypatch.setattr(settings, "login_rate_limit_per_login", 2)
    monkeypatch.setattr(settings, "login_rate_limit_window_seconds", 60)


def test_login_attempts_are_limited_per_login(client, make_user, limits):
    make_user(login="user")
    for _ in range(2):
        assert login(client, "user", "wrong-password").status_code == 401

    # Act
    response = login(client, "user")

    # Assert
    assert_error(response, 429, "RATE_LIMIT_EXCEEDED")
    assert 1 <= int(response.headers["Retry-After"]) <= 60


def test_blocked_login_does_not_block_other_logins(client, make_user, limits):
    make_user(login="other")
    for _ in range(3):
        login(client, "user", "wrong-password")

    # Act
    response = login(client, "other")

    # Assert
    assert response.status_code == 204


def test_login_attempts_are_limited_per_ip(client, make_user, limits):
    make_user(login="last")
    for name in ("first", "second", "third", "fourth"):
        login(client, name, "wrong-password")

    # Act
    response = login(client, "last")

    # Assert
    assert_error(response, 429, "RATE_LIMIT_EXCEEDED")


def test_login_works_when_redis_is_unavailable(client, make_user, monkeypatch, caplog):
    make_user(login="user")
    unavailable = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.5)
    monkeypatch.setitem(client.app_state, "redis", unavailable)

    # Act
    with caplog.at_level(logging.ERROR):
        response = login(client, "user")

    # Assert
    assert response.status_code == 204
    assert any("rate limit" in record.getMessage().lower() for record in caplog.records)
