from decimal import Decimal
from enum import StrEnum

from app.enums import CurrencyEnum


class ErrorCode(StrEnum):
    """Every error code the API can return. In OpenAPI it is an enum, so a generated client gets a union type."""

    # Raised by the app
    NOT_AUTHENTICATED = "NOT_AUTHENTICATED"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    INVALID_REFRESH_TOKEN = "INVALID_REFRESH_TOKEN"
    USER_ALREADY_EXISTS = "USER_ALREADY_EXISTS"
    WALLET_NOT_FOUND = "WALLET_NOT_FOUND"
    WALLET_ALREADY_EXISTS = "WALLET_ALREADY_EXISTS"
    INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"
    EXCHANGE_RATE_NOT_FOUND = "EXCHANGE_RATE_NOT_FOUND"
    EXCHANGE_SERVICE_UNAVAILABLE = "EXCHANGE_SERVICE_UNAVAILABLE"

    # Set by the error handlers in app/api/errors.py
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    HTTP_ERROR = "HTTP_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class AppError(Exception):
    """An error the client is allowed to see.

    Services raise these instead of HTTPException, so they do not depend on HTTP. The HTTP status is chosen
    by the error group in app/api/errors.py. `code` is stable: the frontend checks it, not the message.
    """

    code: ErrorCode

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


# Error groups. Each group has one HTTP status


class NotFoundError(AppError):
    pass


class ConflictError(AppError):
    """The request is valid, but the current state of the data does not allow it."""


class AuthenticationError(AppError):
    pass


class ExternalServiceError(AppError):
    """An external service answered, but the answer cannot be used."""


class ExternalServiceUnavailableError(AppError):
    """An external service did not answer."""


# Errors


class NotAuthenticatedError(AuthenticationError):
    code = ErrorCode.NOT_AUTHENTICATED

    def __init__(self) -> None:
        super().__init__("Not authenticated")


class InvalidCredentialsError(AuthenticationError):
    code = ErrorCode.INVALID_CREDENTIALS

    def __init__(self) -> None:
        super().__init__("Invalid login or password")


class InvalidRefreshTokenError(AuthenticationError):
    code = ErrorCode.INVALID_REFRESH_TOKEN

    def __init__(self) -> None:
        super().__init__("Invalid refresh token")


class UserAlreadyExistsError(ConflictError):
    code = ErrorCode.USER_ALREADY_EXISTS

    def __init__(self) -> None:
        super().__init__("User already exists")


class WalletNotFoundError(NotFoundError):
    code = ErrorCode.WALLET_NOT_FOUND

    def __init__(self, wallet: str | int | None = None) -> None:
        super().__init__("Wallet not found" if wallet is None else f"Wallet '{wallet}' not found")


class WalletAlreadyExistsError(ConflictError):
    code = ErrorCode.WALLET_ALREADY_EXISTS

    def __init__(self, name: str) -> None:
        super().__init__(f"Wallet '{name}' already exists")


class InsufficientFundsError(ConflictError):
    code = ErrorCode.INSUFFICIENT_FUNDS

    def __init__(self, available: Decimal, currency: CurrencyEnum) -> None:
        super().__init__(f"Not enough funds. Available: {available} {currency}")


class ExchangeRateNotFoundError(ExternalServiceError):
    code = ErrorCode.EXCHANGE_RATE_NOT_FOUND

    def __init__(self, base: CurrencyEnum, target: CurrencyEnum) -> None:
        super().__init__(f"Exchange rate {base}/{target} not found")


class ExchangeServiceUnavailableError(ExternalServiceUnavailableError):
    code = ErrorCode.EXCHANGE_SERVICE_UNAVAILABLE

    def __init__(self) -> None:
        super().__init__("Exchange rate service unavailable")
