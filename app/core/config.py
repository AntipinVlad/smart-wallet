from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.logging import LogFormat, LogLevel


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    exchange_api_url: str = "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1"
    exchange_api_timeout_seconds: float = 5.0
    jwt_secret_key: str = Field(min_length=32)
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7
    health_check_timeout_seconds: float = 2.0
    default_page_size: int = Field(default=20, ge=1)
    max_page_size: int = Field(default=100, ge=1)
    log_level: LogLevel = "INFO"
    log_format: LogFormat = "text"


settings = Settings()
