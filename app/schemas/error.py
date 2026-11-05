from pydantic import BaseModel, Field

from app.core.exceptions import ErrorCode


class ErrorDetail(BaseModel):
    field: str = Field(..., examples=["body.login"])
    message: str = Field(..., examples=["Field required"])


class ErrorBody(BaseModel):
    code: ErrorCode = Field(..., description="Stable error code. Check it instead of the message")
    message: str = Field(..., description="Human readable description, may change")
    details: list[ErrorDetail] | None = Field(None, description="Invalid fields, only for VALIDATION_ERROR")


class ErrorResponse(BaseModel):
    error: ErrorBody
