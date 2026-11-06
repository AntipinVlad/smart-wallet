import asyncio
import logging
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import openai
import pytest

from app.ai.categorization.categories import ExpenseCategory, IncomeCategory
from app.ai.categorization.service import ExpenseCategorization, IncomeCategorization, categorize_operation
from app.core.config import settings
from app.database import SessionLocal, engine
from app.enums import OperationType
from app.models import Operation


@pytest.fixture()
def anyio_backend():
    # SelectorEventLoop: async psycopg does not work with ProactorEventLoop on Windows
    return "asyncio", {"loop_factory": asyncio.SelectorEventLoop}


@pytest.fixture()
async def db():
    async with SessionLocal() as session:
        yield session
    # Pooled connections belong to this test's event loop
    await engine.dispose()


def fake_openai(result=None, error: Exception | None = None):
    parse = AsyncMock(return_value=SimpleNamespace(output_parsed=result), side_effect=error)
    return SimpleNamespace(responses=SimpleNamespace(parse=parse))


def stored(db_session, operation: Operation) -> tuple[str | None, str | None]:
    db_session.refresh(operation)
    return operation.category, operation.subcategory


@pytest.mark.anyio
async def test_expense_gets_category_from_model(db, db_session, make_user, make_wallet, make_operation):
    operation = make_operation(make_wallet(make_user()), amount=Decimal(85), description="Сільпо, продукти")
    client = fake_openai(ExpenseCategorization(category=ExpenseCategory.FOOD_GROCERIES))

    # Act
    await categorize_operation(db, client, operation.id)

    # Assert
    assert stored(db_session, operation) == ("food", "groceries")
    request = client.responses.parse.await_args.kwargs
    assert request["model"] == settings.openai_model
    assert request["text_format"] is ExpenseCategorization
    assert "Сільпо, продукти" in request["input"]
    assert "85" in request["input"]


@pytest.mark.anyio
async def test_income_uses_income_categories(db, db_session, make_user, make_wallet, make_operation):
    operation = make_operation(make_wallet(make_user()), type=OperationType.INCOME, description="Salary for October")
    client = fake_openai(IncomeCategorization(category=IncomeCategory.SALARY_SALARY))

    # Act
    await categorize_operation(db, client, operation.id)

    # Assert
    assert stored(db_session, operation) == ("salary", "salary")
    assert client.responses.parse.await_args.kwargs["text_format"] is IncomeCategorization


@pytest.mark.anyio
async def test_operation_without_description_is_other_without_calling_model(
    db, db_session, make_user, make_wallet, make_operation
):
    operation = make_operation(make_wallet(make_user()), description=None)
    client = fake_openai()

    # Act
    await categorize_operation(db, client, operation.id)

    # Assert
    assert stored(db_session, operation) == ("other", "other")
    client.responses.parse.assert_not_awaited()


@pytest.mark.anyio
async def test_categorized_operation_is_not_sent_again(db, db_session, make_user, make_wallet, make_operation):
    # A queue delivers a task at least once: the same task can come twice
    operation = make_operation(make_wallet(make_user()), description="Uber", category="transport")
    client = fake_openai()

    # Act
    await categorize_operation(db, client, operation.id)

    # Assert
    assert stored(db_session, operation) == ("transport", None)
    client.responses.parse.assert_not_awaited()


@pytest.mark.anyio
async def test_transfer_is_not_categorized(db, db_session, make_user, make_wallet, make_operation):
    operation = make_operation(make_wallet(make_user()), type=OperationType.TRANSFER, description="Transfer to cash")
    client = fake_openai()

    # Act
    await categorize_operation(db, client, operation.id)

    # Assert
    assert stored(db_session, operation) == (None, None)
    client.responses.parse.assert_not_awaited()


@pytest.mark.anyio
async def test_model_error_leaves_operation_uncategorized(
    db, db_session, make_user, make_wallet, make_operation, caplog
):
    operation = make_operation(make_wallet(make_user()), description="Netflix")
    client = fake_openai(error=openai.APIConnectionError(request=None))

    # Act
    with caplog.at_level(logging.ERROR, logger="app.ai.categorization.service"):
        await categorize_operation(db, client, operation.id)

    # Assert
    assert stored(db_session, operation) == (None, None)
    assert [record.levelno for record in caplog.records if record.name == "app.ai.categorization.service"] == [
        logging.ERROR
    ]


@pytest.mark.anyio
async def test_empty_model_answer_leaves_operation_uncategorized(
    db, db_session, make_user, make_wallet, make_operation, caplog
):
    # A refusal or a cut answer comes as a successful response without a parsed result
    operation = make_operation(make_wallet(make_user()), description="Netflix")
    client = fake_openai(result=None)

    # Act
    with caplog.at_level(logging.ERROR, logger="app.ai.categorization.service"):
        await categorize_operation(db, client, operation.id)

    # Assert
    assert stored(db_session, operation) == (None, None)
    assert [record.levelno for record in caplog.records if record.name == "app.ai.categorization.service"] == [
        logging.ERROR
    ]


@pytest.mark.anyio
async def test_missing_operation_is_skipped(db, caplog):
    client = fake_openai()

    # Act
    with caplog.at_level(logging.WARNING, logger="app.ai.categorization.service"):
        await categorize_operation(db, client, 999)

    # Assert
    client.responses.parse.assert_not_awaited()
    assert any(record.levelno == logging.WARNING for record in caplog.records)
