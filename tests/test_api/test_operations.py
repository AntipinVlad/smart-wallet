from datetime import datetime
from decimal import Decimal

import pytest

from app.core.exceptions import ExchangeRateNotFoundError
from app.enums import CurrencyEnum
from tests.conftest import assert_error


def add_operation(client, auth, user, endpoint: str, **payload):
    return client.post(f"/api/v1/operations/{endpoint}", json={"wallet_name": "card", "amount": 50} | payload,
                       headers=auth(user))


def transfer(client, auth, user, from_wallet_id: int, to_wallet_id: int, amount: Decimal | str = "50"):
    return client.post("/api/v1/operations/transfers",
                       json={"from_wallet_id": from_wallet_id, "to_wallet_id": to_wallet_id, "amount": str(amount)},
                       headers=auth(user))


def test_add_income(client, db_session, make_user, make_wallet, auth):
    user = make_user()
    wallet = make_wallet(user, balance=Decimal(200))

    # Act
    response = add_operation(client, auth, user, "income", description="Salary")

    # Assert
    assert response.status_code == 200
    operation = response.json()
    assert operation["type"] == "income"
    assert operation["wallet_id"] == wallet.id
    assert Decimal(operation["amount"]) == Decimal(50)
    assert operation["currency"] == "uah"
    assert operation["description"] == "Salary"
    assert operation["transfer_id"] is None
    db_session.refresh(wallet)
    assert wallet.balance == Decimal(250)


@pytest.mark.parametrize("amount, balance_after", [(50, Decimal(150)), (200, Decimal(0))],
                         ids=["part_of_balance", "whole_balance"])
def test_add_expense(client, db_session, make_user, make_wallet, auth, amount, balance_after):
    user = make_user()
    wallet = make_wallet(user, balance=Decimal(200))

    # Act
    response = add_operation(client, auth, user, "expense", amount=amount)

    # Assert
    assert response.status_code == 200
    assert response.json()["type"] == "expense"
    db_session.refresh(wallet)
    assert wallet.balance == balance_after


def test_add_expense_not_enough_money(client, db_session, make_user, make_wallet, auth):
    user = make_user()
    wallet = make_wallet(user, balance=Decimal(200))

    # Act
    response = add_operation(client, auth, user, "expense", amount=250)

    # Assert
    assert_error(response, 409, "INSUFFICIENT_FUNDS")
    db_session.refresh(wallet)
    assert wallet.balance == Decimal(200)


@pytest.mark.parametrize("endpoint, payload", [
    ("income", {"amount": 0}),
    ("expense", {"amount": -50}),
    ("income", {"amount": "0.001"}),
    ("expense", {"amount": "12345678901234567.00"}),
    ("income", {"description": "a" * 256}),
    ("expense", {"wallet_name": " "}),
], ids=["zero_amount", "negative_amount", "too_many_decimals", "too_many_digits", "long_description",
        "empty_wallet_name"])
def test_operation_rejects_invalid_request(client, make_user, make_wallet, auth, endpoint, payload):
    user = make_user()
    make_wallet(user)

    # Act
    response = add_operation(client, auth, user, endpoint, **payload)

    # Assert
    assert_error(response, 422, "VALIDATION_ERROR")


@pytest.mark.parametrize("endpoint", ["income", "expense"])
@pytest.mark.parametrize("wallet_owner", [None, "other"], ids=["missing_wallet", "wallet_of_other_user"])
def test_operation_on_unavailable_wallet(client, db_session, make_user, make_wallet, auth, endpoint, wallet_owner):
    user = make_user(login="user")
    wallet = make_wallet(make_user(login=wallet_owner), balance=Decimal(200)) if wallet_owner else None

    # Act
    response = add_operation(client, auth, user, endpoint)

    # Assert
    assert_error(response, 404, "WALLET_NOT_FOUND")
    if wallet:
        db_session.refresh(wallet)
        assert wallet.balance == Decimal(200)


def test_get_operations_for_all_own_wallets(client, make_user, make_wallet, make_operation, auth):
    user = make_user(login="owner")
    first = make_operation(make_wallet(user, name="card"))
    second = make_operation(make_wallet(user, name="cash"))
    make_operation(make_wallet(make_user(login="other"), name="foreign"))

    # Act
    response = client.get("/api/v1/operations", headers=auth(user))

    # Assert
    assert response.status_code == 200
    assert {operation["id"] for operation in response.json()} == {first.id, second.id}


def test_get_operations_by_wallet(client, make_user, make_wallet, make_operation, auth):
    user = make_user()
    wallet = make_wallet(user, name="card")
    operation = make_operation(wallet)
    make_operation(make_wallet(user, name="cash"))

    # Act
    response = client.get("/api/v1/operations", params={"wallet_id": wallet.id}, headers=auth(user))

    # Assert
    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [operation.id]


@pytest.mark.parametrize("wallet_owner", [None, "other"], ids=["missing_wallet", "wallet_of_other_user"])
def test_get_operations_by_unavailable_wallet(client, make_user, make_wallet, auth, wallet_owner):
    user = make_user(login="user")
    wallet_id = make_wallet(make_user(login=wallet_owner)).id if wallet_owner else 999

    # Act
    response = client.get("/api/v1/operations", params={"wallet_id": wallet_id}, headers=auth(user))

    # Assert
    assert_error(response, 404, "WALLET_NOT_FOUND")


def test_get_operations_by_date_range(client, make_user, make_wallet, make_operation, auth):
    user = make_user()
    wallet = make_wallet(user)
    make_operation(wallet, created_at=datetime(2026, 1, 1))
    inside = make_operation(wallet, created_at=datetime(2026, 1, 15))
    make_operation(wallet, created_at=datetime(2026, 2, 1))

    # Act
    response = client.get(
        "/api/v1/operations",
        params={"date_from": "2026-01-10T00:00:00", "date_to": "2026-01-20T00:00:00"},
        headers=auth(user)
    )

    # Assert
    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [inside.id]


def test_transfer_same_currency(client, db_session, make_user, make_wallet, auth, mock_exchange_rate):
    user = make_user()
    source = make_wallet(user, name="card", balance=Decimal(200))
    target = make_wallet(user, name="cash", balance=Decimal(10))

    # Act
    response = transfer(client, auth, user, source.id, target.id, Decimal(50))

    # Assert
    assert response.status_code == 200
    outgoing, incoming = response.json()
    assert (outgoing["wallet_id"], outgoing["subcategory"], outgoing["description"]) == \
        (source.id, "outgoing", "Transfer to cash")
    assert (incoming["wallet_id"], incoming["subcategory"], incoming["description"]) == \
        (target.id, "incoming", "Transfer from card")
    assert outgoing["type"] == incoming["type"] == "transfer"
    assert Decimal(outgoing["amount"]) == Decimal(incoming["amount"]) == Decimal(50)
    # Both sides of one transfer are linked, so the history can show them as one transfer
    assert outgoing["transfer_id"] is not None
    assert outgoing["transfer_id"] == incoming["transfer_id"]
    mock_exchange_rate.assert_not_awaited()
    db_session.refresh(source)
    db_session.refresh(target)
    assert (source.balance, target.balance) == (Decimal(150), Decimal(60))


def test_transfer_converts_currency(client, db_session, make_user, make_wallet, auth, mock_exchange_rate):
    user = make_user()
    source = make_wallet(user, name="dollars", balance=Decimal(200), currency=CurrencyEnum.USD)
    target = make_wallet(user, name="card", balance=Decimal(10))

    # Act
    response = transfer(client, auth, user, source.id, target.id, Decimal(50))

    # Assert
    assert response.status_code == 200
    outgoing, incoming = response.json()
    assert (Decimal(outgoing["amount"]), outgoing["currency"]) == (Decimal(50), "usd")
    assert (Decimal(incoming["amount"]), incoming["currency"]) == (Decimal(2000), "uah")
    mock_exchange_rate.assert_awaited_once_with(client.app_state["http_session"], CurrencyEnum.USD, CurrencyEnum.UAH)
    db_session.refresh(source)
    db_session.refresh(target)
    assert (source.balance, target.balance) == (Decimal(150), Decimal(2010))


def test_transfer_exchange_rate_not_found_changes_nothing(client, db_session, make_user, make_wallet, auth,
                                                          mock_exchange_rate):
    mock_exchange_rate.side_effect = ExchangeRateNotFoundError(CurrencyEnum.USD, CurrencyEnum.UAH)
    user = make_user()
    source = make_wallet(user, name="dollars", balance=Decimal(200), currency=CurrencyEnum.USD)
    target = make_wallet(user, name="card", balance=Decimal(10))

    # Act
    response = transfer(client, auth, user, source.id, target.id)

    # Assert
    assert_error(response, 502, "EXCHANGE_RATE_NOT_FOUND")
    assert client.get("/api/v1/operations", headers=auth(user)).json() == []
    db_session.refresh(source)
    db_session.refresh(target)
    assert (source.balance, target.balance) == (Decimal(200), Decimal(10))


def test_transfer_not_enough_money(client, db_session, make_user, make_wallet, auth):
    user = make_user()
    source = make_wallet(user, name="card", balance=Decimal(20))
    target = make_wallet(user, name="cash", balance=Decimal(10))

    # Act
    response = transfer(client, auth, user, source.id, target.id, Decimal(50))

    # Assert
    assert_error(response, 409, "INSUFFICIENT_FUNDS")
    db_session.refresh(source)
    assert source.balance == Decimal(20)


@pytest.mark.parametrize("target_owner", [None, "other"], ids=["missing_wallet", "wallet_of_other_user"])
def test_transfer_to_unavailable_wallet(client, db_session, make_user, make_wallet, auth, target_owner):
    user = make_user(login="user")
    source = make_wallet(user, name="card", balance=Decimal(200))
    target_id = make_wallet(make_user(login=target_owner), name="foreign").id if target_owner else 999

    # Act
    response = transfer(client, auth, user, source.id, target_id)

    # Assert
    assert_error(response, 404, "WALLET_NOT_FOUND")
    db_session.refresh(source)
    assert source.balance == Decimal(200)


@pytest.mark.parametrize("to_same_wallet, amount", [(True, "50"), (False, "-50"), (False, "0.001")],
                         ids=["same_wallet", "negative_amount", "too_many_decimals"])
def test_transfer_rejects_invalid_request(client, make_user, make_wallet, auth, to_same_wallet, amount):
    user = make_user()
    source = make_wallet(user, name="card")
    target = source if to_same_wallet else make_wallet(user, name="cash")

    # Act
    response = transfer(client, auth, user, source.id, target.id, amount)

    # Assert
    assert_error(response, 422, "VALIDATION_ERROR")
