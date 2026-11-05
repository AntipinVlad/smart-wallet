from typing import Annotated, Literal, TypedDict

from fastapi import Cookie, Response

from app.core.config import settings
from app.schemas import TokenPair

ACCESS_TOKEN_COOKIE = "access_token"
REFRESH_TOKEN_COOKIE = "refresh_token"
ACCESS_TOKEN_COOKIE_PATH = "/"
# The browser sends the refresh token only to the auth endpoints, not with every API request
REFRESH_TOKEN_COOKIE_PATH = "/api/v1/auth"


# HttpOnly hides the tokens from JavaScript, Secure sends them only over https,
# SameSite=strict keeps the browser from attaching them to requests made by other sites
class CookieFlags(TypedDict):
    httponly: bool
    secure: bool
    samesite: Literal["lax", "strict", "none"]


AUTH_COOKIE_FLAGS: CookieFlags = {"httponly": True, "secure": True, "samesite": "strict"}

RefreshTokenCookie = Annotated[str | None, Cookie(alias=REFRESH_TOKEN_COOKIE, max_length=255)]


def set_auth_cookies(response: Response, tokens: TokenPair) -> None:
    response.set_cookie(
        ACCESS_TOKEN_COOKIE,
        tokens.access_token,
        max_age=settings.access_token_expire_minutes * 60,
        path=ACCESS_TOKEN_COOKIE_PATH,
        **AUTH_COOKIE_FLAGS,
    )
    response.set_cookie(
        REFRESH_TOKEN_COOKIE,
        tokens.refresh_token,
        max_age=settings.refresh_token_expire_days * 24 * 60 * 60,
        path=REFRESH_TOKEN_COOKIE_PATH,
        **AUTH_COOKIE_FLAGS,
    )


def delete_auth_cookies(response: Response) -> None:
    # A cookie is deleted only with the same path it was set with
    response.delete_cookie(ACCESS_TOKEN_COOKIE, path=ACCESS_TOKEN_COOKIE_PATH, **AUTH_COOKIE_FLAGS)
    response.delete_cookie(REFRESH_TOKEN_COOKIE, path=REFRESH_TOKEN_COOKIE_PATH, **AUTH_COOKIE_FLAGS)
