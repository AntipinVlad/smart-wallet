from typing import Any

import pytest

from app.api.errors import status_code_for
from app.core.exceptions import AppError, ErrorCode
from main import app

ERROR_SCHEMA_REF = "#/components/schemas/ErrorResponse"


def operations() -> list[tuple[str, dict[str, Any]]]:
    schema = app.openapi()
    return [(f"{method.upper()} {path}", operation)
            for path, path_item in schema["paths"].items()
            for method, operation in path_item.items()]


def documented_codes(operation: dict[str, Any], status_code: int) -> set[str]:
    # The description looks like "Conflict: WALLET_ALREADY_EXISTS, INSUFFICIENT_FUNDS"
    description = operation["responses"][str(status_code)]["description"]
    return set(description.split(": ", 1)[1].split(", "))


def concrete_errors(error_type: type[AppError] = AppError) -> list[type[AppError]]:
    found = []
    for subclass in error_type.__subclasses__():
        if "code" in vars(subclass):
            found.append(subclass)
        found.extend(concrete_errors(subclass))
    return found


def test_default_fastapi_validation_schema_is_not_used():
    # Act
    schemas = app.openapi()["components"]["schemas"]

    # Assert
    assert "HTTPValidationError" not in schemas


@pytest.mark.parametrize("name, operation", operations())
def test_every_error_response_uses_common_error_schema(name, operation):
    for status_code, response in operation["responses"].items():
        if int(status_code) >= 400:
            # Assert
            assert response["content"]["application/json"]["schema"] == {"$ref": ERROR_SCHEMA_REF}, \
                f"{name} {status_code}"


@pytest.mark.parametrize("name, operation", operations())
def test_every_route_documents_internal_error(name, operation):
    # Assert
    assert documented_codes(operation, 500) == {"INTERNAL_ERROR"}


@pytest.mark.parametrize("name, operation", [(name, op) for name, op in operations() if "security" in op])
def test_every_protected_route_documents_not_authenticated(name, operation):
    # Assert
    assert "NOT_AUTHENTICATED" in documented_codes(operation, 401)


@pytest.mark.parametrize("name, status_code, codes", [
    ("POST /api/v1/users", 409, {"USER_ALREADY_EXISTS"}),
    ("POST /api/v1/auth/login", 401, {"INVALID_CREDENTIALS"}),
    ("POST /api/v1/auth/refresh", 401, {"INVALID_REFRESH_TOKEN"}),
    ("GET /api/v1/balance", 502, {"EXCHANGE_RATE_NOT_FOUND"}),
    ("GET /api/v1/balance", 503, {"EXCHANGE_SERVICE_UNAVAILABLE"}),
    ("POST /api/v1/wallets", 409, {"WALLET_ALREADY_EXISTS"}),
    ("POST /api/v1/operations/income", 404, {"WALLET_NOT_FOUND"}),
    ("POST /api/v1/operations/expense", 404, {"WALLET_NOT_FOUND"}),
    ("POST /api/v1/operations/expense", 409, {"INSUFFICIENT_FUNDS"}),
    ("GET /api/v1/operations", 404, {"WALLET_NOT_FOUND"}),
    ("POST /api/v1/operations/transfers", 404, {"WALLET_NOT_FOUND"}),
    ("POST /api/v1/operations/transfers", 409, {"INSUFFICIENT_FUNDS"}),
    ("POST /api/v1/operations/transfers", 502, {"EXCHANGE_RATE_NOT_FOUND"}),
    ("POST /api/v1/operations/transfers", 503, {"EXCHANGE_SERVICE_UNAVAILABLE"}),
])
def test_route_documents_its_errors(name, status_code, codes):
    operation = dict(operations())[name]

    # Assert
    assert documented_codes(operation, status_code) == codes


def test_validation_error_is_documented():
    operation = dict(operations())["POST /api/v1/users"]

    # Assert
    assert documented_codes(operation, 422) == {"VALIDATION_ERROR"}


def test_error_code_is_documented_as_enum_of_all_codes():
    schemas = app.openapi()["components"]["schemas"]

    # Assert
    assert schemas["ErrorBody"]["properties"]["code"]["$ref"] == "#/components/schemas/ErrorCode"
    assert set(schemas["ErrorCode"]["enum"]) == {code.value for code in ErrorCode}


@pytest.mark.parametrize("error_type", concrete_errors(), ids=lambda error_type: error_type.__name__)
def test_every_error_has_status_and_code_from_enum(error_type):
    # Assert
    assert status_code_for(error_type) >= 400
    assert isinstance(error_type.code, ErrorCode)
