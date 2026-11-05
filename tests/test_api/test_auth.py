from datetime import UTC, datetime, timedelta

import jwt
import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.cookies import ACCESS_TOKEN_COOKIE, REFRESH_TOKEN_COOKIE
from app.core.security import create_access_token, hash_refresh_token, verify_password
from app.main import app
from app.models import RefreshToken, User
from tests.conftest import OTHER_REQUEST_SECONDS, PASSWORD, Pause, assert_error

REFRESH_URL = "/api/v1/auth/refresh"
LOGOUT_URL = "/api/v1/auth/logout"
ME_URL = "/api/v1/users/me"


def login(client, user_login: str = "user", password: str = PASSWORD):
    return client.post("/api/v1/auth/login", json={"login": user_login, "password": password})


def refresh_token_of(response) -> str:
    return response.cookies[REFRESH_TOKEN_COOKIE]


def with_refresh_token(token: str) -> dict[str, str]:
    # An explicit Cookie header replaces the client cookie jar
    return {"Cookie": f"{REFRESH_TOKEN_COOKIE}={token}"}


def with_access_token(token: str) -> dict[str, str]:
    return {"Cookie": f"{ACCESS_TOKEN_COOKIE}={token}"}


def refresh(client, token: str):
    return client.post(REFRESH_URL, headers=with_refresh_token(token))


def set_cookie_header(response, name: str) -> str:
    return next(header for header in response.headers.get_list("set-cookie") if header.startswith(f"{name}="))


def access_token(user: User, key: str = settings.jwt_secret_key, **claims) -> str:
    payload = {"sub": str(user.id), "type": "access", "exp": datetime.now(UTC) + timedelta(minutes=5)} | claims
    return jwt.encode(
        {name: value for name, value in payload.items() if value is not None}, key, algorithm=settings.jwt_algorithm
    )


def protected_routes() -> list[tuple[str, str]]:
    # From the app itself, so a new protected route is checked too
    return [
        (method.upper(), path)
        for path, path_item in app.openapi()["paths"].items()
        for method, operation in path_item.items()
        if "security" in operation
    ]


@pytest.mark.parametrize("method, path", protected_routes())
def test_protected_route_requires_access_token(client, method, path):
    # Act
    response = client.request(method, path)

    # Assert
    assert_error(response, 401, "NOT_AUTHENTICATED")


@pytest.mark.parametrize(
    "headers",
    [
        lambda user: with_access_token(access_token(user, exp=datetime.now(UTC) - timedelta(seconds=1))),
        lambda user: with_access_token(access_token(user, key="other-secret-key-0123456789-0123456789")),
        lambda user: with_access_token(access_token(user, type="refresh")),
        lambda user: with_access_token(access_token(user, exp=None)),
        lambda user: with_access_token(user.login),
        lambda user: {"Authorization": f"Bearer {create_access_token(user.id)}"},
    ],
    ids=[
        "expired",
        "signed_with_other_key",
        "not_access_type",
        "without_expiration",
        "not_a_jwt",
        "valid_token_in_bearer_header",
    ],
)
def test_invalid_access_token_is_rejected(client, make_user, headers):
    user = make_user()

    # Act
    response = client.get(ME_URL, headers=headers(user))

    # Assert
    assert_error(response, 401, "NOT_AUTHENTICATED")


def test_access_token_of_deleted_user(client, db_session, make_user):
    user = make_user()
    token = access_token(user)
    db_session.delete(user)
    db_session.commit()

    # Act
    response = client.get(ME_URL, headers=with_access_token(token))

    # Assert
    assert_error(response, 401, "NOT_AUTHENTICATED")


def test_registered_user_can_login_and_use_cookies(client):
    client.post("/api/v1/users", json={"login": "user", "password": PASSWORD})

    # Act
    response = login(client)
    me = client.get(ME_URL)

    # Assert
    assert response.status_code == 204
    assert response.content == b""
    assert me.json()["login"] == "user"


@pytest.mark.parametrize(
    "cookie, path, max_age",
    [
        (ACCESS_TOKEN_COOKIE, "path=/;", settings.access_token_expire_minutes * 60),
        (REFRESH_TOKEN_COOKIE, "path=/api/v1/auth", settings.refresh_token_expire_days * 24 * 60 * 60),
    ],
)
def test_login_sets_cookie_hidden_from_javascript(client, make_user, cookie, path, max_age):
    make_user()

    # Act
    header = set_cookie_header(login(client), cookie).lower()

    # Assert
    for flag in ("httponly", "secure", "samesite=strict", path, f"max-age={max_age}"):
        assert flag in header


def test_login_with_wrong_credentials(client, make_user):
    make_user()

    # Act
    wrong_password = login(client, password="wrong-password")
    unknown_user = login(client, user_login="unknown")

    # Assert
    assert_error(wrong_password, 401, "INVALID_CREDENTIALS")
    # Must not reveal which logins exist
    assert unknown_user.status_code == wrong_password.status_code
    assert unknown_user.json() == wrong_password.json()
    assert "set-cookie" not in wrong_password.headers


def test_refresh_sets_new_working_cookies(client, make_user):
    make_user()
    old_refresh_token = refresh_token_of(login(client))

    # Act
    response = client.post(REFRESH_URL)

    # Assert
    assert response.status_code == 204
    assert ACCESS_TOKEN_COOKIE in response.cookies
    assert refresh_token_of(response) != old_refresh_token
    assert client.get(ME_URL).status_code == 200


@pytest.mark.parametrize("headers", [{}, with_refresh_token("not_exist")], ids=["without_cookie", "unknown_token"])
def test_refresh_without_valid_token(client, headers):
    # Act
    response = client.post(REFRESH_URL, headers=headers)

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


def test_refresh_expired_token(client, db_session, make_user):
    make_user()
    token = refresh_token_of(login(client))
    stored = db_session.scalars(select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(token))).one()
    stored.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()

    # Act
    response = refresh(client, token)

    # Assert
    assert_error(response, 401, "INVALID_REFRESH_TOKEN")


def test_refresh_token_is_stored_hashed(client, db_session, make_user):
    make_user()

    # Act
    token = refresh_token_of(login(client))

    # Assert
    stored = db_session.scalars(select(RefreshToken)).one()
    assert stored.token_hash == hash_refresh_token(token)
    assert stored.token_hash != token


def test_logout_ends_session(client, make_user):
    make_user()
    token = refresh_token_of(login(client))

    # Act
    response = client.post(LOGOUT_URL)

    # Assert
    assert response.status_code == 204
    assert "max-age=0" in set_cookie_header(response, ACCESS_TOKEN_COOKIE).lower()
    assert "max-age=0" in set_cookie_header(response, REFRESH_TOKEN_COOKIE).lower()
    assert_error(client.get(ME_URL), 401, "NOT_AUTHENTICATED")
    assert_error(refresh(client, token), 401, "INVALID_REFRESH_TOKEN")


def test_logout_keeps_other_sessions(client, make_user):
    make_user()
    token = refresh_token_of(login(client))
    other_session = refresh_token_of(login(client))

    # Act
    client.post(LOGOUT_URL, headers=with_refresh_token(token))

    # Assert
    assert refresh(client, other_session).status_code == 204


@pytest.mark.parametrize("headers", [{}, with_refresh_token("not_exist")], ids=["without_cookie", "unknown_token"])
def test_logout_without_valid_token_still_succeeds(client, headers):
    response = client.post(LOGOUT_URL, headers=headers)

    # Assert
    assert response.status_code == 204


def test_slow_password_check_does_not_block_other_requests(client, make_user, auth, monkeypatch, run_in_background):
    # Password checking is CPU-heavy: in the event loop it would stop other requests
    user = make_user()
    pause = Pause()
    monkeypatch.setattr("app.services.auth.verify_password", pause.wrap(verify_password))

    # Act
    try:
        login_request = run_in_background(login, client)
        assert pause.entered.wait(timeout=5)
        other = run_in_background(client.get, ME_URL, headers=auth(user))
        other_response = other.result(timeout=OTHER_REQUEST_SECONDS)
    finally:
        pause.released.set()

    # Assert
    assert other_response.status_code == 200
    assert login_request.result(timeout=10).status_code == 204
