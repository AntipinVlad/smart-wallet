from typing import Any

from app.api.errors import status_code_for
from app.core.exceptions import AppError, ErrorCode
from app.main import app

ERROR_SCHEMA = {"$ref": "#/components/schemas/ErrorResponse"}


def operations() -> dict[str, dict[str, Any]]:
    return {
        f"{method.upper()} {path}": operation
        for path, path_item in app.openapi()["paths"].items()
        for method, operation in path_item.items()
    }


def documented_codes(operation: dict[str, Any], status_code: int) -> set[str]:
    # The description looks like "Conflict: WALLET_ALREADY_EXISTS, INSUFFICIENT_FUNDS"
    response = operation["responses"].get(str(status_code))
    return set(response["description"].split(": ", 1)[1].split(", ")) if response else set()


def concrete_errors(error_type: type[AppError] = AppError) -> list[type[AppError]]:
    found = []
    for subclass in error_type.__subclasses__():
        if "code" in vars(subclass):
            found.append(subclass)
        found.extend(concrete_errors(subclass))
    return found


def test_every_route_documents_common_errors():
    problems = []
    for name, operation in operations().items():
        for status_code, response in operation["responses"].items():
            if int(status_code) >= 400 and response["content"]["application/json"]["schema"] != ERROR_SCHEMA:
                problems.append(f"{name}: {status_code} does not use ErrorResponse")
        if "INTERNAL_ERROR" not in documented_codes(operation, 500):
            problems.append(f"{name}: no 500 INTERNAL_ERROR")
        if "security" in operation and "NOT_AUTHENTICATED" not in documented_codes(operation, 401):
            problems.append(f"{name}: protected, but no 401 NOT_AUTHENTICATED")
        has_input = "requestBody" in operation or operation.get("parameters")
        if has_input and "VALIDATION_ERROR" not in documented_codes(operation, 422):
            problems.append(f"{name}: takes input, but no 422 VALIDATION_ERROR")

    # Assert
    assert problems == []


def test_default_fastapi_validation_schema_is_not_used():
    # Act
    schemas = app.openapi()["components"]["schemas"]

    # Assert
    assert "HTTPValidationError" not in schemas


def test_route_errors_are_grouped_by_status():
    operation = operations()["POST /api/v1/operations/transfers"]

    # Act
    codes_by_status = {
        int(status): documented_codes(operation, int(status)) for status in operation["responses"] if int(status) >= 400
    }

    # Assert
    assert codes_by_status == {
        401: {"NOT_AUTHENTICATED"},
        404: {"WALLET_NOT_FOUND"},
        409: {"INSUFFICIENT_FUNDS"},
        422: {"VALIDATION_ERROR"},
        500: {"INTERNAL_ERROR"},
        502: {"EXCHANGE_RATE_NOT_FOUND"},
        503: {"EXCHANGE_SERVICE_UNAVAILABLE"},
    }


def test_error_code_is_documented_as_enum_of_all_codes():
    schemas = app.openapi()["components"]["schemas"]

    # Assert
    assert schemas["ErrorBody"]["properties"]["code"]["$ref"] == "#/components/schemas/ErrorCode"
    assert set(schemas["ErrorCode"]["enum"]) == {code.value for code in ErrorCode}


def test_every_error_has_status_and_code_from_enum():
    errors = concrete_errors()

    # Act
    statuses = [status_code_for(error_type) for error_type in errors]

    # Assert
    assert all(status >= 400 for status in statuses)
    assert all(isinstance(error_type.code, ErrorCode) for error_type in errors)
