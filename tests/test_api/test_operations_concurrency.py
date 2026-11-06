# Each test locks a wallet row, starts the request in another thread, then changes the balance and commits.
# A request that locks before reading sees the new balance; one that does not overwrites the change

import time
from collections.abc import Generator
from concurrent.futures import Future
from contextlib import contextmanager
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.enums import CurrencyEnum
from app.models import Wallet
from tests.conftest import OTHER_REQUEST_SECONDS, SyncSessionLocal, assert_error

REQUEST_START_SECONDS = 10
REQUEST_FINISH_SECONDS = 10


@contextmanager
def locked_wallet(wallet_id: int) -> Generator[Wallet, None, None]:
    db = SyncSessionLocal()
    try:
        yield db.scalars(select(Wallet).where(Wallet.id == wallet_id).with_for_update()).one()
        db.commit()
    finally:
        db.close()


def wait_until_blocked(request: Future) -> None:
    # Asks Postgres instead of timing the request: on a slow machine a missing lock cannot look like a wait
    deadline = time.monotonic() + REQUEST_START_SECONDS
    with SyncSessionLocal() as db:
        while not request.done() and time.monotonic() < deadline:
            waiting = db.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND wait_event_type = 'Lock' AND pid <> pg_backend_pid()"
                )
            )
            if waiting:
                return
            db.rollback()
            time.sleep(0.01)
    if request.done():
        pytest.fail(f"The request finished without waiting for the lock: {request.result().status_code}")
    pytest.fail(f"The request did not reach the lock within {REQUEST_START_SECONDS} s")


def is_wallet_lock_free(wallet_id: int) -> bool:
    db: Session = SyncSessionLocal()
    try:
        db.scalars(select(Wallet).where(Wallet.id == wallet_id).with_for_update(nowait=True)).one()
        return True
    except OperationalError:
        return False
    finally:
        db.rollback()
        db.close()


def test_income_is_not_lost_on_concurrent_balance_change(
    client, db_session, make_user, make_wallet, auth, run_in_background
):
    user = make_user()
    wallet = make_wallet(user, balance=Decimal(100))

    # Act
    with locked_wallet(wallet.id) as locked:
        request = run_in_background(
            client.post, "/api/v1/operations/income", json={"wallet_id": wallet.id, "amount": 50}, headers=auth(user)
        )
        wait_until_blocked(request)
        locked.balance = Decimal(150)
    response = request.result(timeout=REQUEST_FINISH_SECONDS)

    # Assert
    assert response.status_code == 201
    db_session.refresh(wallet)
    assert wallet.balance == Decimal(200)


def test_expense_sees_balance_changed_concurrently(client, db_session, make_user, make_wallet, auth, run_in_background):
    user = make_user()
    wallet = make_wallet(user, balance=Decimal(100))

    # Act
    with locked_wallet(wallet.id) as locked:
        request = run_in_background(
            client.post, "/api/v1/operations/expense", json={"wallet_id": wallet.id, "amount": 50}, headers=auth(user)
        )
        wait_until_blocked(request)
        locked.balance = Decimal(30)
    response = request.result(timeout=REQUEST_FINISH_SECONDS)

    # Assert
    assert_error(response, 409, "INSUFFICIENT_FUNDS")
    db_session.refresh(wallet)
    assert wallet.balance == Decimal(30)


def test_transfer_sees_source_balance_changed_concurrently(
    client, db_session, make_user, make_wallet, auth, run_in_background
):
    user = make_user()
    source = make_wallet(user, name="card", balance=Decimal(100))
    target = make_wallet(user, name="cash", balance=Decimal(200))

    # Act
    with locked_wallet(source.id) as locked:
        request = run_in_background(
            client.post,
            "/api/v1/operations/transfers",
            json={"from_wallet_id": source.id, "to_wallet_id": target.id, "amount": 50},
            headers=auth(user),
        )
        wait_until_blocked(request)
        locked.balance = Decimal(30)
    response = request.result(timeout=REQUEST_FINISH_SECONDS)

    # Assert
    assert_error(response, 409, "INSUFFICIENT_FUNDS")
    db_session.refresh(source)
    db_session.refresh(target)
    assert source.balance == Decimal(30)
    assert target.balance == Decimal(200)


def test_transfer_keeps_target_balance_changed_concurrently(
    client, db_session, make_user, make_wallet, auth, run_in_background
):
    user = make_user()
    source = make_wallet(user, name="card", balance=Decimal(100))
    target = make_wallet(user, name="cash", balance=Decimal(200))

    # Act
    with locked_wallet(target.id) as locked:
        request = run_in_background(
            client.post,
            "/api/v1/operations/transfers",
            json={"from_wallet_id": source.id, "to_wallet_id": target.id, "amount": 50},
            headers=auth(user),
        )
        wait_until_blocked(request)
        locked.balance = Decimal(500)
    response = request.result(timeout=REQUEST_FINISH_SECONDS)

    # Assert
    assert response.status_code == 201
    db_session.refresh(target)
    assert target.balance == Decimal(550)


def test_transfer_locks_wallets_in_id_order(client, make_user, make_wallet, auth, run_in_background):
    # A -> B and B -> A deadlock if each locks its source wallet first
    user = make_user()
    first = make_wallet(user, name="card", balance=Decimal(100))
    second = make_wallet(user, name="cash", balance=Decimal(100))

    # Act
    with locked_wallet(first.id):
        request = run_in_background(
            client.post,
            "/api/v1/operations/transfers",
            json={"from_wallet_id": second.id, "to_wallet_id": first.id, "amount": 50},
            headers=auth(user),
        )
        wait_until_blocked(request)
        second_is_free = is_wallet_lock_free(second.id)
    request.result(timeout=REQUEST_FINISH_SECONDS)

    # Assert
    assert second_is_free


def test_transfer_gets_exchange_rate_before_waiting_for_lock(
    client, make_user, make_wallet, auth, mock_exchange_rate, run_in_background
):
    user = make_user()
    source = make_wallet(user, name="card", balance=Decimal(100), currency=CurrencyEnum.USD)
    target = make_wallet(user, name="cash", balance=Decimal(100), currency=CurrencyEnum.UAH)

    # Act
    with locked_wallet(source.id):
        request = run_in_background(
            client.post,
            "/api/v1/operations/transfers",
            json={"from_wallet_id": source.id, "to_wallet_id": target.id, "amount": 50},
            headers=auth(user),
        )
        wait_until_blocked(request)
        rate_requested_while_waiting = mock_exchange_rate.await_count == 1
    response = request.result(timeout=REQUEST_FINISH_SECONDS)

    # Assert
    assert rate_requested_while_waiting
    assert response.status_code == 201


def test_transfer_waiting_for_lock_does_not_block_other_requests(
    client, make_user, make_wallet, auth, run_in_background
):
    # All requests share one event loop: waiting for the database must not stop it
    user = make_user()
    source = make_wallet(user, name="card", balance=Decimal(100))
    target = make_wallet(user, name="cash", balance=Decimal(100))

    # Act
    with locked_wallet(source.id):
        transfer = run_in_background(
            client.post,
            "/api/v1/operations/transfers",
            json={"from_wallet_id": source.id, "to_wallet_id": target.id, "amount": 50},
            headers=auth(user),
        )
        wait_until_blocked(transfer)
        other = run_in_background(client.get, "/api/v1/users/me", headers=auth(user))
        other_response = other.result(timeout=OTHER_REQUEST_SECONDS)
    transfer.result(timeout=REQUEST_FINISH_SECONDS)

    # Assert
    assert other_response.status_code == 200
