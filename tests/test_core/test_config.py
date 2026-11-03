import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_settings_read_from_environment(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./from_env.db")
    monkeypatch.setenv("EXCHANGE_API_URL", "https://rates.test/v1")
    monkeypatch.setenv("EXCHANGE_API_TIMEOUT_SECONDS", "1.5")

    # Act
    settings = Settings(_env_file=None)

    # Assert
    assert settings.database_url == "sqlite:///./from_env.db"
    assert settings.exchange_api_url == "https://rates.test/v1"
    assert settings.exchange_api_timeout_seconds == 1.5


def test_settings_read_from_env_file(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL")
    env_file = tmp_path / ".env"
    env_file.write_text("DATABASE_URL=sqlite:///./from_file.db\n")

    # Act
    settings = Settings(_env_file=env_file)

    # Assert
    assert settings.database_url == "sqlite:///./from_file.db"


def test_settings_require_jwt_secret_key(monkeypatch):
    monkeypatch.delenv("JWT_SECRET_KEY")

    # Act
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_reject_short_jwt_secret_key(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "too-short")

    # Act
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_require_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL")

    # Act
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
