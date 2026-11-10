from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.core.config import settings
from app.core.exceptions import ExchangeServiceUnavailableError
from app.enums import CurrencyEnum, OperationType
from tests.conftest import TIMEZONE, assert_error

OCTOBER = {"period": "month", "date": "2026-10-15"}
DAY_IN_OCTOBER = datetime(2026, 10, 5, 12, tzinfo=UTC)
COMPARISON_KEYS = {"previous_amount", "change_percent"}


def summary(client, auth, user, **params):
    return client.get("/api/v1/analytics/summary", params=params, headers=auth(user))


def rates(uah: str, eur: str = "0.8") -> dict[CurrencyEnum, Decimal]:
    return {CurrencyEnum.USD: Decimal(1), CurrencyEnum.UAH: Decimal(uah), CurrencyEnum.EUR: Decimal(eur)}


def at(day: int, hour: int = 12) -> datetime:
    return datetime(2026, 10, day, hour, tzinfo=UTC)


class TestSummaryTotals:
    def test_empty_period_has_zeros(self, client, make_user, auth):
        user = make_user()

        # Act
        response = summary(client, auth, user, **OCTOBER)

        # Assert
        assert response.status_code == 200
        assert response.json() == {
            "period": {"date_from": "2026-10-01", "date_to": "2026-10-31"},
            "currency": "uah",
            "wallet_id": None,
            "income": "0.00",
            "expense": "0.00",
            "net": "0.00",
            "expense_by_category": [],
            "income_by_category": [],
            "previous": {
                "period": {"date_from": "2026-09-01", "date_to": "2026-09-30"},
                "income": "0.00",
                "expense": "0.00",
                "net": "0.00",
                "income_change_percent": None,
                "expense_change_percent": None,
            },
        }

    def test_totals_by_category_and_subcategory(self, client, make_user, make_wallet, make_operation, auth):
        user = make_user()
        card = make_wallet(user)
        make_operation(card, Decimal(200), at(3), category="food", subcategory="groceries")
        make_operation(card, Decimal(100), at(4), category="food", subcategory="groceries")
        make_operation(card, Decimal(100), at(5), category="food", subcategory="restaurants")
        make_operation(card, Decimal(100), at(6), category="transport", subcategory="taxi")
        make_operation(card, Decimal(1000), at(7), type=OperationType.INCOME, category="salary", subcategory="salary")

        # Act
        response = summary(client, auth, user, **OCTOBER)

        # Assert
        body = response.json()
        assert (body["income"], body["expense"], body["net"]) == ("1000.00", "500.00", "500.00")
        assert without_comparison(body["expense_by_category"]) == [
            {
                "category": "food",
                "amount": "400.00",
                "share": 80.0,
                "operations": 3,
                "subcategories": [
                    {"subcategory": "groceries", "amount": "300.00", "share": 60.0, "operations": 2},
                    {"subcategory": "restaurants", "amount": "100.00", "share": 20.0, "operations": 1},
                ],
            },
            {
                "category": "transport",
                "amount": "100.00",
                "share": 20.0,
                "operations": 1,
                "subcategories": [{"subcategory": "taxi", "amount": "100.00", "share": 20.0, "operations": 1}],
            },
        ]
        assert [category["category"] for category in body["income_by_category"]] == ["salary"]

    def test_operations_without_category_are_a_group(self, client, make_user, make_wallet, make_operation, auth):
        user = make_user()
        card = make_wallet(user)
        make_operation(card, Decimal(30), DAY_IN_OCTOBER)

        # Act
        response = summary(client, auth, user, **OCTOBER)

        # Assert
        assert without_comparison(response.json()["expense_by_category"]) == [
            {"category": "uncategorized", "amount": "30.00", "share": 100.0, "operations": 1, "subcategories": []}
        ]

    def test_only_own_income_and_expenses_of_period_count(self, client, make_user, make_wallet, make_operation, auth):
        user = make_user(login="owner")
        card = make_wallet(user)
        make_operation(card, Decimal(50), DAY_IN_OCTOBER)
        make_operation(card, Decimal(1000), DAY_IN_OCTOBER, type=OperationType.TRANSFER, category="transfer")
        make_operation(card, Decimal(70), datetime(2026, 9, 30, 12, tzinfo=UTC))
        make_operation(make_wallet(make_user(login="other")), Decimal(90), DAY_IN_OCTOBER)

        # Act
        response = summary(client, auth, user, **OCTOBER)

        # Assert
        assert response.json()["expense"] == "50.00"

    def test_day_is_taken_in_time_zone_of_user(self, client, make_user, make_wallet, make_operation, auth):
        user = make_user()
        card = make_wallet(user)
        # Kyiv is UTC+3 until October 25, then UTC+2
        make_operation(card, Decimal(10), datetime(2026, 9, 30, 21, 30, tzinfo=UTC))  # October 1, 00:30 in Kyiv
        make_operation(card, Decimal(20), datetime(2026, 10, 31, 22, 30, tzinfo=UTC))  # November 1, 00:30 in Kyiv

        # Act
        response = summary(client, auth, user, **OCTOBER)

        # Assert
        assert response.json()["expense"] == "10.00"

    def test_current_month_by_default(self, client, make_user, make_wallet, make_operation, auth):
        user = make_user()
        make_operation(make_wallet(user), Decimal(10), datetime.now(UTC))
        today = datetime.now(ZoneInfo(TIMEZONE)).date()

        # Act
        response = summary(client, auth, user)

        # Assert
        body = response.json()
        assert body["period"] == {"date_from": today.replace(day=1).isoformat(), "date_to": today.isoformat()}
        assert body["expense"] == "10.00"

    @pytest.mark.parametrize(
        "params, expected",
        [
            ({"period": "quarter", "date": "2026-05-20"}, ("2026-04-01", "2026-06-30")),
            ({"period": "year", "date": "2025-07-01"}, ("2025-01-01", "2025-12-31")),
            ({"date_from": "2026-09-10", "date_to": "2026-10-05"}, ("2026-09-10", "2026-10-05")),
        ],
        ids=["quarter", "year", "range"],
    )
    def test_period(self, client, make_user, auth, params, expected):
        user = make_user()

        # Act
        response = summary(client, auth, user, **params)

        # Assert
        assert response.status_code == 200
        assert response.json()["period"] == {"date_from": expected[0], "date_to": expected[1]}


class TestSummaryCurrency:
    def test_operations_are_converted_at_rate_of_their_day(
        self, client, make_user, make_wallet, make_operation, auth, daily_rates
    ):
        user = make_user()
        dollars = make_wallet(user, name="dollars", currency=CurrencyEnum.USD)
        make_operation(dollars, Decimal(10), at(5))
        make_operation(dollars, Decimal(10), at(6))
        make_operation(make_wallet(user), Decimal(5), at(6))
        daily_rates.by_day = {date(2026, 10, 5): rates(uah="40"), date(2026, 10, 6): rates(uah="41")}

        # Act
        response = summary(client, auth, user, **OCTOBER)

        # Assert
        assert response.json()["expense"] == "815.00"

    def test_report_in_requested_currency(self, client, make_user, make_wallet, make_operation, auth, daily_rates):
        user = make_user()
        make_operation(make_wallet(user), Decimal(400), DAY_IN_OCTOBER)

        # Act
        response = summary(client, auth, user, **OCTOBER, currency="usd")

        # Assert
        assert (response.json()["currency"], response.json()["expense"]) == ("usd", "10.00")

    def test_rate_between_two_currencies_other_than_dollar(
        self, client, make_user, make_wallet, make_operation, auth, daily_rates
    ):
        user = make_user()
        make_operation(make_wallet(user, name="euro", currency=CurrencyEnum.EUR), Decimal(10), DAY_IN_OCTOBER)

        # Act
        response = summary(client, auth, user, **OCTOBER)

        # Assert
        # 10 EUR = 10 / 0.8 USD = 12.5 USD = 12.5 * 40 UAH
        assert response.json()["expense"] == "500.00"

    def test_day_without_rates_uses_nearest_earlier_day(
        self, client, make_user, make_wallet, make_operation, auth, daily_rates
    ):
        user = make_user()
        dollars = make_wallet(user, name="dollars", currency=CurrencyEnum.USD)
        make_operation(dollars, Decimal(10), at(5))
        make_operation(dollars, Decimal(10), at(6))
        daily_rates.by_day = {date(2026, 10, 5): rates(uah="40")}
        daily_rates.unpublished = {date(2026, 10, 6)}

        # Act
        response = summary(client, auth, user, **OCTOBER)

        # Assert
        assert response.json()["expense"] == "800.00"

    def test_day_before_history_uses_first_published_day(
        self, client, make_user, make_wallet, make_operation, auth, daily_rates
    ):
        user = make_user()
        dollars = make_wallet(user, name="dollars", currency=CurrencyEnum.USD)
        make_operation(dollars, Decimal(10), datetime(2023, 6, 1, 12, tzinfo=UTC))
        daily_rates.by_day = {settings.exchange_api_history_start: rates(uah="38")}

        # Act
        response = summary(client, auth, user, date_from="2023-06-01", date_to="2023-06-30")

        # Assert
        assert response.json()["expense"] == "380.00"
        assert daily_rates.days == [settings.exchange_api_history_start]

    def test_rates_are_not_loaded_when_nothing_to_convert(
        self, client, make_user, make_wallet, make_operation, auth, daily_rates
    ):
        user = make_user()
        make_operation(make_wallet(user), Decimal(10), DAY_IN_OCTOBER)

        # Act
        response = summary(client, auth, user, **OCTOBER)

        # Assert
        assert response.status_code == 200
        assert daily_rates.days == []

    def test_exchange_service_unavailable(self, client, make_user, make_wallet, make_operation, auth, daily_rates):
        user = make_user()
        make_operation(make_wallet(user, name="dollars", currency=CurrencyEnum.USD), Decimal(10), DAY_IN_OCTOBER)
        daily_rates.error = ExchangeServiceUnavailableError()

        # Act
        response = summary(client, auth, user, **OCTOBER)

        # Assert
        assert_error(response, 503, "EXCHANGE_SERVICE_UNAVAILABLE")


class TestSummaryComparison:
    def test_previous_period_totals(self, client, make_user, make_wallet, make_operation, auth, daily_rates):
        user = make_user()
        card = make_wallet(user)
        dollars = make_wallet(user, name="dollars", currency=CurrencyEnum.USD)
        make_operation(card, Decimal(300), at(5))
        make_operation(card, Decimal(1000), at(6), type=OperationType.INCOME)
        make_operation(dollars, Decimal(5), datetime(2026, 9, 10, 12, tzinfo=UTC))
        make_operation(card, Decimal(1000), datetime(2026, 9, 11, 12, tzinfo=UTC), type=OperationType.INCOME)
        daily_rates.by_day = {date(2026, 9, 10): rates(uah="40")}

        # Act
        response = summary(client, auth, user, **OCTOBER)

        # Assert
        assert response.json()["previous"] == {
            "period": {"date_from": "2026-09-01", "date_to": "2026-09-30"},
            "income": "1000.00",
            "expense": "200.00",
            "net": "800.00",
            "income_change_percent": 0.0,
            "expense_change_percent": 50.0,
        }

    def test_range_is_compared_with_range_of_same_length_before(self, client, make_user, auth):
        user = make_user()

        # Act
        response = summary(client, auth, user, date_from="2026-10-21", date_to="2026-10-30")

        # Assert
        assert response.json()["previous"]["period"] == {"date_from": "2026-10-11", "date_to": "2026-10-20"}

    def test_category_change(self, client, make_user, make_wallet, make_operation, auth):
        user = make_user()
        card = make_wallet(user)
        make_operation(card, Decimal(300), at(5), category="food", subcategory="groceries")
        make_operation(
            card, Decimal(200), datetime(2026, 9, 5, 12, tzinfo=UTC), category="food", subcategory="groceries"
        )

        # Act
        response = summary(client, auth, user, **OCTOBER)

        # Assert
        food = response.json()["expense_by_category"][0]
        assert (food["previous_amount"], food["change_percent"]) == ("200.00", 50.0)
        groceries = food["subcategories"][0]
        assert (groceries["previous_amount"], groceries["change_percent"]) == ("200.00", 50.0)

    def test_category_only_in_previous_period_is_shown(self, client, make_user, make_wallet, make_operation, auth):
        user = make_user()
        card = make_wallet(user)
        make_operation(card, Decimal(50), at(5), category="food", subcategory="groceries")
        make_operation(
            card, Decimal(100), datetime(2026, 9, 5, 12, tzinfo=UTC), category="transport", subcategory="taxi"
        )

        # Act
        response = summary(client, auth, user, **OCTOBER)

        # Assert
        transport = response.json()["expense_by_category"][1]
        assert transport == {
            "category": "transport",
            "amount": "0.00",
            "share": 0.0,
            "operations": 0,
            "previous_amount": "100.00",
            "change_percent": -100.0,
            "subcategories": [
                {
                    "subcategory": "taxi",
                    "amount": "0.00",
                    "share": 0.0,
                    "operations": 0,
                    "previous_amount": "100.00",
                    "change_percent": -100.0,
                }
            ],
        }

    def test_change_is_null_when_previous_period_had_nothing(
        self, client, make_user, make_wallet, make_operation, auth
    ):
        user = make_user()
        make_operation(make_wallet(user), Decimal(50), at(5), category="food", subcategory="groceries")

        # Act
        response = summary(client, auth, user, **OCTOBER)

        # Assert
        body = response.json()
        assert body["previous"]["expense_change_percent"] is None
        food = body["expense_by_category"][0]
        assert (food["previous_amount"], food["change_percent"]) == ("0.00", None)


class TestSummaryQuery:
    def test_wallet_filter(self, client, make_user, make_wallet, make_operation, auth):
        user = make_user()
        card = make_wallet(user, name="card")
        make_operation(card, Decimal(10), DAY_IN_OCTOBER)
        make_operation(make_wallet(user, name="cash"), Decimal(20), DAY_IN_OCTOBER)

        # Act
        response = summary(client, auth, user, **OCTOBER, wallet_id=card.id)

        # Assert
        assert (response.json()["wallet_id"], response.json()["expense"]) == (card.id, "10.00")

    def test_wallet_of_another_user(self, client, make_user, make_wallet, auth):
        user = make_user(login="owner")
        foreign = make_wallet(make_user(login="other"))

        # Act
        response = summary(client, auth, user, **OCTOBER, wallet_id=foreign.id)

        # Assert
        assert_error(response, 404, "WALLET_NOT_FOUND")

    @pytest.mark.parametrize(
        "params",
        [
            {"period": "month", "date_from": "2026-10-01", "date_to": "2026-10-31"},
            {"date": "2026-10-15"},
            {"date_from": "2026-10-01"},
            {"date_from": "2026-10-31", "date_to": "2026-10-01"},
            {"date_from": "2010-01-01", "date_to": "2026-10-01"},
            {"period": "week"},
            {"period": "month", "currency": "btc"},
        ],
        ids=[
            "period_and_range",
            "date_without_period",
            "range_without_end",
            "range_ends_before_start",
            "range_longer_than_limit",
            "unknown_period",
            "unknown_currency",
        ],
    )
    def test_invalid_query(self, client, make_user, auth, params):
        user = make_user()

        # Act
        response = summary(client, auth, user, **params)

        # Assert
        assert_error(response, 422, "VALIDATION_ERROR")

    def test_period_in_future(self, client, make_user, auth):
        user = make_user()
        tomorrow = datetime.now(ZoneInfo(TIMEZONE)).date() + timedelta(days=1)

        # Act
        response = summary(client, auth, user, period="month", date=tomorrow.isoformat())

        # Assert
        assert_error(response, 422, "INVALID_PERIOD")

    def test_summary_requires_login(self, client):
        # Act
        response = client.get("/api/v1/analytics/summary")

        # Assert
        assert_error(response, 401, "NOT_AUTHENTICATED")


def timeline(client, auth, user, **params):
    return client.get("/api/v1/analytics/timeline", params=params, headers=auth(user))


def point(day: str, income: str = "0.00", expense: str = "0.00", net: str = "0.00") -> dict[str, str]:
    return {"date": day, "income": income, "expense": expense, "net": net}


class TestTimeline:
    def test_points_by_day_include_days_without_operations(self, client, make_user, make_wallet, make_operation, auth):
        user = make_user()
        card = make_wallet(user)
        make_operation(card, Decimal(50), at(2))
        make_operation(card, Decimal(100), at(3), type=OperationType.INCOME)

        # Act
        response = timeline(client, auth, user, date_from="2026-10-01", date_to="2026-10-03")

        # Assert
        assert response.status_code == 200
        assert response.json() == {
            "period": {"date_from": "2026-10-01", "date_to": "2026-10-03"},
            "currency": "uah",
            "wallet_id": None,
            "group_by": "day",
            "points": [
                point("2026-10-01"),
                point("2026-10-02", expense="50.00", net="-50.00"),
                point("2026-10-03", income="100.00", net="100.00"),
            ],
        }

    def test_operation_is_on_its_day_in_time_zone_of_user(self, client, make_user, make_wallet, make_operation, auth):
        user = make_user()
        # October 2, 00:30 in Kyiv
        make_operation(make_wallet(user), Decimal(10), datetime(2026, 10, 1, 21, 30, tzinfo=UTC))

        # Act
        response = timeline(client, auth, user, date_from="2026-10-01", date_to="2026-10-02")

        # Assert
        assert response.json()["points"] == [point("2026-10-01"), point("2026-10-02", expense="10.00", net="-10.00")]

    @pytest.mark.parametrize(
        "params, group_by, points",
        [
            (OCTOBER, "day", 31),
            ({"period": "quarter", "date": "2026-05-20"}, "month", 3),
            ({"period": "year", "date": "2025-07-01"}, "month", 12),
        ],
        ids=["month_by_day", "quarter_by_month", "year_by_month"],
    )
    def test_step_follows_period(self, client, make_user, auth, params, group_by, points):
        user = make_user()

        # Act
        response = timeline(client, auth, user, **params)

        # Assert
        body = response.json()
        assert (body["group_by"], len(body["points"])) == (group_by, points)

    def test_weeks_start_on_monday(self, client, make_user, make_wallet, make_operation, auth):
        user = make_user()
        make_operation(make_wallet(user), Decimal(10), datetime(2026, 11, 3, 12, tzinfo=UTC))

        # Act
        response = timeline(client, auth, user, date_from="2026-10-28", date_to="2026-11-10", group_by="week")

        # Assert
        assert response.json()["points"] == [
            point("2026-10-28"),
            point("2026-11-02", expense="10.00", net="-10.00"),
            point("2026-11-09"),
        ]

    def test_months_without_operations_are_zero(self, client, make_user, make_wallet, make_operation, auth):
        user = make_user()
        card = make_wallet(user)
        make_operation(card, Decimal(10), datetime(2025, 1, 15, 12, tzinfo=UTC))
        make_operation(card, Decimal(20), datetime(2025, 3, 15, 12, tzinfo=UTC))

        # Act
        response = timeline(client, auth, user, date_from="2025-01-01", date_to="2025-03-31", group_by="month")

        # Assert
        assert response.json()["points"] == [
            point("2025-01-01", expense="10.00", net="-10.00"),
            point("2025-02-01"),
            point("2025-03-01", expense="20.00", net="-20.00"),
        ]

    def test_converted_without_transfers(self, client, make_user, make_wallet, make_operation, auth, daily_rates):
        user = make_user()
        dollars = make_wallet(user, name="dollars", currency=CurrencyEnum.USD)
        make_operation(dollars, Decimal(10), at(5))
        make_operation(dollars, Decimal(99), at(5), type=OperationType.TRANSFER, category="transfer")

        # Act
        response = timeline(client, auth, user, date_from="2026-10-05", date_to="2026-10-05")

        # Assert
        assert response.json()["points"] == [point("2026-10-05", expense="400.00", net="-400.00")]

    def test_wallet_of_another_user(self, client, make_user, make_wallet, auth):
        user = make_user(login="owner")
        foreign = make_wallet(make_user(login="other"))

        # Act
        response = timeline(client, auth, user, **OCTOBER, wallet_id=foreign.id)

        # Assert
        assert_error(response, 404, "WALLET_NOT_FOUND")

    @pytest.mark.parametrize(
        "params",
        [OCTOBER | {"group_by": "hour"}, {"date": "2026-10-15"}],
        ids=["unknown_step", "date_without_period"],
    )
    def test_invalid_query(self, client, make_user, auth, params):
        user = make_user()

        # Act
        response = timeline(client, auth, user, **params)

        # Assert
        assert_error(response, 422, "VALIDATION_ERROR")


def without_comparison(items: list[dict]) -> list[dict]:
    # The comparison with the previous period has its own tests
    result = []
    for item in items:
        item = {key: value for key, value in item.items() if key not in COMPARISON_KEYS}
        if "subcategories" in item:
            item["subcategories"] = without_comparison(item["subcategories"])
        result.append(item)
    return result
