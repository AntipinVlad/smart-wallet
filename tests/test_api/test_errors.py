import asyncio
import json
import logging

from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.errors import handle_http_exception
from main import app
from tests.conftest import assert_error


def test_unknown_route(client):
    # Act
    response = client.get("/api/v1/unknown")

    # Assert
    assert_error(response, 404, "NOT_FOUND")


def test_method_not_allowed(client):
    # Act
    response = client.delete("/api/v1/users")

    # Assert
    assert_error(response, 405, "METHOD_NOT_ALLOWED")


def test_other_framework_errors_get_common_code():
    # Act
    response = asyncio.run(handle_http_exception(None, StarletteHTTPException(status_code=418)))

    # Assert
    assert response.status_code == 418
    assert json.loads(response.body)["error"]["code"] == "HTTP_ERROR"


def test_validation_error_lists_invalid_fields(client):
    # Act
    response = client.post("/api/v1/users", json={"password": "secret-password"})

    # Assert
    error = assert_error(response, 422, "VALIDATION_ERROR")
    assert [detail["field"] for detail in error["details"]] == ["body.login"]
    assert error["details"][0]["message"]


def test_validation_error_does_not_return_sent_values(client):
    # Act
    # Pydantic puts the sent value into its errors. A rejected password must not come back in the response
    response = client.post("/api/v1/users", json={"login": "new_user", "password": "Pw7#x"})

    # Assert
    assert_error(response, 422, "VALIDATION_ERROR")
    assert "Pw7#x" not in response.text


def test_unexpected_error_is_logged_and_hidden_from_client(make_user, auth, monkeypatch, caplog):
    async def broken(*args, **kwargs):
        raise RuntimeError("internal detail")

    monkeypatch.setattr("app.services.wallets.get_all_wallets", broken)
    user = make_user()

    # Act
    # raise_server_exceptions=False: get the 500 response like a real client instead of the exception
    with TestClient(app, base_url="https://testserver", raise_server_exceptions=False,
                    backend_options={"loop_factory": asyncio.SelectorEventLoop}) as client:
        with caplog.at_level(logging.ERROR):
            response = client.get("/api/v1/wallets", headers=auth(user))

    # Assert
    assert_error(response, 500, "INTERNAL_ERROR")
    assert "internal detail" not in response.text
    assert any(record.exc_info and "internal detail" in str(record.exc_info[1]) for record in caplog.records)
