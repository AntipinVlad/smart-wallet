import asyncio
import os
import threading
from collections.abc import Callable, Generator
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://wallet@127.0.0.1:5433/finance_test")
REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://127.0.0.1:6380/0")

# Before the app is imported, so tests never touch the working database
os.environ["DATABASE_URL"] = DATABASE_URL
os.environ["REDIS_URL"] = REDIS_URL
os.environ["JWT_SECRET_KEY"] = "test-only-secret-key-0123456789-0123456789"
# Tests never call OpenAI: every test that needs it gets a fake client
os.environ["OPENAI_API_KEY"] = "test-only-openai-key"

import httpx2
import pytest
import redis
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from app.core.cookies import ACCESS_TOKEN_COOKIE
from app.core.exceptions import ErrorCode
from app.core.security import create_access_token, hash_password
from app.database import Base
from app.enums import CurrencyEnum, Language, OperationType
from app.main import app
from app.models import Operation, User, Wallet

PASSWORD = "correct-password"
TIMEZONE = "Europe/Kyiv"
OTHER_REQUEST_SECONDS = 2
PASSWORD_HASH = hash_password(PASSWORD)

sync_engine = create_engine(DATABASE_URL)
SyncSessionLocal = sessionmaker(bind=sync_engine)
sync_redis = redis.Redis.from_url(REDIS_URL)


class Pause:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.released = threading.Event()

    def wrap(self, func: Callable[..., Any]) -> Callable[..., Any]:
        def paused(*args: Any, **kwargs: Any) -> Any:
            self.entered.set()
            self.released.wait(timeout=10)
            return func(*args, **kwargs)

        return paused


def assert_error(response: httpx2.Response, status_code: int, code: str) -> dict[str, Any]:
    assert response.status_code == status_code, response.text
    error = response.json()["error"]
    assert error["code"] == code
    assert error["code"] in set(ErrorCode), "every code the API returns must be in ErrorCode"
    assert error["message"]
    return error


@pytest.fixture(scope="session")
def alembic_config() -> Config:
    return Config(Path(__file__).parent.parent / "alembic.ini")


@pytest.fixture(scope="session", autouse=True)
def migrated_db(alembic_config):
    with sync_engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    command.upgrade(alembic_config, "head")
    yield
    sync_engine.dispose()


@pytest.fixture(autouse=True)
def clean_redis():
    # Login tests share one client IP: counters from earlier tests would hit the limits
    sync_redis.flushdb()


@pytest.fixture(autouse=True)
def clean_tables():
    yield
    tables = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    with sync_engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture()
def client():
    # https: the auth cookies are Secure. One loop per test, like uvicorn.
    # SelectorEventLoop: async psycopg does not work with ProactorEventLoop on Windows
    with TestClient(
        app, base_url="https://testserver", backend_options={"loop_factory": asyncio.SelectorEventLoop}
    ) as test_client:
        yield test_client


@pytest.fixture()
def run_in_background() -> Generator[Callable[..., Future], None, None]:
    with ThreadPoolExecutor(max_workers=2) as pool:
        yield pool.submit


@pytest.fixture()
def db_session() -> Generator[Session, None, None]:
    db = SyncSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture()
def make_user(db_session) -> Callable[..., User]:
    def _make_user(login: str = "user", timezone: str = TIMEZONE, language: Language = Language.UK) -> User:
        user = User(login=login, password_hash=PASSWORD_HASH, timezone=timezone, language=language)
        db_session.add(user)
        db_session.commit()
        return user

    return _make_user


@pytest.fixture()
def make_wallet(db_session) -> Callable[..., Wallet]:
    def _make_wallet(
        user: User, name: str = "card", balance: Decimal = Decimal(200), currency: CurrencyEnum = CurrencyEnum.UAH
    ) -> Wallet:
        wallet = Wallet(name=name, balance=balance, user_id=user.id, currency=currency)
        db_session.add(wallet)
        db_session.commit()
        return wallet

    return _make_wallet


@pytest.fixture()
def make_operation(db_session) -> Callable[..., Operation]:
    def _make_operation(
        wallet: Wallet,
        amount: Decimal = Decimal(50),
        created_at: datetime = datetime(2026, 1, 15, tzinfo=UTC),
        type: OperationType = OperationType.EXPENSE,
        description: str | None = None,
        category: str | None = None,
        subcategory: str | None = None,
    ) -> Operation:
        operation = Operation(
            wallet_id=wallet.id,
            type=type,
            amount=amount,
            currency=wallet.currency,
            created_at=created_at,
            description=description,
            category=category,
            subcategory=subcategory,
        )
        db_session.add(operation)
        db_session.commit()
        return operation

    return _make_operation


@pytest.fixture()
def auth() -> Callable[[User], dict[str, str]]:
    def _auth(user: User) -> dict[str, str]:
        return {"Cookie": f"{ACCESS_TOKEN_COOKIE}={create_access_token(user.id)}"}

    return _auth


@pytest.fixture()
def mock_exchange_rate(monkeypatch) -> AsyncMock:
    mock = AsyncMock(return_value=Decimal(40))
    monkeypatch.setattr("app.services.exchange_service.get_exchange_rate", mock)
    monkeypatch.setattr("app.services.operation.get_exchange_rate", mock)
    return mock


class FakeDailyRates:
    """Stands in for the exchange API of past days: the same rates every day unless a day is set."""

    def __init__(self) -> None:
        self.default = {CurrencyEnum.USD: Decimal(1), CurrencyEnum.UAH: Decimal(40), CurrencyEnum.EUR: Decimal("0.8")}
        self.by_day: dict[date, dict[CurrencyEnum, Decimal]] = {}
        self.unpublished: set[date] = set()
        self.error: Exception | None = None
        self.days: list[date] = []

    async def __call__(self, http_session: Any, day: date) -> dict[CurrencyEnum, Decimal] | None:
        self.days.append(day)
        if self.error is not None:
            raise self.error
        if day in self.unpublished:
            return None
        return self.by_day.get(day, self.default)


@pytest.fixture()
def daily_rates(monkeypatch) -> FakeDailyRates:
    fake = FakeDailyRates()
    monkeypatch.setattr("app.services.exchange_service.get_daily_rates", fake)
    return fake
