from datetime import date

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.logging import LogFormat, LogLevel


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    redis_url: str
    redis_timeout_seconds: float = 1.0
    exchange_api_url: str = "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1"
    # {day} becomes a date: the API keeps the rates of every past day
    exchange_api_history_url: str = Field(
        default="https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@{day}/v1", pattern=r"\{day\}"
    )
    # The first day the API has rates for: earlier days get its rates
    exchange_api_history_start: date = date(2024, 3, 2)
    exchange_api_timeout_seconds: float = 5.0
    # Requests to the exchange API at once from one process: a report over years needs hundreds of days
    exchange_api_max_concurrency: int = Field(default=10, ge=1)
    # Longer than one load of a day takes: otherwise a second process starts the same load
    exchange_rate_lock_seconds: int = Field(default=30, ge=1)
    # How long a request waits for another process to load a day before it loads the day itself
    exchange_rate_wait_seconds: float = Field(default=10.0, gt=0)
    jwt_secret_key: str = Field(min_length=32)
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7
    health_check_timeout_seconds: float = 2.0
    default_page_size: int = Field(default=20, ge=1)
    max_page_size: int = Field(default=100, ge=1)
    login_rate_limit_per_ip: int = Field(default=20, ge=1)
    login_rate_limit_per_login: int = Field(default=5, ge=1)
    login_rate_limit_window_seconds: int = Field(default=60, ge=1)
    # Every parse request is a paid model call
    parse_rate_limit_per_user: int = Field(default=10, ge=1)
    parse_rate_limit_window_seconds: int = Field(default=60, ge=1)
    # Every tips request that misses the cache is a paid model call
    tips_rate_limit_per_user: int = Field(default=10, ge=1)
    tips_rate_limit_window_seconds: int = Field(default=60, ge=1)
    # Facts are in the key, so an entry never goes stale: the time only bounds the memory
    tips_cache_ttl_seconds: int = Field(default=86400, ge=1)
    # An expense category gets a warning when it is new or grew this much, and holds this share of expenses
    # Tips compare a period with the average of this many periods before it
    tips_baseline_periods: int = Field(default=3, ge=1)
    tips_alert_min_growth_percent: float = Field(default=20.0, gt=0)
    tips_alert_min_share_percent: float = Field(default=5.0, ge=0, le=100)
    openai_api_key: SecretStr
    openai_model: str = "gpt-6-luna"
    # The SDK default is 10 minutes: a hanging request would hold a worker slot that long
    openai_timeout_seconds: float = Field(default=30.0, gt=0)
    # Ten years: a report over a longer range loads too many days of exchange rates the first time
    analytics_max_range_days: int = Field(default=3653, ge=1)
    log_level: LogLevel = "INFO"
    log_format: LogFormat = "text"


settings = Settings()
