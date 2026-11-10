import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app.core.exceptions import ExchangeServiceUnavailableError
from app.enums import CurrencyEnum
from tests.conftest import assert_error


@pytest.mark.parametrize(
    "payload, expected",
    [
        ({"name": "card", "initial_balance": 100, "currency": "usd"}, ("card", Decimal(100), "usd")),
        ({"name": "card"}, ("card", Decimal(0), "uah")),
        ({"name": "  card  "}, ("card", Decimal(0), "uah")),
    ],
    ids=["all_fields", "default_values", "name_is_trimmed"],
)
def test_create_wallet(client, make_user, auth, payload, expected):
    user = make_user()

    # Act
    response = client.post("/api/v1/wallets", json=payload, headers=auth(user))

    # Assert
    assert response.status_code == 201
    wallet = response.json()
    assert (wallet["name"], Decimal(wallet["balance"]), wallet["currency"]) == expected


@pytest.mark.parametrize(
    "currency", ["gbp", "jpy", "chf", "cny", "cad", "aud", "hkd", "sgd", "sek", "nok", "krw", "nzd"]
)
def test_create_wallet_in_popular_currency(client, make_user, auth, currency):
    user = make_user()

    # Act
    response = client.post("/api/v1/wallets", json={"name": "card", "currency": currency}, headers=auth(user))

    # Assert
    assert response.status_code == 201
    assert response.json()["currency"] == currency


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "card", "initial_balance": "10.001"},
        {"name": "card", "initial_balance": "12345678901234567.00"},
        {"name": "card", "initial_balance": -1},
        {"name": " "},
        {"name": "card", "currency": "btc"},
    ],
    ids=["too_many_decimals", "too_many_digits", "negative_balance", "empty_name", "unknown_currency"],
)
def test_create_wallet_rejects_invalid_request(client, make_user, auth, payload):
    user = make_user()

    # Act
    response = client.post("/api/v1/wallets", json=payload, headers=auth(user))

    # Assert
    assert_error(response, 422, "VALIDATION_ERROR")


@pytest.mark.parametrize("concurrent", [False, True], ids=["found_by_check", "found_by_unique_constraint"])
def test_create_wallet_name_taken(client, make_user, make_wallet, auth, monkeypatch, concurrent):
    user = make_user()
    make_wallet(user, name="card")
    if concurrent:
        # Another request created the wallet after the check: only the unique constraint stops it
        monkeypatch.setattr("app.repository.wallets.is_wallet_exist", AsyncMock(return_value=False))

    # Act
    response = client.post("/api/v1/wallets", json={"name": "card"}, headers=auth(user))

    # Assert
    assert_error(response, 409, "WALLET_ALREADY_EXISTS")


def test_create_wallet_same_name_for_different_users(client, make_user, make_wallet, auth):
    make_wallet(make_user(login="owner"), name="card")
    user = make_user(login="other")

    # Act
    response = client.post("/api/v1/wallets", json={"name": "card"}, headers=auth(user))

    # Assert
    assert response.status_code == 201


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


def test_get_balance_sums_own_uah_wallets_without_exchange_rate(
    client, make_user, make_wallet, auth, mock_exchange_rate
):
    user = make_user(login="owner")
    make_wallet(user, name="card", balance=Decimal(100))
    make_wallet(user, name="cash", balance=Decimal(50))
    make_wallet(make_user(login="other"), balance=Decimal(999))

    # Act
    response = client.get("/api/v1/balance", headers=auth(user))

    # Assert
    assert response.status_code == 200
    assert Decimal(response.json()["total_balance"]) == Decimal(150)
    mock_exchange_rate.assert_not_awaited()


def test_get_balance_converts_foreign_currency_to_uah(client, make_user, make_wallet, auth, mock_exchange_rate):
    user = make_user()
    make_wallet(user, name="card", balance=Decimal(100))
    make_wallet(user, name="dollars", balance=Decimal(10), currency=CurrencyEnum.USD)

    # Act
    response = client.get("/api/v1/balance", headers=auth(user))

    # Assert
    assert response.status_code == 200
    assert Decimal(response.json()["total_balance"]) == Decimal(500)
    mock_exchange_rate.assert_awaited_once_with(client.app_state["http_session"], CurrencyEnum.USD, CurrencyEnum.UAH)


def test_get_balance_in_requested_currency(client, make_user, make_wallet, auth, mock_exchange_rate):
    user = make_user()
    make_wallet(user, name="card", balance=Decimal(400))
    make_wallet(user, name="dollars", balance=Decimal(10), currency=CurrencyEnum.USD)
    mock_exchange_rate.return_value = Decimal("0.025")

    # Act
    response = client.get("/api/v1/balance", params={"currency": "usd"}, headers=auth(user))

    # Assert
    assert response.status_code == 200
    assert response.json() == {"total_balance": "20.00", "currency": "usd"}
    mock_exchange_rate.assert_awaited_once_with(client.app_state["http_session"], CurrencyEnum.UAH, CurrencyEnum.USD)


def test_get_balance_is_in_uah_by_default(client, make_user, make_wallet, auth):
    user = make_user()
    make_wallet(user, balance=Decimal(100))

    # Act
    response = client.get("/api/v1/balance", headers=auth(user))

    # Assert
    assert response.status_code == 200
    assert response.json()["currency"] == "uah"


def test_get_balance_rejects_unknown_currency(client, make_user, auth):
    user = make_user()

    # Act
    response = client.get("/api/v1/balance", params={"currency": "btc"}, headers=auth(user))

    # Assert
    assert_error(response, 422, "VALIDATION_ERROR")


def test_get_balance_fetches_one_rate_per_currency(client, make_user, make_wallet, auth, mock_exchange_rate):
    user = make_user()
    make_wallet(user, name="dollars", balance=Decimal(10), currency=CurrencyEnum.USD)
    make_wallet(user, name="savings", balance=Decimal(5), currency=CurrencyEnum.USD)

    # Act
    response = client.get("/api/v1/balance", headers=auth(user))

    # Assert
    assert response.status_code == 200
    assert Decimal(response.json()["total_balance"]) == Decimal(600)
    mock_exchange_rate.assert_awaited_once_with(client.app_state["http_session"], CurrencyEnum.USD, CurrencyEnum.UAH)


def test_get_balance_fetches_rates_of_currencies_concurrently(client, make_user, make_wallet, auth, monkeypatch):
    both_requested = asyncio.Barrier(2)

    async def exchange_rate(http_session, base, target):
        # Requests one after another never meet here: the first one waits for the second until the timeout
        async with asyncio.timeout(1):
            await both_requested.wait()
        return Decimal(40) if base == CurrencyEnum.USD else Decimal(50)

    monkeypatch.setattr("app.services.exchange_service.get_exchange_rate", exchange_rate)
    user = make_user()
    make_wallet(user, name="card", balance=Decimal(100))
    make_wallet(user, name="dollars", balance=Decimal(1), currency=CurrencyEnum.USD)
    make_wallet(user, name="euros", balance=Decimal(1), currency=CurrencyEnum.EUR)

    # Act
    response = client.get("/api/v1/balance", headers=auth(user))

    # Assert
    assert response.status_code == 200
    assert Decimal(response.json()["total_balance"]) == Decimal(190)


def test_get_balance_exchange_service_unavailable(client, make_user, make_wallet, auth, mock_exchange_rate):
    mock_exchange_rate.side_effect = ExchangeServiceUnavailableError()
    user = make_user()
    make_wallet(user, name="dollars", balance=Decimal(1), currency=CurrencyEnum.USD)
    make_wallet(user, name="euros", balance=Decimal(1), currency=CurrencyEnum.EUR)

    # Act
    response = client.get("/api/v1/balance", headers=auth(user))

    # Assert
    assert_error(response, 503, "EXCHANGE_SERVICE_UNAVAILABLE")
