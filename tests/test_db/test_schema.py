from decimal import Decimal

import pytest
from alembic import command
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from tests.conftest import sync_engine


def test_models_match_migrations(alembic_config):
    # Act / Assert
    command.check(alembic_config)


def test_migrations_can_be_rolled_back(alembic_config):
    # Act
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")

    # Assert
    command.check(alembic_config)


@pytest.mark.parametrize("table, column", [("wallet", "balance"), ("operation", "amount")])
def test_money_columns_have_fixed_precision(table, column):
    # Act
    columns = {item["name"]: item["type"] for item in inspect(sync_engine).get_columns(table)}

    # Assert
    assert (columns[column].precision, columns[column].scale) == (18, 2)


@pytest.mark.parametrize("table, column", [
    ("user", "login"),
    ("wallet", "name"),
    ("operation", "category"),
    ("operation", "subcategory"),
    ("operation", "description"),
])
def test_text_columns_are_limited_to_255_characters(table, column):
    # Act
    columns = {item["name"]: item["type"] for item in inspect(sync_engine).get_columns(table)}

    # Assert
    assert columns[column].length == 255


def test_wallet_name_unique_per_user(make_user, make_wallet):
    user = make_user()
    make_wallet(user, name="card")

    # Act
    with pytest.raises(IntegrityError):
        make_wallet(user, name="card")


def test_wallet_name_can_repeat_for_different_users(make_user, make_wallet):
    make_wallet(make_user(login="owner"), name="card")

    # Act
    wallet = make_wallet(make_user(login="other"), name="card")

    # Assert
    assert wallet.id is not None


def test_balance_keeps_exact_decimal_value(client, db_session, make_user, make_wallet, auth):
    user = make_user()
    wallet = make_wallet(user, balance=Decimal("0.1"))

    # Act
    client.post(
        "/api/v1/operations/income",
        json={"wallet_name": "card", "amount": "0.2"},
        headers=auth(user)
    )

    # Assert
    db_session.refresh(wallet)
    assert str(wallet.balance) == "0.30"


def test_wallet_balance_cannot_be_negative(make_user, make_wallet):
    user = make_user()

    # Act
    with pytest.raises(IntegrityError):
        make_wallet(user, balance=Decimal("-0.01"))
