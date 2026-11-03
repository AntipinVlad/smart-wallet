"""Balance changes racing with a concurrent transaction.

Each test opens its own transaction, locks a wallet row and starts the request in another thread.
While the row is locked the test changes the balance and commits. A request that locks the wallet
before reading it waits and sees the new balance. A request that reads without a lock works with
the old balance and overwrites the concurrent change.
"""
from collections.abc import Callable, Generator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from decimal import Decimal

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.enum import CurrencyEnum
from app.models import Wallet

# How long a request is given to reach the lock. Long enough for a local request, short enough for the suite
REQUEST_START_SECONDS = 0.5
REQUEST_FINISH_SECONDS = 10


@contextmanager
def locked_wallet(wallet_id: int) -> Generator[Wallet, None, None]:
    db = SessionLocal()
    try:
        yield db.query(Wallet).filter(Wallet.id == wallet_id).with_for_update().one()
        db.commit()
    finally:
        db.close()


def assert_still_waiting(request: Future) -> None:
    with pytest.raises(TimeoutError):
        request.result(timeout=REQUEST_START_SECONDS)


def is_wallet_lock_free(wallet_id: int) -> bool:
    db: Session = SessionLocal()
    try:
        db.query(Wallet).filter(Wallet.id == wallet_id).with_for_update(nowait=True).one()
        return True
    except OperationalError:
        return False
    finally:
        db.rollback()
        db.close()


@pytest.fixture()
def run_in_background() -> Generator[Callable[..., Future], None, None]:
    with ThreadPoolExecutor(max_workers=1) as pool:
        yield pool.submit


def test_income_is_not_lost_on_concurrent_balance_change(client, db_session, make_user, make_wallet, auth,
                                                         run_in_background):
    user = make_user()
    wallet = make_wallet(user, balance=Decimal(100))

    # Act
    with locked_wallet(wallet.id) as locked:
        request = run_in_background(client.post, "/api/v1/operations/income",
                                    json={"wallet_name": "card", "amount": 50}, headers=auth(user))
        assert_still_waiting(request)
        locked.balance = Decimal(150)
    response = request.result(timeout=REQUEST_FINISH_SECONDS)

    # Assert
    assert response.status_code == 200
    db_session.refresh(wallet)
    assert wallet.balance == Decimal(200)


def test_expense_sees_balance_changed_concurrently(client, db_session, make_user, make_wallet, auth,
                                                   run_in_background):
    user = make_user()
    wallet = make_wallet(user, balance=Decimal(100))

    # Act
    with locked_wallet(wallet.id) as locked:
        request = run_in_background(client.post, "/api/v1/operations/expense",
                                    json={"wallet_name": "card", "amount": 50}, headers=auth(user))
        assert_still_waiting(request)
        locked.balance = Decimal(30)
    response = request.result(timeout=REQUEST_FINISH_SECONDS)

    # Assert
    assert response.status_code == 400
    db_session.refresh(wallet)
    assert wallet.balance == Decimal(30)


def test_transfer_sees_source_balance_changed_concurrently(client, db_session, make_user, make_wallet, auth,
                                                           run_in_background):
    user = make_user()
    source = make_wallet(user, name="card", balance=Decimal(100))
    target = make_wallet(user, name="cash", balance=Decimal(200))

    # Act
    with locked_wallet(source.id) as locked:
        request = run_in_background(client.post, "/api/v1/operations/transfers",
                                    json={"from_wallet_id": source.id, "to_wallet_id": target.id, "amount": 50},
                                    headers=auth(user))
        assert_still_waiting(request)
        locked.balance = Decimal(30)
    response = request.result(timeout=REQUEST_FINISH_SECONDS)

    # Assert
    assert response.status_code == 400
    db_session.refresh(source)
    db_session.refresh(target)
    assert source.balance == Decimal(30)
    assert target.balance == Decimal(200)


def test_transfer_keeps_target_balance_changed_concurrently(client, db_session, make_user, make_wallet, auth,
                                                            run_in_background):
    user = make_user()
    source = make_wallet(user, name="card", balance=Decimal(100))
    target = make_wallet(user, name="cash", balance=Decimal(200))

    # Act
    with locked_wallet(target.id) as locked:
        request = run_in_background(client.post, "/api/v1/operations/transfers",
                                    json={"from_wallet_id": source.id, "to_wallet_id": target.id, "amount": 50},
                                    headers=auth(user))
        assert_still_waiting(request)
        locked.balance = Decimal(500)
    response = request.result(timeout=REQUEST_FINISH_SECONDS)

    # Assert
    assert response.status_code == 200
    db_session.refresh(target)
    assert target.balance == Decimal(550)


def test_transfer_locks_wallets_in_id_order(client, make_user, make_wallet, auth, run_in_background):
    # Opposite transfers A -> B and B -> A deadlock if each one locks its source wallet first
    user = make_user()
    first = make_wallet(user, name="card", balance=Decimal(100))
    second = make_wallet(user, name="cash", balance=Decimal(100))

    # Act
    with locked_wallet(first.id):
        request = run_in_background(client.post, "/api/v1/operations/transfers",
                                    json={"from_wallet_id": second.id, "to_wallet_id": first.id, "amount": 50},
                                    headers=auth(user))
        assert_still_waiting(request)
        second_is_free = is_wallet_lock_free(second.id)
    request.result(timeout=REQUEST_FINISH_SECONDS)

    # Assert
    assert second_is_free


def test_transfer_gets_exchange_rate_before_waiting_for_lock(client, make_user, make_wallet, auth,
                                                             mock_exchange_rate, run_in_background):
    # Waiting for a slow external API while holding the lock would block every other operation on the wallet
    user = make_user()
    source = make_wallet(user, name="card", balance=Decimal(100), currency=CurrencyEnum.USD)
    target = make_wallet(user, name="cash", balance=Decimal(100), currency=CurrencyEnum.UAH)

    # Act
    with locked_wallet(source.id):
        request = run_in_background(client.post, "/api/v1/operations/transfers",
                                    json={"from_wallet_id": source.id, "to_wallet_id": target.id, "amount": 50},
                                    headers=auth(user))
        assert_still_waiting(request)
        rate_requested_while_waiting = mock_exchange_rate.await_count == 1
    response = request.result(timeout=REQUEST_FINISH_SECONDS)

    # Assert
    assert rate_requested_while_waiting
    assert response.status_code == 200
