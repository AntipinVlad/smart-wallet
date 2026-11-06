from decimal import Decimal

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from alembic import command
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


def test_migration_normalizes_logins_and_operation_types(alembic_config):
    command.downgrade(alembic_config, "dc30326d7ecd")
    with sync_engine.begin() as connection:
        user_id = connection.scalar(
            text("""INSERT INTO "user" (login, password_hash) VALUES (' Anna ', 'hash') RETURNING id""")
        )
        wallet_id = connection.scalar(
            text(
                "INSERT INTO wallet (name, balance, user_id, currency) VALUES ('card', 0, :user_id, 'UAH') RETURNING id"
            ),
            {"user_id": user_id},
        )
        connection.execute(
            text(
                "INSERT INTO operation (wallet_id, type, amount, currency, created_at) "
                "VALUES (:wallet_id, 'income', 10, 'UAH', now())"
            ),
            {"wallet_id": wallet_id},
        )

    # Act
    command.upgrade(alembic_config, "head")

    # Assert
    with sync_engine.connect() as connection:
        assert connection.scalar(text('SELECT login FROM "user"')) == "anna"
        assert connection.scalar(text("SELECT type FROM operation")) == "INCOME"


@pytest.mark.parametrize("table, column", [("wallet", "balance"), ("operation", "amount")])
def test_money_columns_have_fixed_precision(table, column):
    # Act
    columns = {item["name"]: item["type"] for item in inspect(sync_engine).get_columns(table)}

    # Assert
    assert (columns[column].precision, columns[column].scale) == (18, 2)


def test_balance_keeps_exact_decimal_value(client, db_session, make_user, make_wallet, auth):
    user = make_user()
    wallet = make_wallet(user, balance=Decimal("0.1"))

    # Act
    client.post("/api/v1/operations/income", json={"wallet_id": wallet.id, "amount": "0.2"}, headers=auth(user))

    # Assert
    db_session.refresh(wallet)
    assert str(wallet.balance) == "0.30"


def test_wallet_balance_cannot_be_negative(make_user, make_wallet):
    user = make_user()

    # Act
    with pytest.raises(IntegrityError):
        make_wallet(user, balance=Decimal("-0.01"))
