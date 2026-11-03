from sqlalchemy import select

from app.models import User
from tests.conftest import PASSWORD


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
    assert response.status_code == 422


def test_create_user_short_password(client):
    # Act
    response = client.post("/api/v1/users", json={"login": "user", "password": "short"})

    # Assert
    assert response.status_code == 422


def test_create_user_already_exists(client, make_user):
    make_user(login="user")

    # Act
    response = client.post("/api/v1/users", json={"login": "user", "password": PASSWORD})

    # Assert
    assert response.status_code == 400


def test_create_user_login_taken_by_concurrent_request(client, make_user, monkeypatch):
    make_user(login="user")
    monkeypatch.setattr("app.repository.users.get_user", lambda *args: None)

    # Act
    response = client.post("/api/v1/users", json={"login": "user", "password": PASSWORD})

    # Assert
    assert response.status_code == 400


def test_create_user_login_too_long(client):
    # Act
    response = client.post("/api/v1/users", json={"login": "a" * 128, "password": PASSWORD})

    # Assert
    assert response.status_code == 422


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
    assert response.status_code == 401


def test_get_me_without_token(client):
    # Act
    response = client.get("/api/v1/users/me")

    # Assert
    assert response.status_code == 401
