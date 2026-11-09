from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.core.exceptions import ExchangeRateNotFoundError
from app.enums import CurrencyEnum
from app.models import Operation
from tests.conftest import assert_error


def add_operation(client, auth, user, endpoint: str, wallet_id: int, **payload):
    return client.post(
        f"/api/v1/operations/{endpoint}", json={"wallet_id": wallet_id, "amount": 50} | payload, headers=auth(user)
    )


def transfer(client, auth, user, from_wallet_id: int, to_wallet_id: int, amount: Decimal | str = "50", **payload):
    return client.post(
        "/api/v1/operations/transfers",
        json={"from_wallet_id": from_wallet_id, "to_wallet_id": to_wallet_id, "amount": str(amount)} | payload,
        headers=auth(user),
    )


PAST_DATE = "2026-11-08T18:30:00+02:00"
FUTURE_DATE = "2999-01-01T00:00:00+00:00"


def test_add_income(client, db_session, make_user, make_wallet, auth):
    user = make_user()
    wallet = make_wallet(user, balance=Decimal(200))
    started = datetime.now(UTC)

    # Act
    response = add_operation(client, auth, user, "income", wallet.id, description="Salary")

    # Assert
    assert response.status_code == 201
    operation = response.json()
    assert operation["type"] == "income"
    assert operation["wallet_id"] == wallet.id
    assert Decimal(operation["amount"]) == Decimal(50)
    assert operation["currency"] == "uah"
    assert operation["description"] == "Salary"
    assert operation["transfer_id"] is None
    assert started <= datetime.fromisoformat(operation["created_at"]) <= datetime.now(UTC)
    db_session.refresh(wallet)
    assert wallet.balance == Decimal(250)


@pytest.mark.parametrize(
    "amount, balance_after", [(50, Decimal(150)), (200, Decimal(0))], ids=["part_of_balance", "whole_balance"]
)
def test_add_expense(client, db_session, make_user, make_wallet, auth, amount, balance_after):
    user = make_user()
    wallet = make_wallet(user, balance=Decimal(200))

    # Act
    response = add_operation(client, auth, user, "expense", wallet.id, amount=amount)

    # Assert
    assert response.status_code == 201
    assert response.json()["type"] == "expense"
    db_session.refresh(wallet)
    assert wallet.balance == balance_after


@pytest.mark.parametrize("endpoint", ["income", "expense"])
def test_add_operation_on_past_date(client, db_session, make_user, make_wallet, auth, endpoint):
    user = make_user()
    wallet = make_wallet(user)

    # Act
    response = add_operation(client, auth, user, endpoint, wallet.id, created_at=PAST_DATE)

    # Assert
    assert response.status_code == 201
    created_at = datetime.fromisoformat(response.json()["created_at"])
    assert created_at == datetime.fromisoformat(PAST_DATE)
    operation = db_session.get(Operation, response.json()["id"])
    assert operation.created_at == datetime.fromisoformat(PAST_DATE)


def test_add_expense_not_enough_money(client, db_session, make_user, make_wallet, auth):
    user = make_user()
    wallet = make_wallet(user, balance=Decimal(200))

    # Act
    response = add_operation(client, auth, user, "expense", wallet.id, amount=250)

    # Assert
    assert_error(response, 409, "INSUFFICIENT_FUNDS")
    db_session.refresh(wallet)
    assert wallet.balance == Decimal(200)


@pytest.mark.parametrize(
    "endpoint, payload",
    [
        ("income", {"amount": 0}),
        ("expense", {"amount": -50}),
        ("income", {"amount": "0.001"}),
        ("expense", {"amount": "12345678901234567.00"}),
        ("income", {"description": "a" * 256}),
        ("expense", {"wallet_id": "card"}),
        ("income", {"created_at": "2026-11-08T18:30:00"}),
        ("expense", {"created_at": FUTURE_DATE}),
    ],
    ids=[
        "zero_amount",
        "negative_amount",
        "too_many_decimals",
        "too_many_digits",
        "long_description",
        "wallet_id_not_a_number",
        "date_without_time_zone",
        "date_in_future",
    ],
)
def test_operation_rejects_invalid_request(client, make_user, make_wallet, auth, endpoint, payload):
    user = make_user()
    wallet = make_wallet(user)

    # Act
    response = add_operation(client, auth, user, endpoint, **({"wallet_id": wallet.id} | payload))

    # Assert
    assert_error(response, 422, "VALIDATION_ERROR")


@pytest.mark.parametrize("endpoint", ["income", "expense"])
@pytest.mark.parametrize("wallet_owner", [None, "other"], ids=["missing_wallet", "wallet_of_other_user"])
def test_operation_on_unavailable_wallet(client, db_session, make_user, make_wallet, auth, endpoint, wallet_owner):
    user = make_user(login="user")
    wallet = make_wallet(make_user(login=wallet_owner), balance=Decimal(200)) if wallet_owner else None

    # Act
    response = add_operation(client, auth, user, endpoint, wallet.id if wallet else 999)

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
    assert {operation["id"] for operation in response.json()["items"]} == {first.id, second.id}


def test_get_operations_by_wallet(client, make_user, make_wallet, make_operation, auth):
    user = make_user()
    wallet = make_wallet(user, name="card")
    operation = make_operation(wallet)
    make_operation(make_wallet(user, name="cash"))

    # Act
    response = client.get("/api/v1/operations", params={"wallet_id": wallet.id}, headers=auth(user))

    # Assert
    assert response.status_code == 200
    assert [(item["id"], item["type"]) for item in response.json()["items"]] == [(operation.id, "expense")]


@pytest.mark.parametrize(
    "wallet_owner, missing_id",
    [(None, 999), (None, 0), ("other", None)],
    ids=["missing_wallet", "zero_id", "wallet_of_other_user"],
)
def test_get_operations_by_unavailable_wallet(client, make_user, make_wallet, auth, wallet_owner, missing_id):
    user = make_user(login="user")
    wallet_id = make_wallet(make_user(login=wallet_owner)).id if wallet_owner else missing_id

    # Act
    response = client.get("/api/v1/operations", params={"wallet_id": wallet_id}, headers=auth(user))

    # Assert
    assert_error(response, 404, "WALLET_NOT_FOUND")


def test_get_operations_by_date_range(client, make_user, make_wallet, make_operation, auth):
    user = make_user()
    wallet = make_wallet(user)
    make_operation(wallet, created_at=datetime(2026, 1, 1, tzinfo=UTC))
    inside = make_operation(wallet, created_at=datetime(2026, 1, 15, tzinfo=UTC))
    make_operation(wallet, created_at=datetime(2026, 2, 1, tzinfo=UTC))

    # Act
    response = client.get(
        "/api/v1/operations",
        params={"date_from": "2026-01-10T00:00:00Z", "date_to": "2026-01-20T00:00:00Z"},
        headers=auth(user),
    )

    # Assert
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [inside.id]


def day(day_of_january: int) -> datetime:
    return datetime(2026, 1, day_of_january, tzinfo=UTC)


def get_operations(client, auth, user, **params):
    return client.get("/api/v1/operations", params=params, headers=auth(user))


def page_info(page: dict) -> dict:
    return {key: page[key] for key in ("page", "size", "total", "pages")}


def test_get_operations_by_pages_newest_first(client, make_user, make_wallet, make_operation, auth):
    user = make_user()
    wallet = make_wallet(user)
    oldest = make_operation(wallet, created_at=day(1))
    # The same time: the id decides the order
    same_time_first = make_operation(wallet, created_at=day(15))
    same_time_second = make_operation(wallet, created_at=day(15))
    newest = make_operation(wallet, created_at=day(31))

    # Act
    first = get_operations(client, auth, user, size=3).json()
    second = get_operations(client, auth, user, size=3, page=2).json()

    # Assert
    assert [item["id"] for item in first["items"]] == [newest.id, same_time_second.id, same_time_first.id]
    assert [item["id"] for item in second["items"]] == [oldest.id]
    assert page_info(first) == {"page": 1, "size": 3, "total": 4, "pages": 2}
    assert page_info(second) == {"page": 2, "size": 3, "total": 4, "pages": 2}


@pytest.mark.parametrize(
    "sort, expected",
    [
        ("created_at,asc", ["small_old", "big_middle", "middle_new"]),
        ("created_at,desc", ["middle_new", "big_middle", "small_old"]),
        ("amount,asc", ["small_old", "middle_new", "big_middle"]),
        ("amount,desc", ["big_middle", "middle_new", "small_old"]),
        # Without a direction: ascending
        ("amount", ["small_old", "middle_new", "big_middle"]),
    ],
)
def test_get_operations_sorted(client, make_user, make_wallet, make_operation, auth, sort, expected):
    user = make_user()
    wallet = make_wallet(user)
    operations = {
        "small_old": make_operation(wallet, amount=Decimal(10), created_at=day(1)),
        "big_middle": make_operation(wallet, amount=Decimal(300), created_at=day(15)),
        "middle_new": make_operation(wallet, amount=Decimal(50), created_at=day(31)),
    }

    # Act
    response = get_operations(client, auth, user, sort=sort)

    # Assert
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [operations[name].id for name in expected]


def test_page_after_the_last_one_is_empty(client, make_user, make_wallet, make_operation, auth):
    user = make_user()
    make_operation(make_wallet(user))

    # Act
    response = get_operations(client, auth, user, page=5)

    # Assert
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["total"] == 1


def test_get_operations_without_operations(client, make_user, auth):
    user = make_user()

    # Act
    response = get_operations(client, auth, user)

    # Assert
    assert response.json() == {"items": [], "page": 1, "size": 20, "total": 0, "pages": 0}


@pytest.mark.parametrize(
    "params",
    [
        {"page": 0},
        {"size": 0},
        {"size": 101},
        {"sort": "id,desc"},
        {"sort": "amount,down"},
        {"date_from": "2026-01-10T00:00:00"},
    ],
    ids=[
        "page_too_small",
        "size_too_small",
        "size_too_large",
        "sort_by_unknown_field",
        "sort_in_unknown_direction",
        "date_without_timezone",
    ],
)
def test_get_operations_with_invalid_params(client, make_user, auth, params):
    user = make_user()

    # Act
    response = get_operations(client, auth, user, **params)

    # Assert
    assert_error(response, 422, "VALIDATION_ERROR")


def test_transfer_same_currency(client, db_session, make_user, make_wallet, auth, mock_exchange_rate):
    user = make_user()
    source = make_wallet(user, name="card", balance=Decimal(200))
    target = make_wallet(user, name="cash", balance=Decimal(10))

    # Act
    response = transfer(client, auth, user, source.id, target.id, Decimal(50))

    # Assert
    assert response.status_code == 201
    outgoing, incoming = response.json()
    assert (outgoing["wallet_id"], outgoing["subcategory"], outgoing["description"]) == (
        source.id,
        "outgoing",
        "Transfer to cash",
    )
    assert (incoming["wallet_id"], incoming["subcategory"], incoming["description"]) == (
        target.id,
        "incoming",
        "Transfer from card",
    )
    assert outgoing["type"] == incoming["type"] == "transfer"
    assert Decimal(outgoing["amount"]) == Decimal(incoming["amount"]) == Decimal(50)
    assert outgoing["transfer_id"] is not None
    assert outgoing["transfer_id"] == incoming["transfer_id"]
    mock_exchange_rate.assert_not_awaited()
    db_session.refresh(source)
    db_session.refresh(target)
    assert (source.balance, target.balance) == (Decimal(150), Decimal(60))


def test_transfer_on_past_date(client, make_user, make_wallet, auth, mock_exchange_rate):
    user = make_user()
    source = make_wallet(user, name="card")
    target = make_wallet(user, name="cash")

    # Act
    response = transfer(client, auth, user, source.id, target.id, created_at=PAST_DATE)

    # Assert
    assert response.status_code == 201
    outgoing, incoming = response.json()
    expected = datetime.fromisoformat(PAST_DATE)
    assert datetime.fromisoformat(outgoing["created_at"]) == datetime.fromisoformat(incoming["created_at"]) == expected


def test_transfer_converts_currency(client, db_session, make_user, make_wallet, auth, mock_exchange_rate):
    user = make_user()
    source = make_wallet(user, name="dollars", balance=Decimal(200), currency=CurrencyEnum.USD)
    target = make_wallet(user, name="card", balance=Decimal(10))

    # Act
    response = transfer(client, auth, user, source.id, target.id, Decimal(50))

    # Assert
    assert response.status_code == 201
    outgoing, incoming = response.json()
    assert (Decimal(outgoing["amount"]), outgoing["currency"]) == (Decimal(50), "usd")
    assert (Decimal(incoming["amount"]), incoming["currency"]) == (Decimal(2000), "uah")
    mock_exchange_rate.assert_awaited_once_with(client.app_state["http_session"], CurrencyEnum.USD, CurrencyEnum.UAH)
    db_session.refresh(source)
    db_session.refresh(target)
    assert (source.balance, target.balance) == (Decimal(150), Decimal(2010))


def test_transfer_exchange_rate_not_found_changes_nothing(
    client, db_session, make_user, make_wallet, auth, mock_exchange_rate
):
    mock_exchange_rate.side_effect = ExchangeRateNotFoundError(CurrencyEnum.USD, CurrencyEnum.UAH)
    user = make_user()
    source = make_wallet(user, name="dollars", balance=Decimal(200), currency=CurrencyEnum.USD)
    target = make_wallet(user, name="card", balance=Decimal(10))

    # Act
    response = transfer(client, auth, user, source.id, target.id)

    # Assert
    assert_error(response, 502, "EXCHANGE_RATE_NOT_FOUND")
    assert client.get("/api/v1/operations", headers=auth(user)).json()["items"] == []
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


@pytest.mark.parametrize(
    "to_same_wallet, payload",
    [(True, {}), (False, {"amount": "-50"}), (False, {"amount": "0.001"}), (False, {"created_at": FUTURE_DATE})],
    ids=["same_wallet", "negative_amount", "too_many_decimals", "date_in_future"],
)
def test_transfer_rejects_invalid_request(client, make_user, make_wallet, auth, to_same_wallet, payload):
    user = make_user()
    source = make_wallet(user, name="card")
    target = source if to_same_wallet else make_wallet(user, name="cash")

    # Act
    response = transfer(client, auth, user, source.id, target.id, **payload)

    # Assert
    assert_error(response, 422, "VALIDATION_ERROR")
