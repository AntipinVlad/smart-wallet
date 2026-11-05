from pydantic import BaseModel, Field

from app.schemas.user import UserRequest


class LoginRequest(UserRequest):
    password: str = Field(..., max_length=128)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
