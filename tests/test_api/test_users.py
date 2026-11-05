from unittest.mock import AsyncMock

from sqlalchemy import select

from app.core.security import hash_password
from app.models import User
from tests.conftest import assert_error, PASSWORD, Pause

# A request that does not wait for anything answers well within this time
OTHER_REQUEST_SECONDS = 2


def test_create_user_success(client):
    # Act
    response = client.post("/api/v1/users", json={"login": "user", "password": PASSWORD})

    # Assert
    assert response.status_code == 201
    assert response.json()["login"] == "user"
    assert response.json()["id"] is not None
    assert "password" not in response.text


def test_create_user_stores_password_hashed(client, db_session):
    # Act
    client.post("/api/v1/users", json={"login": "user", "password": PASSWORD})

    # Assert
    user = db_session.scalars(select(User)).one()
    assert PASSWORD not in user.password_hash
    assert user.password_hash.startswith("$argon2id$")


def test_create_user_without_password(client):
    # Act
    response = client.post("/api/v1/users", json={"login": "user"})

    # Assert
    assert_error(response, 422, "VALIDATION_ERROR")


def test_create_user_short_password(client):
    # Act
    response = client.post("/api/v1/users", json={"login": "user", "password": "short"})

    # Assert
    assert_error(response, 422, "VALIDATION_ERROR")


def test_create_user_already_exists(client, make_user):
    make_user(login="user")

    # Act
    response = client.post("/api/v1/users", json={"login": "user", "password": PASSWORD})

    # Assert
    assert_error(response, 409, "USER_ALREADY_EXISTS")


def test_create_user_login_taken_by_concurrent_request(client, make_user, monkeypatch):
    make_user(login="user")
    monkeypatch.setattr("app.repository.users.get_user", AsyncMock(return_value=None))

    # Act
    response = client.post("/api/v1/users", json={"login": "user", "password": PASSWORD})

    # Assert
    assert_error(response, 409, "USER_ALREADY_EXISTS")


def test_create_user_login_too_long(client):
    # Act
    response = client.post("/api/v1/users", json={"login": "a" * 128, "password": PASSWORD})

    # Assert
    assert_error(response, 422, "VALIDATION_ERROR")


def test_get_me_success(client, make_user, auth):
    user = make_user()

    # Act
    response = client.get("/api/v1/users/me", headers=auth(user))

    # Assert
    assert response.status_code == 200
    assert response.json() == {"id": user.id, "login": user.login}


def test_get_me_unknown_token(client):
    # Act
    response = client.get("/api/v1/users/me", headers={"Cookie": "access_token=not_exist"})

    # Assert
    assert_error(response, 401, "NOT_AUTHENTICATED")


def test_get_me_without_token(client):
    # Act
    response = client.get("/api/v1/users/me")

    # Assert
    assert_error(response, 401, "NOT_AUTHENTICATED")


def test_slow_password_hashing_does_not_block_other_requests(client, make_user, auth, monkeypatch,
                                                             run_in_background):
    # Hashing a password takes tens of milliseconds of CPU. In the event loop it would stop every other request
    user = make_user()
    pause = Pause()
    monkeypatch.setattr("app.services.users.hash_password", pause.wrap(hash_password))

    # Act
    try:
        registration = run_in_background(client.post, "/api/v1/users", json={"login": "new", "password": PASSWORD})
        assert pause.entered.wait(timeout=5)
        other = run_in_background(client.get, "/api/v1/users/me", headers=auth(user))
        other_response = other.result(timeout=OTHER_REQUEST_SECONDS)
    finally:
        pause.released.set()

    # Assert
    assert other_response.status_code == 200
    assert registration.result(timeout=10).status_code == 201
