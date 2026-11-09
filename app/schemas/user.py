from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from app.schemas.common import TimeZone

Login = Annotated[str, StringConstraints(strip_whitespace=True, to_lower=True, min_length=1, max_length=127)]


class UserRequest(BaseModel):
    login: Login


class UserCreateRequest(UserRequest):
    password: str = Field(..., min_length=8, max_length=128)
    timezone: TimeZone


class UserUpdateRequest(BaseModel):
    timezone: TimeZone


class UserResponse(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    login: str
    timezone: str
