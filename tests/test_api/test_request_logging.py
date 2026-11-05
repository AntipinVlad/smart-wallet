import logging
import re

import pytest

from tests.conftest import assert_error

ACCESS_LOGGER = "app.access"


def access_records(caplog) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name == ACCESS_LOGGER]


@pytest.mark.parametrize("sent_id, kept", [
    (None, False),
    # Usually set by nginx, so the same id is in the logs of nginx and of the app
    ("nginx-4f2a.1", True),
    ("x" * 65, False),
    ("bad id with spaces", False),
    ("id\r\nX-Injected: 1", False),
], ids=["not_sent", "valid", "too_long", "unsafe_characters", "header_injection"])
def test_response_has_request_id(client, sent_id, kept):
    # Act
    response = client.get("/api/v1/users/me", headers={"X-Request-ID": sent_id} if sent_id else {})

    # Assert
    request_id = response.headers["X-Request-ID"]
    if kept:
        assert request_id == sent_id
    else:
        assert re.fullmatch(r"[0-9a-f]{32}", request_id)


def test_every_request_is_logged_once_without_query_string(client, caplog):
    # Act
    with caplog.at_level(logging.INFO, logger=ACCESS_LOGGER):
        # Query parameters can carry personal data, the path is enough to find the route
        response = client.get("/api/v1/operations?date_from=2026-01-01")

    # Assert
    [record] = access_records(caplog)
    assert (record.method, record.path, record.status) == ("GET", "/api/v1/operations", 401)
    assert record.duration_ms >= 0
    assert record.request_id == response.headers["X-Request-ID"]


def test_logs_inside_request_have_its_request_id(client, make_user, caplog):
    make_user(login="known")

    # Act
    with caplog.at_level(logging.WARNING, logger="app.services.auth"):
        response = client.post("/api/v1/auth/login", json={"login": "known", "password": "wrong-password"})

    # Assert
    [record] = [record for record in caplog.records if record.name == "app.services.auth"]
    assert record.request_id == response.headers["X-Request-ID"]


def test_unexpected_error_is_logged_once_and_hidden_from_client(client, make_user, auth, monkeypatch, caplog):
    async def broken(*args, **kwargs):
        raise RuntimeError("internal detail")

    monkeypatch.setattr("app.services.wallets.get_all_wallets", broken)
    user = make_user()

    # Act
    # The default test client re-raises errors that reach the server. A response here means the error was
    # handled inside the app, so uvicorn does not log the same traceback a second time
    with caplog.at_level(logging.INFO):
        response = client.get("/api/v1/wallets", headers=auth(user))

    # Assert
    assert_error(response, 500, "INTERNAL_ERROR")
    assert "internal detail" not in response.text
    [error_record] = [record for record in caplog.records if record.exc_info]
    assert "internal detail" in str(error_record.exc_info[1])
    assert error_record.request_id == response.headers["X-Request-ID"]
    [access_record] = access_records(caplog)
    assert access_record.status == 500
