from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    exchange_api_url: str = "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1"
    exchange_api_timeout_seconds: float = 5.0


settings = Settings()
