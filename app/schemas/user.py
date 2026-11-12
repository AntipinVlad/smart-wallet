from typing import Annotated, Self

from pydantic import BaseModel, Field, StringConstraints, model_validator

from app.enums import Language
from app.schemas.common import TimeZone

Login = Annotated[str, StringConstraints(strip_whitespace=True, to_lower=True, min_length=1, max_length=127)]


class UserRequest(BaseModel):
    login: Login


class UserCreateRequest(UserRequest):
    password: str = Field(..., min_length=8, max_length=128)
    timezone: TimeZone
    language: Language = Field(Language.UK, description="Language of AI tips")


class UserUpdateRequest(BaseModel):
    """Only the fields that are sent change."""

    timezone: TimeZone | None = None
    language: Language | None = Field(None, description="Language of AI tips")

    @model_validator(mode="after")
    def check_not_empty(self) -> Self:
        # A field sent as null is in model_fields_set too, so null is rejected below, not skipped
        if not self.model_fields_set:
            raise ValueError("Send timezone, language or both")
        if None in (getattr(self, name) for name in self.model_fields_set):
            raise ValueError("Fields cannot be null")
        return self


class UserResponse(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    login: str
    timezone: str
    language: Language
