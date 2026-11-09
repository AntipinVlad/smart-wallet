import asyncio
import json

from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.errors import handle_http_exception
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
    response = client.post("/api/v1/users", json={"password": "secret-password", "timezone": "UTC"})

    # Assert
    error = assert_error(response, 422, "VALIDATION_ERROR")
    assert [detail["field"] for detail in error["details"]] == ["body.login"]
    assert error["details"][0]["message"]


def test_validation_error_does_not_return_sent_values(client):
    # Act
    # Pydantic puts the sent value into its errors
    response = client.post("/api/v1/users", json={"login": "new_user", "password": "Pw7#x"})

    # Assert
    assert_error(response, 422, "VALIDATION_ERROR")
    assert "Pw7#x" not in response.text
