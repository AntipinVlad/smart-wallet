from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.core.security import hash_password
from app.models import User
from tests.conftest import OTHER_REQUEST_SECONDS, PASSWORD, TIMEZONE, Pause, assert_error


def register(client, **payload):
    return client.post("/api/v1/users", json={"login": "user", "password": PASSWORD, "timezone": TIMEZONE} | payload)


def update_me(client, auth, user, **payload):
    return client.patch("/api/v1/users/me", json=payload, headers=auth(user))


def test_create_user(client, db_session):
    # Act
    response = register(client)

    # Assert
    assert response.status_code == 201
    user = db_session.scalars(select(User)).one()
    assert response.json() == {"id": user.id, "login": "user", "timezone": TIMEZONE}
    assert user.password_hash.startswith("$argon2id$")
    assert user.timezone == TIMEZONE


def test_create_user_stores_login_in_lower_case_without_spaces(client, db_session):
    # Act
    response = register(client, login="  Anna ")

    # Assert
    assert response.status_code == 201
    assert response.json()["login"] == "anna"
    assert db_session.scalars(select(User.login)).one() == "anna"


def test_create_user_login_taken_in_other_case(client, make_user):
    make_user(login="anna")

    # Act
    response = register(client, login="ANNA")

    # Assert
    assert_error(response, 409, "USER_ALREADY_EXISTS")


@pytest.mark.parametrize(
    "payload",
    [
        {"login": "user", "timezone": TIMEZONE},
        {"login": "user", "password": "short", "timezone": TIMEZONE},
        {"login": "a" * 128, "password": PASSWORD, "timezone": TIMEZONE},
        {"login": "   ", "password": PASSWORD, "timezone": TIMEZONE},
        {"login": "user", "password": PASSWORD},
        {"login": "user", "password": PASSWORD, "timezone": "Mars/Olympus"},
        {"login": "user", "password": PASSWORD, "timezone": "+02:00"},
    ],
    ids=[
        "without_password",
        "short_password",
        "long_login",
        "blank_login",
        "without_timezone",
        "unknown_timezone",
        "offset_instead_of_timezone",
    ],
)
def test_create_user_rejects_invalid_request(client, payload):
    # Act
    response = client.post("/api/v1/users", json=payload)

    # Assert
    assert_error(response, 422, "VALIDATION_ERROR")


@pytest.mark.parametrize("concurrent", [False, True], ids=["found_by_check", "found_by_unique_constraint"])
def test_create_user_login_taken(client, make_user, monkeypatch, concurrent):
    make_user(login="user")
    if concurrent:
        # Another request took the login after the check: only the unique constraint stops it
        monkeypatch.setattr("app.repository.users.get_user", AsyncMock(return_value=None))

    # Act
    response = register(client)

    # Assert
    assert_error(response, 409, "USER_ALREADY_EXISTS")


def test_get_me(client, make_user, auth):
    user = make_user()

    # Act
    response = client.get("/api/v1/users/me", headers=auth(user))

    # Assert
    assert response.status_code == 200
    assert response.json() == {"id": user.id, "login": user.login, "timezone": TIMEZONE}


def test_update_timezone(client, db_session, make_user, auth):
    user = make_user(timezone="UTC")

    # Act
    response = update_me(client, auth, user, timezone="America/New_York")

    # Assert
    assert response.status_code == 200
    assert response.json() == {"id": user.id, "login": user.login, "timezone": "America/New_York"}
    db_session.refresh(user)
    assert user.timezone == "America/New_York"


@pytest.mark.parametrize(
    "payload", [{}, {"timezone": "Mars/Olympus"}, {"timezone": None}], ids=["empty", "unknown_timezone", "null"]
)
def test_update_me_rejects_invalid_request(client, db_session, make_user, auth, payload):
    user = make_user()

    # Act
    response = update_me(client, auth, user, **payload)

    # Assert
    assert_error(response, 422, "VALIDATION_ERROR")
    db_session.refresh(user)
    assert user.timezone == TIMEZONE


def test_slow_password_hashing_does_not_block_other_requests(client, make_user, auth, monkeypatch, run_in_background):
    # Hashing is CPU-heavy: in the event loop it would stop other requests
    user = make_user()
    pause = Pause()
    monkeypatch.setattr("app.services.users.hash_password", pause.wrap(hash_password))

    # Act
    try:
        registration = run_in_background(register, client, login="new")
        assert pause.entered.wait(timeout=5)
        other = run_in_background(client.get, "/api/v1/users/me", headers=auth(user))
        other_response = other.result(timeout=OTHER_REQUEST_SECONDS)
    finally:
        pause.released.set()

    # Assert
    assert other_response.status_code == 200
    assert registration.result(timeout=10).status_code == 201
