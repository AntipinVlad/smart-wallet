from pydantic import BaseModel, Field


class UserRequest(BaseModel):
    login: str = Field(..., max_length=127)


class UserCreateRequest(UserRequest):
    password: str = Field(..., min_length=8, max_length=128)


class UserResponse(UserRequest):
    model_config = {"from_attributes": True}
    id: int
