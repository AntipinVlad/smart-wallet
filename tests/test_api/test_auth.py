from datetime import datetime, timedelta, UTC

import jwt

from app.core.config import settings
from app.core.cookies import ACCESS_TOKEN_COOKIE, REFRESH_TOKEN_COOKIE
from app.core.security import create_access_token, hash_refresh_token, verify_password
from app.models import RefreshToken, User
from tests.conftest import assert_error, PASSWORD, Pause

REFRESH_URL = "/api/v1/auth/refresh"
LOGOUT_URL = "/api/v1/auth/logout"
# A request that does not wait for anything answers well within this time
OTHER_REQUEST_SECONDS = 2


def login(client, user_login: str = "user", password: str = PASSWORD):
    return client.post("/api/v1/auth/login", json={"login": user_login, "password": password})


def refresh_token_of(response) -> str:
    return response.cookies[REFRESH_TOKEN_COOKIE]


def with_refresh_token(token: str) -> dict[str, str]:
    # An explicit Cookie header replaces the client cookie jar, so a test can send any token it holds
    return {"Cookie": f"{REFRESH_TOKEN_COOKIE}={token}"}


def with_access_token(token: str) -> dict[str, str]:
    return {"Cookie": f"{ACCESS_TOKEN_COOKIE}={token}"}


def refresh(client, token: str):
    return client.post(REFRESH_URL, headers=with_refresh_token(token))


def set_cookie_header(response, name: str) -> str:
    return next(header for header in response.headers.get_list("set-cookie") if header.startswith(f"{name}="))


def encode_access_token(payload: dict, key: str = settings.jwt_secret_key) -> str:
    return jwt.encode(payload, key, algorithm=settings.jwt_algorithm)


def valid_payload(user: User) -> dict:
    return {"sub": str(user.id), "type": "access", "exp": datetime.now(UTC) + timedelta(minutes=5)}


def test_registered_user_can_login_and_use_cookies(client):
    client.post("/api/v1/users", json={"login": "user", "password": PASSWORD})

    # Act
    response = login(client)
    me = client.get("/api/v1/users/me")

    # Assert
    assert response.status_code == 204
    assert response.content == b""
    assert me.status_code == 200
    assert me.json()["login"] == "user"


def test_login_sets_access_cookie_hidden_from_javascript(client, make_user):
    make_user()

    # Act
    cookie = set_cookie_header(login(client), ACCESS_TOKEN_COOKIE).lower()

    # Assert
    assert "httponly" in cookie
    assert "secure" in cookie
    assert "samesite=strict" in cookie
    assert "path=/;" in cookie
    assert f"max-age={settings.access_token_expire_minutes * 60}" in cookie


def test_login_sets_refresh_cookie_only_for_auth_endpoints(client, make_user):
    make_user()

    # Act
    cookie = set_cookie_header(login(client), REFRESH_TOKEN_COOKIE).lower()

    # Assert
    assert "httponly" in cookie
    assert "secure" in cookie
    assert "samesite=strict" in cookie
    assert "path=/api/v1/auth" in cookie
    assert f"max-age={settings.refresh_token_expire_days * 24 * 60 * 60}" in cookie


def test_bearer_header_is_not_accepted(client, make_user):
    user = make_user()

    # Act
    response = client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {create_access_token(user.id)}"})

    # Assert
    assert_error(response, 401, "NOT_AUTHENTICATED")


def test_login_wrong_password(client, make_user):
    make_user()

    # Act
    response = login(client, password="wrong-password")

    # Assert
    assert_error(response, 401, "INVALID_CREDENTIALS")
    assert "set-cookie" not in response.headers


def test_login_unknown_user_gets_same_error_as_wrong_password(client, make_user):
    make_user()

    # Act
    unknown = login(client, user_login="unknown")
    wrong_password = login(client, password="wrong-password")

    # Assert
    assert_error(unknown, 401, "INVALID_CREDENTIALS")
    assert unknown.json() == wrong_password.json()


def test_refresh_sets_new_working_cookies(client, make_user):
    make_user()
    old_refresh_token = refresh_token_of(login(client))

    # Act
    response = client.post(REFRESH_URL)
    me = client.get("/api/v1/users/me")

    # Assert
    assert response.status_code == 204
    assert ACCESS_TOKEN_COOKIE in response.cookies
    assert refresh_token_of(response) != old_refresh_token
    assert me.status_code == 200


def test_refresh_without_cookie(client):
    # Act
    response = client.post(REFRESH_URL)

    # Assert
    assert_error(response, 401, "INVALID_REFRESH_TOKEN")


def test_refresh_token_works_only_once(client, make_user):
    make_user()
    token = refresh_token_of(login(client))
    refresh(client, token)

    # Act
    response = refresh(client, token)

    # Assert
    assert_error(response, 401, "INVALID_REFRESH_TOKEN")


def test_reusing_refresh_token_revokes_all_user_sessions(client, make_user):
    make_user()
    stolen = refresh_token_of(login(client))
    current = refresh_token_of(refresh(client, stolen))
    other_session = refresh_token_of(login(client))

    # Act
    refresh(client, stolen)

    # Assert
    assert_error(refresh(client, current), 401, "INVALID_REFRESH_TOKEN")
    assert_error(refresh(client, other_session), 401, "INVALID_REFRESH_TOKEN")


def test_refresh_unknown_token(client):
    # Act
    response = refresh(client, "not_exist")

    # Assert
    assert_error(response, 401, "INVALID_REFRESH_TOKEN")


def test_refresh_expired_token(client, db_session, make_user):
    make_user()
    token = refresh_token_of(login(client))
    stored = db_session.query(RefreshToken).filter_by(token_hash=hash_refresh_token(token)).one()
    stored.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()

    # Act
    response = refresh(client, token)

    # Assert
    assert_error(response, 401, "INVALID_REFRESH_TOKEN")


def test_refresh_token_is_stored_hashed(client, db_session, make_user):
    make_user()
    token = refresh_token_of(login(client))

    # Act
    stored = db_session.query(RefreshToken).one()

    # Assert
    assert stored.token_hash != token
    assert stored.token_hash == hash_refresh_token(token)


def test_logout_revokes_refresh_token(client, make_user):
    make_user()
    token = refresh_token_of(login(client))

    # Act
    response = client.post(LOGOUT_URL)

    # Assert
    assert response.status_code == 204
    assert_error(refresh(client, token), 401, "INVALID_REFRESH_TOKEN")


def test_logout_deletes_cookies(client, make_user):
    make_user()
    login(client)

    # Act
    response = client.post(LOGOUT_URL)

    # Assert
    assert "max-age=0" in set_cookie_header(response, ACCESS_TOKEN_COOKIE).lower()
    assert "max-age=0" in set_cookie_header(response, REFRESH_TOKEN_COOKIE).lower()
    assert_error(client.get("/api/v1/users/me"), 401, "NOT_AUTHENTICATED")


def test_logout_keeps_other_sessions(client, make_user):
    make_user()
    token = refresh_token_of(login(client))
    other_session = refresh_token_of(login(client))

    # Act
    client.post(LOGOUT_URL, headers=with_refresh_token(token))

    # Assert
    assert refresh(client, other_session).status_code == 204


def test_logout_unknown_token(client):
    # Act
    response = client.post(LOGOUT_URL, headers=with_refresh_token("not_exist"))

    # Assert
    assert response.status_code == 204


def test_logout_without_cookie(client):
    # Act
    response = client.post(LOGOUT_URL)

    # Assert
    assert response.status_code == 204


def test_access_token_expired(client, make_user):
    user = make_user()
    payload = valid_payload(user) | {"exp": datetime.now(UTC) - timedelta(seconds=1)}

    # Act
    response = client.get("/api/v1/users/me", headers=with_access_token(encode_access_token(payload)))

    # Assert
    assert_error(response, 401, "NOT_AUTHENTICATED")


def test_access_token_signed_with_other_key(client, make_user):
    user = make_user()
    token = encode_access_token(valid_payload(user), key="other-secret-key-0123456789-0123456789")

    # Act
    response = client.get("/api/v1/users/me", headers=with_access_token(token))

    # Assert
    assert_error(response, 401, "NOT_AUTHENTICATED")


def test_access_token_with_wrong_type(client, make_user):
    user = make_user()
    payload = valid_payload(user) | {"type": "refresh"}

    # Act
    response = client.get("/api/v1/users/me", headers=with_access_token(encode_access_token(payload)))

    # Assert
    assert_error(response, 401, "NOT_AUTHENTICATED")


def test_access_token_without_expiration(client, make_user):
    user = make_user()
    payload = valid_payload(user)
    del payload["exp"]

    # Act
    response = client.get("/api/v1/users/me", headers=with_access_token(encode_access_token(payload)))

    # Assert
    assert_error(response, 401, "NOT_AUTHENTICATED")


def test_access_token_of_deleted_user(client, db_session, make_user):
    user = make_user()
    token = encode_access_token(valid_payload(user))
    db_session.delete(user)
    db_session.commit()

    # Act
    response = client.get("/api/v1/users/me", headers=with_access_token(token))

    # Assert
    assert_error(response, 401, "NOT_AUTHENTICATED")


def test_login_cannot_be_used_as_access_token(client, make_user):
    user = make_user()

    # Act
    response = client.get("/api/v1/users/me", headers=with_access_token(user.login))

    # Assert
    assert_error(response, 401, "NOT_AUTHENTICATED")


def test_slow_password_check_does_not_block_other_requests(client, make_user, auth, monkeypatch, run_in_background):
    # Checking a password takes tens of milliseconds of CPU. In the event loop it would stop every other request
    user = make_user()
    pause = Pause()
    monkeypatch.setattr("app.services.auth.verify_password", pause.wrap(verify_password))

    # Act
    try:
        login_request = run_in_background(login, client)
        assert pause.entered.wait(timeout=5)
        other = run_in_background(client.get, "/api/v1/users/me", headers=auth(user))
        other_response = other.result(timeout=OTHER_REQUEST_SECONDS)
    finally:
        pause.released.set()

    # Assert
    assert other_response.status_code == 200
    assert login_request.result(timeout=10).status_code == 204
