from datetime import datetime
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.enum import CurrencyEnum


def test_add_income_success(client, db_session, make_user, make_wallet, auth):
    user = make_user()
    wallet = make_wallet(user, balance=Decimal(200))

    # Act
    response = client.post(
        "/api/v1/operations/income",
        json={"wallet_name": "card", "amount": 50.0, "description": "Salary"},
        headers=auth(user)
    )

    # Assert
    assert response.status_code == 200
    assert response.json()["type"] == "income"
    assert response.json()["description"] == "Salary"
    assert response.json()["category"] is None
    assert response.json()["wallet_id"] == wallet.id
    assert Decimal(str(response.json()["amount"])) == Decimal(50)
    assert response.json()["currency"] == "uah"
    db_session.refresh(wallet)
    assert wallet.balance == Decimal(250)


@pytest.mark.parametrize("amount", [-50.0, 0])
def test_add_income_not_positive_amount(client, make_user, make_wallet, auth, amount):
    user = make_user()
    make_wallet(user)

    # Act
    response = client.post(
        "/api/v1/operations/income",
        json={"wallet_name": "card", "amount": amount},
        headers=auth(user)
    )

    # Assert
    assert response.status_code == 422


def test_add_income_without_description(client, make_user, make_wallet, auth):
    user = make_user()
    make_wallet(user)

    # Act
    response = client.post(
        "/api/v1/operations/income",
        json={"wallet_name": "card", "amount": 50.0},
        headers=auth(user)
    )

    # Assert
    assert response.status_code == 200
    assert response.json()["description"] is None


def test_add_income_description_too_long(client, make_user, make_wallet, auth):
    user = make_user()
    make_wallet(user)

    # Act
    response = client.post(
        "/api/v1/operations/income",
        json={"wallet_name": "card", "amount": 50.0, "description": "a" * 256},
        headers=auth(user)
    )

    # Assert
    assert response.status_code == 422


@pytest.mark.parametrize("endpoint", ["income", "expense"])
@pytest.mark.parametrize("amount", ["0.001", "12345678901234567.00"])
def test_operation_amount_out_of_precision(client, db_session, make_user, make_wallet, auth, endpoint, amount):
    user = make_user()
    wallet = make_wallet(user, balance=Decimal(200))

    # Act
    response = client.post(
        f"/api/v1/operations/{endpoint}",
        json={"wallet_name": "card", "amount": amount},
        headers=auth(user)
    )

    # Assert
    assert response.status_code == 422
    db_session.refresh(wallet)
    assert wallet.balance == Decimal(200)


def test_add_income_wallet_not_exist(client, make_user, auth):
    user = make_user()

    # Act
    response = client.post(
        "/api/v1/operations/income",
        json={"wallet_name": "card", "amount": 50.0},
        headers=auth(user)
    )

    # Assert
    assert response.status_code == 404


def test_add_income_to_other_users_wallet(client, db_session, make_user, make_wallet, auth):
    wallet = make_wallet(make_user(login="owner"), balance=Decimal(200))
    other = make_user(login="other")

    # Act
    response = client.post(
        "/api/v1/operations/income",
        json={"wallet_name": "card", "amount": 50.0},
        headers=auth(other)
    )

    # Assert
    assert response.status_code == 404
    db_session.refresh(wallet)
    assert wallet.balance == Decimal(200)


def test_add_expense_success(client, db_session, make_user, make_wallet, auth):
    user = make_user()
    wallet = make_wallet(user, balance=Decimal(200))

    # Act
    response = client.post(
        "/api/v1/operations/expense",
        json={"wallet_name": "card", "amount": 50.0, "description": "Food"},
        headers=auth(user)
    )

    # Assert
    assert response.status_code == 200
    assert response.json()["type"] == "expense"
    assert response.json()["description"] == "Food"
    assert response.json()["category"] is None
    assert response.json()["wallet_id"] == wallet.id
    assert Decimal(str(response.json()["amount"])) == Decimal(50)
    db_session.refresh(wallet)
    assert wallet.balance == Decimal(150)


def test_add_expense_whole_balance(client, db_session, make_user, make_wallet, auth):
    user = make_user()
    wallet = make_wallet(user, balance=Decimal(200))

    # Act
    response = client.post(
        "/api/v1/operations/expense",
        json={"wallet_name": "card", "amount": 200.0},
        headers=auth(user)
    )

    # Assert
    assert response.status_code == 200
    db_session.refresh(wallet)
    assert wallet.balance == Decimal(0)


def test_add_expense_not_enough_money(client, db_session, make_user, make_wallet, auth):
    user = make_user()
    wallet = make_wallet(user, balance=Decimal(200))

    # Act
    response = client.post(
        "/api/v1/operations/expense",
        json={"wallet_name": "card", "amount": 250.0},
        headers=auth(user)
    )

    # Assert
    assert response.status_code == 400
    db_session.refresh(wallet)
    assert wallet.balance == Decimal(200)


def test_add_expense_negative(client, make_user, make_wallet, auth):
    user = make_user()
    make_wallet(user)

    # Act
    response = client.post(
        "/api/v1/operations/expense",
        json={"wallet_name": "card", "amount": -50.0},
        headers=auth(user)
    )

    # Assert
    assert response.status_code == 422


def test_add_expense_empty_name(client, make_user, make_wallet, auth):
    user = make_user()
    make_wallet(user)

    # Act
    response = client.post(
        "/api/v1/operations/expense",
        json={"wallet_name": " ", "amount": 50.0},
        headers=auth(user)
    )

    # Assert
    assert response.status_code == 422


def test_add_expense_wallet_not_exist(client, make_user, auth):
    user = make_user()

    # Act
    response = client.post(
        "/api/v1/operations/expense",
        json={"wallet_name": "card", "amount": 50.0},
        headers=auth(user)
    )

    # Assert
    assert response.status_code == 404


def test_add_expense_not_authorized(client):
    # Act
    response = client.post(
        "/api/v1/operations/expense",
        json={"wallet_name": "card", "amount": 50.0},
        headers={"Cookie": "access_token=not_exist"}
    )

    # Assert
    assert response.status_code == 401


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


def test_get_operations_wallet_not_exist(client, make_user, auth):
    user = make_user()

    # Act
    response = client.get("/api/v1/operations", params={"wallet_id": 999}, headers=auth(user))

    # Assert
    assert response.status_code == 404


def test_get_operations_other_users_wallet(client, make_user, make_wallet, auth):
    wallet = make_wallet(make_user(login="owner"))
    other = make_user(login="other")

    # Act
    response = client.get("/api/v1/operations", params={"wallet_id": wallet.id}, headers=auth(other))

    # Assert
    assert response.status_code == 404


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


def test_get_operations_not_authorized(client):
    # Act
    response = client.get("/api/v1/operations", headers={"Cookie": "access_token=not_exist"})

    # Assert
    assert response.status_code == 401


def test_transfer_same_currency(client, db_session, make_user, make_wallet, auth):
    user = make_user()
    source = make_wallet(user, name="card", balance=Decimal(200))
    target = make_wallet(user, name="cash", balance=Decimal(10))

    # Act
    response = client.post(
        "/api/v1/operations/transfers",
        json={"from_wallet_id": source.id, "to_wallet_id": target.id, "amount": 50.0},
        headers=auth(user)
    )

    # Assert
    assert response.status_code == 200
    outgoing, incoming = response.json()
    assert outgoing["type"] == incoming["type"] == "transfer"
    assert outgoing["wallet_id"] == source.id
    assert outgoing["subcategory"] == "outgoing"
    assert outgoing["description"] == "Transfer to cash"
    assert Decimal(str(outgoing["amount"])) == Decimal(50)
    assert incoming["wallet_id"] == target.id
    assert incoming["subcategory"] == "incoming"
    assert incoming["description"] == "Transfer from card"
    assert Decimal(str(incoming["amount"])) == Decimal(50)
    db_session.refresh(source)
    db_session.refresh(target)
    assert source.balance == Decimal(150)
    assert target.balance == Decimal(60)


def test_transfer_converts_currency(client, db_session, make_user, make_wallet, auth, mock_exchange_rate):
    user = make_user()
    source = make_wallet(user, name="dollars", balance=Decimal(200), currency=CurrencyEnum.USD)
    target = make_wallet(user, name="card", balance=Decimal(10))

    # Act
    response = client.post(
        "/api/v1/operations/transfers",
        json={"from_wallet_id": source.id, "to_wallet_id": target.id, "amount": 50.0},
        headers=auth(user)
    )

    # Assert
    assert response.status_code == 200
    outgoing, incoming = response.json()
    assert Decimal(str(outgoing["amount"])) == Decimal(50)
    assert outgoing["currency"] == "usd"
    assert Decimal(str(incoming["amount"])) == Decimal(2000)
    assert incoming["currency"] == "uah"
    mock_exchange_rate.assert_awaited_once_with(CurrencyEnum.USD, CurrencyEnum.UAH)
    db_session.refresh(source)
    db_session.refresh(target)
    assert source.balance == Decimal(150)
    assert target.balance == Decimal(2010)


def test_transfer_operations_share_transfer_id(client, make_user, make_wallet, auth):
    user = make_user()
    source = make_wallet(user, name="card", balance=Decimal(200))
    target = make_wallet(user, name="cash", balance=Decimal(10))
    payload = {"from_wallet_id": source.id, "to_wallet_id": target.id, "amount": 50.0}

    # Act
    first_outgoing, first_incoming = client.post(
        "/api/v1/operations/transfers", json=payload, headers=auth(user)
    ).json()
    second_outgoing, second_incoming = client.post(
        "/api/v1/operations/transfers", json=payload, headers=auth(user)
    ).json()

    # Assert
    assert first_outgoing["transfer_id"] is not None
    assert first_outgoing["transfer_id"] == first_incoming["transfer_id"]
    assert second_outgoing["transfer_id"] == second_incoming["transfer_id"]
    assert first_outgoing["transfer_id"] != second_outgoing["transfer_id"]


def test_operation_outside_transfer_has_no_transfer_id(client, make_user, make_wallet, auth):
    user = make_user()
    make_wallet(user)

    # Act
    response = client.post(
        "/api/v1/operations/income",
        json={"wallet_name": "card", "amount": 50.0},
        headers=auth(user)
    )

    # Assert
    assert response.status_code == 200
    assert response.json()["transfer_id"] is None


def test_transfer_appears_in_history_of_both_wallets(client, make_user, make_wallet, auth):
    user = make_user()
    source = make_wallet(user, name="card", balance=Decimal(200))
    target = make_wallet(user, name="cash", balance=Decimal(10))

    # Act
    client.post(
        "/api/v1/operations/transfers",
        json={"from_wallet_id": source.id, "to_wallet_id": target.id, "amount": 50.0},
        headers=auth(user)
    )
    source_history = client.get("/api/v1/operations", params={"wallet_id": source.id}, headers=auth(user))
    target_history = client.get("/api/v1/operations", params={"wallet_id": target.id}, headers=auth(user))

    # Assert
    assert [item["subcategory"] for item in source_history.json()] == ["outgoing"]
    assert [item["subcategory"] for item in target_history.json()] == ["incoming"]


def test_transfer_exchange_rate_not_found_changes_nothing(client, db_session, make_user, make_wallet, auth,
                                                          mock_exchange_rate):
    mock_exchange_rate.side_effect = HTTPException(status_code=404, detail="Exchange rate not found")
    user = make_user()
    source = make_wallet(user, name="dollars", balance=Decimal(200), currency=CurrencyEnum.USD)
    target = make_wallet(user, name="card", balance=Decimal(10))

    # Act
    response = client.post(
        "/api/v1/operations/transfers",
        json={"from_wallet_id": source.id, "to_wallet_id": target.id, "amount": 50.0},
        headers=auth(user)
    )
    history = client.get("/api/v1/operations", headers=auth(user))

    # Assert
    assert response.status_code == 404
    assert history.json() == []
    db_session.refresh(source)
    db_session.refresh(target)
    assert source.balance == Decimal(200)
    assert target.balance == Decimal(10)


def test_transfer_not_enough_money(client, db_session, make_user, make_wallet, auth):
    user = make_user()
    source = make_wallet(user, name="card", balance=Decimal(20))
    target = make_wallet(user, name="cash", balance=Decimal(10))

    # Act
    response = client.post(
        "/api/v1/operations/transfers",
        json={"from_wallet_id": source.id, "to_wallet_id": target.id, "amount": 50.0},
        headers=auth(user)
    )

    # Assert
    assert response.status_code == 400
    db_session.refresh(source)
    assert source.balance == Decimal(20)


def test_transfer_wallet_not_exist(client, make_user, make_wallet, auth):
    user = make_user()
    source = make_wallet(user)

    # Act
    response = client.post(
        "/api/v1/operations/transfers",
        json={"from_wallet_id": source.id, "to_wallet_id": 999, "amount": 50.0},
        headers=auth(user)
    )

    # Assert
    assert response.status_code == 404


def test_transfer_to_other_users_wallet(client, db_session, make_user, make_wallet, auth):
    user = make_user(login="owner")
    source = make_wallet(user, name="card", balance=Decimal(200))
    target = make_wallet(make_user(login="other"), name="foreign")

    # Act
    response = client.post(
        "/api/v1/operations/transfers",
        json={"from_wallet_id": source.id, "to_wallet_id": target.id, "amount": 50.0},
        headers=auth(user)
    )

    # Assert
    assert response.status_code == 404
    db_session.refresh(source)
    assert source.balance == Decimal(200)


def test_transfer_to_same_wallet(client, make_user, make_wallet, auth):
    user = make_user()
    wallet = make_wallet(user)

    # Act
    response = client.post(
        "/api/v1/operations/transfers",
        json={"from_wallet_id": wallet.id, "to_wallet_id": wallet.id, "amount": 50.0},
        headers=auth(user)
    )

    # Assert
    assert response.status_code == 422


def test_transfer_negative_amount(client, make_user, make_wallet, auth):
    user = make_user()
    source = make_wallet(user, name="card")
    target = make_wallet(user, name="cash")

    # Act
    response = client.post(
        "/api/v1/operations/transfers",
        json={"from_wallet_id": source.id, "to_wallet_id": target.id, "amount": -50.0},
        headers=auth(user)
    )

    # Assert
    assert response.status_code == 422


def test_transfer_amount_out_of_precision(client, make_user, make_wallet, auth):
    user = make_user()
    source = make_wallet(user, name="card")
    target = make_wallet(user, name="cash")

    # Act
    response = client.post(
        "/api/v1/operations/transfers",
        json={"from_wallet_id": source.id, "to_wallet_id": target.id, "amount": "0.001"},
        headers=auth(user)
    )

    # Assert
    assert response.status_code == 422


def test_transfer_not_authorized(client):
    # Act
    response = client.post(
        "/api/v1/operations/transfers",
        json={"from_wallet_id": 1, "to_wallet_id": 2, "amount": 50.0},
        headers={"Cookie": "access_token=not_exist"}
    )

    # Assert
    assert response.status_code == 401
