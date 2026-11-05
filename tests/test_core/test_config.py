import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_settings_read_from_environment(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./from_env.db")
    monkeypatch.setenv("EXCHANGE_API_URL", "https://rates.test/v1")
    monkeypatch.setenv("EXCHANGE_API_TIMEOUT_SECONDS", "1.5")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    monkeypatch.setenv("LOG_FORMAT", "json")

    # Act
    settings = Settings(_env_file=None)

    # Assert
    assert settings.database_url == "sqlite:///./from_env.db"
    assert settings.exchange_api_url == "https://rates.test/v1"
    assert settings.exchange_api_timeout_seconds == 1.5
    assert (settings.log_level, settings.log_format) == ("WARNING", "json")


def test_settings_read_from_env_file(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL")
    env_file = tmp_path / ".env"
    env_file.write_text("DATABASE_URL=sqlite:///./from_file.db\n")

    # Act
    settings = Settings(_env_file=env_file)

    # Assert
    assert settings.database_url == "sqlite:///./from_file.db"


@pytest.mark.parametrize("name", ["DATABASE_URL", "JWT_SECRET_KEY", "REDIS_URL"])
def test_settings_require_value(monkeypatch, name):
    monkeypatch.delenv(name)

    # Act
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize(
    "name, value",
    [
        ("JWT_SECRET_KEY", "too-short"),
        ("LOG_LEVEL", "VERBOSE"),
        ("LOG_FORMAT", "xml"),
    ],
)
def test_settings_reject_invalid_value(monkeypatch, name, value):
    monkeypatch.setenv(name, value)

    # Act
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
