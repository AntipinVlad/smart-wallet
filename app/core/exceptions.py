from decimal import Decimal
from enum import StrEnum

from app.enums import CurrencyEnum


class ErrorCode(StrEnum):
    NOT_AUTHENTICATED = "NOT_AUTHENTICATED"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    INVALID_REFRESH_TOKEN = "INVALID_REFRESH_TOKEN"
    USER_ALREADY_EXISTS = "USER_ALREADY_EXISTS"
    WALLET_NOT_FOUND = "WALLET_NOT_FOUND"
    WALLET_ALREADY_EXISTS = "WALLET_ALREADY_EXISTS"
    INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"
    EXCHANGE_RATE_NOT_FOUND = "EXCHANGE_RATE_NOT_FOUND"
    EXCHANGE_SERVICE_UNAVAILABLE = "EXCHANGE_SERVICE_UNAVAILABLE"
    DATABASE_UNAVAILABLE = "DATABASE_UNAVAILABLE"
    RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"

    # No error class: set by the handlers in app/api/errors.py
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    HTTP_ERROR = "HTTP_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class AppError(Exception):
    """An error the client may see. Services raise it instead of HTTPException, so they do not depend on HTTP."""

    code: ErrorCode
    headers: dict[str, str] | None = None

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


# Error groups. Each group has one HTTP status


class NotFoundError(AppError):
    """The requested resource does not exist or does not belong to the current user."""


class ConflictError(AppError):
    """The request is valid, but the current state of the data does not allow it."""


class AuthenticationError(AppError):
    """The client is not authenticated: no token, an invalid token, or wrong credentials."""


class TooManyRequestsError(AppError):
    """The client sent too many requests in a short time and has to wait."""


class ExternalServiceError(AppError):
    """An external service answered, but the answer cannot be used."""


class ExternalServiceUnavailableError(AppError):
    """A service the app depends on (an external API, the database) did not answer."""


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


class DatabaseUnavailableError(ExternalServiceUnavailableError):
    code = ErrorCode.DATABASE_UNAVAILABLE

    def __init__(self) -> None:
        super().__init__("Database unavailable")


class RateLimitExceededError(TooManyRequestsError):
    code = ErrorCode.RATE_LIMIT_EXCEEDED

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("Too many requests, try again later")
        self.headers = {"Retry-After": str(retry_after_seconds)}
