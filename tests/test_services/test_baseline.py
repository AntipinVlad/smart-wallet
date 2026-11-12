import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.database import SessionLocal, engine
from app.enums import OperationType, PeriodPreset
from app.schemas import AnalyticsQuery, PeriodResponse
from app.services.analytics import get_baseline

pytestmark = pytest.mark.anyio

OCTOBER = AnalyticsQuery(period=PeriodPreset.MONTH, date=date(2026, 10, 15))


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


class NoRates:
    """Hryvnia operations in a hryvnia report need no exchange rates."""

    async def ensure_rates(self, db, days) -> None:
        assert not days


def day(month: int, day_of_month: int = 10) -> datetime:
    return datetime(2026, month, day_of_month, 12, tzinfo=UTC)


async def test_average_of_three_previous_periods(db, make_user, make_wallet, make_operation):
    user = make_user()
    card = make_wallet(user)
    make_operation(card, Decimal(10), day(6), category="health", subcategory="pharmacy")
    make_operation(card, Decimal(300), day(9), category="food", subcategory="groceries")
    make_operation(card, Decimal(600), day(8), category="food", subcategory="groceries")
    make_operation(card, Decimal(90), day(7), category="transport", subcategory="taxi")
    make_operation(card, Decimal(3000), day(9), type=OperationType.INCOME, category="salary", subcategory="salary")

    # Act
    baseline = await get_baseline(db, NoRates(), user, OCTOBER)

    # Assert
    assert baseline is not None
    assert baseline.periods == [
        PeriodResponse(date_from=date(2026, 9, 1), date_to=date(2026, 9, 30)),
        PeriodResponse(date_from=date(2026, 8, 1), date_to=date(2026, 8, 31)),
        PeriodResponse(date_from=date(2026, 7, 1), date_to=date(2026, 7, 31)),
    ]
    assert baseline.expense.total == Decimal("330.00")
    # food was not there in July: that month counts as 0
    assert baseline.expense.categories == {"food": Decimal("300.00"), "transport": Decimal("30.00")}
    assert baseline.expense.subcategories == {
        ("food", "groceries"): Decimal("300.00"),
        ("transport", "taxi"): Decimal("30.00"),
    }
    assert (baseline.income.total, baseline.income.categories) == (Decimal("1000.00"), {"salary": Decimal("1000.00")})


async def test_average_is_rounded_to_cents(db, make_user, make_wallet, make_operation):
    user = make_user()
    card = make_wallet(user)
    make_operation(card, Decimal(1), day(6))
    make_operation(card, Decimal(100), day(9))

    # Act
    baseline = await get_baseline(db, NoRates(), user, OCTOBER)

    # Assert
    assert baseline is not None
    assert baseline.expense.categories == {"uncategorized": Decimal("33.33")}


@pytest.mark.parametrize(
    "first_operation",
    [datetime(2026, 7, 2, 12, tzinfo=UTC), datetime(2026, 9, 5, 12, tzinfo=UTC)],
    ids=["during_third_period", "during_first_period"],
)
async def test_no_baseline_without_three_periods_of_history(
    db, make_user, make_wallet, make_operation, first_operation
):
    # Before the first operation there is no data, not zero spending
    user = make_user()
    make_operation(make_wallet(user), Decimal(100), first_operation)

    # Act
    baseline = await get_baseline(db, NoRates(), user, OCTOBER)

    # Assert
    assert baseline is None


async def test_no_baseline_for_user_without_operations(db, make_user):
    # Act / Assert
    assert await get_baseline(db, NoRates(), make_user(), OCTOBER) is None


async def test_first_operation_at_start_of_third_period_is_enough(db, make_user, make_wallet, make_operation):
    user = make_user()
    # July 1, 00:30 in Kyiv
    make_operation(make_wallet(user), Decimal(30), datetime(2026, 6, 30, 21, 30, tzinfo=UTC))

    # Act
    baseline = await get_baseline(db, NoRates(), user, OCTOBER)

    # Assert
    assert baseline is not None
    assert baseline.expense.total == Decimal("10.00")


async def test_range_is_compared_with_ranges_of_same_length_before(db, make_user, make_wallet, make_operation):
    user = make_user()
    make_operation(make_wallet(user), Decimal(30), datetime(2026, 9, 1, 12, tzinfo=UTC))

    # Act
    baseline = await get_baseline(
        db, NoRates(), user, AnalyticsQuery(date_from=date(2026, 10, 21), date_to=date(2026, 10, 30))
    )

    # Assert
    assert baseline is not None
    assert baseline.periods == [
        PeriodResponse(date_from=date(2026, 10, 11), date_to=date(2026, 10, 20)),
        PeriodResponse(date_from=date(2026, 10, 1), date_to=date(2026, 10, 10)),
        PeriodResponse(date_from=date(2026, 9, 21), date_to=date(2026, 9, 30)),
    ]


async def test_wallet_filter_applies_to_history_and_sums(db, make_user, make_wallet, make_operation):
    user = make_user()
    card, cash = make_wallet(user, name="card"), make_wallet(user, name="cash")
    make_operation(card, Decimal(10), day(6))
    make_operation(card, Decimal(900), day(9))
    make_operation(cash, Decimal(30), day(9))

    # Act
    card_baseline = await get_baseline(db, NoRates(), user, OCTOBER.model_copy(update={"wallet_id": card.id}))
    cash_baseline = await get_baseline(db, NoRates(), user, OCTOBER.model_copy(update={"wallet_id": cash.id}))

    # Assert
    assert card_baseline is not None
    assert card_baseline.expense.total == Decimal("300.00")
    assert cash_baseline is None
