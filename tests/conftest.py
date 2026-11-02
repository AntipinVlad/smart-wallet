import os
from datetime import datetime
from decimal import Decimal
from typing import Callable, Generator
from unittest.mock import AsyncMock

DATABASE_URL = "sqlite:///./test.db"

# Must be set before the app is imported, so tests never touch the working database
os.environ["DATABASE_URL"] = DATABASE_URL

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from fastapi.testclient import TestClient

from app.database import Base
from app.dependency import get_db
from app.enum import CurrencyEnum, OperationType
from app.models import Operation, User, Wallet
from main import app

test_engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


def get_test_db() -> Generator[Session, None, None]:
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = get_test_db


@pytest.fixture()
def client():
    yield TestClient(app)


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture()
def db_session() -> Generator[Session, None, None]:
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture()
def make_user(db_session) -> Callable[..., User]:
    def _make_user(login: str = "user") -> User:
        user = User(login=login)
        db_session.add(user)
        db_session.commit()
        return user

    return _make_user


@pytest.fixture()
def make_wallet(db_session) -> Callable[..., Wallet]:
    def _make_wallet(user: User, name: str = "card", balance: Decimal = Decimal(200),
                     currency: CurrencyEnum = CurrencyEnum.UAH) -> Wallet:
        wallet = Wallet(name=name, balance=balance, user_id=user.id, currency=currency)
        db_session.add(wallet)
        db_session.commit()
        return wallet

    return _make_wallet


@pytest.fixture()
def make_operation(db_session) -> Callable[..., Operation]:
    def _make_operation(wallet: Wallet, amount: Decimal = Decimal(50),
                        created_at: datetime = datetime(2026, 1, 15)) -> Operation:
        operation = Operation(wallet_id=wallet.id, type=OperationType.EXPENSE, amount=amount,
                              currency=wallet.currency, created_at=created_at)
        db_session.add(operation)
        db_session.commit()
        return operation

    return _make_operation


@pytest.fixture()
def auth() -> Callable[[User], dict[str, str]]:
    def _auth(user: User) -> dict[str, str]:
        return {"Authorization": f"Bearer {user.login}"}

    return _auth


@pytest.fixture()
def mock_exchange_rate(monkeypatch) -> AsyncMock:
    mock = AsyncMock(return_value=Decimal(40))
    monkeypatch.setattr("app.services.exchange_service.get_exchange_rate", mock)
    monkeypatch.setattr("app.services.operation.get_exchange_rate", mock)
    return mock
