from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.enum import CurrencyEnum


def test_create_wallet_success(client, make_user, auth):
    user = make_user()

    # Act
    response = client.post(
        "/api/v1/wallets",
        json={"name": "card", "initial_balance": 100, "currency": "usd"},
        headers=auth(user)
    )

    # Assert
    assert response.status_code == 201
    assert response.json()["name"] == "card"
    assert Decimal(str(response.json()["balance"])) == Decimal(100)
    assert response.json()["currency"] == "usd"


def test_create_wallet_default_values(client, make_user, auth):
    user = make_user()

    # Act
    response = client.post("/api/v1/wallets", json={"name": "card"}, headers=auth(user))

    # Assert
    assert response.status_code == 201
    assert Decimal(str(response.json()["balance"])) == Decimal(0)
    assert response.json()["currency"] == "uah"


def test_create_wallet_trims_name(client, make_user, auth):
    user = make_user()

    # Act
    response = client.post("/api/v1/wallets", json={"name": "  card  "}, headers=auth(user))

    # Assert
    assert response.status_code == 201
    assert response.json()["name"] == "card"


def test_create_wallet_already_exists(client, make_user, make_wallet, auth):
    user = make_user()
    make_wallet(user, name="card")

    # Act
    response = client.post("/api/v1/wallets", json={"name": "card"}, headers=auth(user))

    # Assert
    assert response.status_code == 400


def test_create_wallet_name_taken_by_concurrent_request(client, make_user, make_wallet, auth, monkeypatch):
    user = make_user()
    make_wallet(user, name="card")
    monkeypatch.setattr("app.repository.wallets.is_wallet_exist", lambda *args: False)

    # Act
    response = client.post("/api/v1/wallets", json={"name": "card"}, headers=auth(user))

    # Assert
    assert response.status_code == 400


@pytest.mark.parametrize("initial_balance", ["10.001", "12345678901234567.00"])
def test_create_wallet_balance_out_of_precision(client, make_user, auth, initial_balance):
    user = make_user()

    # Act
    response = client.post(
        "/api/v1/wallets",
        json={"name": "card", "initial_balance": initial_balance},
        headers=auth(user)
    )

    # Assert
    assert response.status_code == 422


def test_create_wallet_same_name_for_different_users(client, make_user, make_wallet, auth):
    make_wallet(make_user(login="owner"), name="card")
    user = make_user(login="other")

    # Act
    response = client.post("/api/v1/wallets", json={"name": "card"}, headers=auth(user))

    # Assert
    assert response.status_code == 201


def test_create_wallet_negative_balance(client, make_user, auth):
    user = make_user()

    # Act
    response = client.post(
        "/api/v1/wallets",
        json={"name": "card", "initial_balance": -1},
        headers=auth(user)
    )

    # Assert
    assert response.status_code == 422


def test_create_wallet_empty_name(client, make_user, auth):
    user = make_user()

    # Act
    response = client.post("/api/v1/wallets", json={"name": " "}, headers=auth(user))

    # Assert
    assert response.status_code == 422


def test_create_wallet_unknown_currency(client, make_user, auth):
    user = make_user()

    # Act
    response = client.post(
        "/api/v1/wallets",
        json={"name": "card", "currency": "gbp"},
        headers=auth(user)
    )

    # Assert
    assert response.status_code == 422


def test_create_wallet_not_authorized(client):
    # Act
    response = client.post(
        "/api/v1/wallets",
        json={"name": "card"},
        headers={"Cookie": "access_token=not_exist"}
    )

    # Assert
    assert response.status_code == 401


def test_get_wallets_returns_only_own_wallets(client, make_user, make_wallet, auth):
    user = make_user(login="owner")
    make_wallet(user, name="card")
    make_wallet(user, name="cash")
    make_wallet(make_user(login="other"), name="foreign")

    # Act
    response = client.get("/api/v1/wallets", headers=auth(user))

    # Assert
    assert response.status_code == 200
    assert {wallet["name"] for wallet in response.json()} == {"card", "cash"}


def test_get_wallets_empty(client, make_user, auth):
    user = make_user()

    # Act
    response = client.get("/api/v1/wallets", headers=auth(user))

    # Assert
    assert response.status_code == 200
    assert response.json() == []


def test_get_balance_without_wallets(client, make_user, auth):
    user = make_user()

    # Act
    response = client.get("/api/v1/balance", headers=auth(user))

    # Assert
    assert response.status_code == 200
    assert Decimal(str(response.json()["total_balance"])) == Decimal(0)


def test_get_balance_uah_wallets_do_not_use_exchange_rate(client, make_user, make_wallet, auth, mock_exchange_rate):
    user = make_user()
    make_wallet(user, name="card", balance=Decimal(100))
    make_wallet(user, name="cash", balance=Decimal(50))

    # Act
    response = client.get("/api/v1/balance", headers=auth(user))

    # Assert
    assert response.status_code == 200
    assert Decimal(str(response.json()["total_balance"])) == Decimal(150)
    mock_exchange_rate.assert_not_awaited()


def test_get_balance_converts_foreign_currency_to_uah(client, make_user, make_wallet, auth, mock_exchange_rate):
    user = make_user()
    make_wallet(user, name="card", balance=Decimal(100))
    make_wallet(user, name="dollars", balance=Decimal(10), currency=CurrencyEnum.USD)

    # Act
    response = client.get("/api/v1/balance", headers=auth(user))

    # Assert
    assert response.status_code == 200
    assert Decimal(str(response.json()["total_balance"])) == Decimal(500)
    mock_exchange_rate.assert_awaited_once_with(CurrencyEnum.USD, CurrencyEnum.UAH)


def test_get_balance_exchange_rate_not_found(client, make_user, make_wallet, auth, mock_exchange_rate):
    mock_exchange_rate.side_effect = HTTPException(status_code=404, detail="Exchange rate not found")
    user = make_user()
    make_wallet(user, currency=CurrencyEnum.USD)

    # Act
    response = client.get("/api/v1/balance", headers=auth(user))

    # Assert
    assert response.status_code == 404


def test_get_balance_ignores_other_users_wallets(client, make_user, make_wallet, auth):
    user = make_user(login="owner")
    make_wallet(user, balance=Decimal(100))
    make_wallet(make_user(login="other"), balance=Decimal(999))

    # Act
    response = client.get("/api/v1/balance", headers=auth(user))

    # Assert
    assert Decimal(str(response.json()["total_balance"])) == Decimal(100)


def test_get_balance_not_authorized(client):
    # Act
    response = client.get("/api/v1/balance", headers={"Cookie": "access_token=not_exist"})

    # Assert
    assert response.status_code == 401
