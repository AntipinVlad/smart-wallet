def test_create_user_success(client):
    # Act
    response = client.post("/api/v1/users", json={"login": "user"})

    # Assert
    assert response.status_code == 200
    assert response.json()["login"] == "user"
    assert response.json()["id"] is not None


def test_create_user_already_exists(client, make_user):
    make_user(login="user")

    # Act
    response = client.post("/api/v1/users", json={"login": "user"})

    # Assert
    assert response.status_code == 400


def test_create_user_login_taken_by_concurrent_request(client, make_user, monkeypatch):
    make_user(login="user")
    monkeypatch.setattr("app.repository.users.get_user", lambda *args: None)

    # Act
    response = client.post("/api/v1/users", json={"login": "user"})

    # Assert
    assert response.status_code == 400


def test_create_user_login_too_long(client):
    # Act
    response = client.post("/api/v1/users", json={"login": "a" * 128})

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
    response = client.get("/api/v1/users/me", headers={"Authorization": "Bearer not_exist"})

    # Assert
    assert response.status_code == 401


def test_get_me_without_token(client):
    # Act
    response = client.get("/api/v1/users/me")

    # Assert
    assert response.status_code == 401
