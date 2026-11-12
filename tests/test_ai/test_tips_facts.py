from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

import pytest

from app.ai.tips.facts import ChangeStatus, build_facts
from app.enums import CurrencyEnum
from app.schemas import CategoryTotal, PeriodResponse, PreviousPeriod, SubcategoryTotal, SummaryResponse
from app.services.analytics import Averages, Baseline

BASELINE_PERIODS = [
    PeriodResponse(date_from=date(2026, month, 1), date_to=date(2026, month, last))
    for month, last in ((9, 30), (8, 31), (7, 31))
]


def change(amount: Decimal, previous: Decimal) -> float | None:
    return float(round((amount - previous) / previous * 100, 1)) if previous else None


@dataclass
class Item:
    name: str
    amount: str
    previous: str = "0.00"
    subcategories: list["Item"] = field(default_factory=list)
    # Average of the baseline periods; None when the category was not there
    average: str | None = None

    def fields(self, total: Decimal) -> dict:
        amount, previous = Decimal(self.amount), Decimal(self.previous)
        return {
            "amount": amount,
            "share": float(round(amount / total * 100, 1)) if total else 0.0,
            "operations": 1,
            "previous_amount": previous,
            "change_percent": change(amount, previous),
        }


@dataclass
class SummaryBuilder:
    """Totals follow from the categories, as in a real summary."""

    expense: list[Item] = field(default_factory=list)
    income: list[Item] = field(default_factory=list)

    def build(self) -> SummaryResponse:
        expense, income = self._categories(self.expense), self._categories(self.income)
        expense_total, income_total = _sum(self.expense, "amount"), _sum(self.income, "amount")
        previous_expense, previous_income = _sum(self.expense, "previous"), _sum(self.income, "previous")
        return SummaryResponse(
            period=PeriodResponse(date_from=date(2026, 10, 1), date_to=date(2026, 10, 31)),
            currency=CurrencyEnum.UAH,
            wallet_id=None,
            income=income_total,
            expense=expense_total,
            net=income_total - expense_total,
            expense_by_category=expense,
            income_by_category=income,
            previous=PreviousPeriod(
                period=PeriodResponse(date_from=date(2026, 9, 1), date_to=date(2026, 9, 30)),
                income=previous_income,
                expense=previous_expense,
                net=previous_income - previous_expense,
                income_change_percent=change(income_total, previous_income),
                expense_change_percent=change(expense_total, previous_expense),
            ),
        )

    def baseline(self) -> Baseline:
        return Baseline(periods=BASELINE_PERIODS, income=_averages(self.income), expense=_averages(self.expense))

    @staticmethod
    def _categories(items: list[Item]) -> list[CategoryTotal]:
        total = _sum(items, "amount")
        categories = [
            CategoryTotal(
                category=item.name,
                subcategories=[
                    SubcategoryTotal(subcategory=sub.name, **sub.fields(total)) for sub in item.subcategories
                ],
                **item.fields(total),
            )
            for item in items
        ]
        return sorted(categories, key=lambda c: (-c.amount, -c.previous_amount, c.category))


def _averages(items: list[Item]) -> Averages:
    categories = {item.name: Decimal(item.average) for item in items if item.average is not None}
    subcategories = {
        (item.name, sub.name): Decimal(sub.average) for item in items for sub in item.subcategories if sub.average
    }
    return Averages(total=sum(categories.values(), Decimal("0.00")), categories=categories, subcategories=subcategories)


def _sum(items: list[Item], name: str) -> Decimal:
    return sum((Decimal(getattr(item, name)) for item in items), Decimal("0.00"))


class TestTotals:
    def test_totals_have_difference_and_status(self):
        summary = SummaryBuilder(
            expense=[Item("food", "6400.00", previous="3000.00")], income=[Item("salary", "1000.00")]
        ).build()

        # Act
        facts = build_facts(summary)

        # Assert
        assert facts.expense.model_dump() == {
            "amount": Decimal("6400.00"),
            "previous_amount": Decimal("3000.00"),
            "difference": Decimal("3400.00"),
            "change_percent": 113.3,
            "status": ChangeStatus.UP,
            "baseline": None,
        }
        assert (facts.income.status, facts.income.change_percent) == (ChangeStatus.NEW, None)
        assert (facts.net, facts.previous_net) == (Decimal("-5400.00"), Decimal("-3000.00"))

    def test_total_without_anything_in_both_periods_is_same_not_new(self):
        summary = SummaryBuilder(expense=[Item("food", "150.00")]).build()

        # Act
        facts = build_facts(summary)

        # Assert
        assert (facts.income.amount, facts.income.status) == (Decimal("0.00"), ChangeStatus.SAME)

    @pytest.mark.parametrize(
        "income, net_below_zero",
        [("7000.00", False), ("6400.00", False), ("100.00", True)],
        ids=["above", "zero", "below"],
    )
    def test_net_below_zero(self, income, net_below_zero):
        summary = SummaryBuilder(expense=[Item("food", "6400.00")], income=[Item("salary", income)]).build()

        # Act
        facts = build_facts(summary)

        # Assert
        assert facts.net_below_zero is net_below_zero

    def test_period_and_currency(self):
        summary = SummaryBuilder().build()

        # Act
        facts = build_facts(summary)

        # Assert
        assert (facts.period, facts.previous_period, facts.currency) == (
            summary.period,
            summary.previous.period,
            CurrencyEnum.UAH,
        )


class TestCategories:
    @pytest.mark.parametrize(
        "amount, previous, status",
        [
            ("100.00", "0.00", ChangeStatus.NEW),
            ("0.00", "100.00", ChangeStatus.GONE),
            ("150.00", "100.00", ChangeStatus.UP),
            ("50.00", "100.00", ChangeStatus.DOWN),
            ("100.00", "100.00", ChangeStatus.SAME),
        ],
        ids=["new", "gone", "up", "down", "same"],
    )
    def test_status_of_change(self, amount, previous, status):
        summary = SummaryBuilder(expense=[Item("food", amount, previous=previous)]).build()

        # Act
        facts = build_facts(summary)

        # Assert
        assert facts.expense_categories[0].status == status

    def test_category_and_subcategory_have_difference(self):
        summary = SummaryBuilder(
            expense=[
                Item(
                    "food",
                    "1900.00",
                    previous="3000.00",
                    subcategories=[
                        Item("restaurants", "1900.00", previous="800.00"),
                        Item("groceries", "0.00", "2200.00"),
                    ],
                )
            ]
        ).build()

        # Act
        food = build_facts(summary).expense_categories[0]

        # Assert
        assert (food.category, food.difference, food.change_percent, food.share) == (
            "food",
            Decimal("-1100.00"),
            -36.7,
            100.0,
        )
        assert [(sub.subcategory, sub.difference, sub.status) for sub in food.subcategories] == [
            ("restaurants", Decimal("1100.00"), ChangeStatus.UP),
            ("groceries", Decimal("-2200.00"), ChangeStatus.GONE),
        ]

    def test_only_largest_categories_and_subcategories(self):
        summary = SummaryBuilder(
            expense=[
                Item(f"expense{index}", f"{100 - index}.00", subcategories=[Item(f"sub{n}", "1.00") for n in range(5)])
                for index in range(10)
            ],
            income=[Item(f"income{index}", f"{100 - index}.00") for index in range(7)],
        ).build()

        # Act
        facts = build_facts(summary)

        # Assert
        assert [c.category for c in facts.expense_categories] == [f"expense{index}" for index in range(8)]
        assert [c.category for c in facts.income_categories] == [f"income{index}" for index in range(5)]
        assert all(len(c.subcategories) == 3 for c in facts.expense_categories)

    def test_category_that_dropped_most_is_kept_beyond_limit(self):
        expense = [Item(f"expense{index}", f"{100 - index}.00") for index in range(8)]
        summary = SummaryBuilder(expense=[*expense, Item("taxi", "0.00", previous="5000.00")]).build()

        # Act
        facts = build_facts(summary)

        # Assert
        assert facts.expense_categories[-1].category == "taxi"


class TestAlerts:
    """An expense category needs a warning when it is new or grew by 20% and holds at least 5% of expenses."""

    @pytest.mark.parametrize(
        "amount, previous, alert",
        [
            ("125.00", "100.00", True),
            ("119.00", "100.00", False),
            ("50.00", "0.00", True),
            ("80.00", "100.00", False),
            ("0.00", "100.00", False),
        ],
        ids=["grew_20_percent", "grew_less", "new", "dropped", "gone"],
    )
    def test_alert_by_growth(self, amount, previous, alert):
        summary = SummaryBuilder(
            expense=[Item("food", amount, previous=previous), Item("housing", "500.00", previous="500.00")]
        ).build()

        # Act
        food = next(c for c in build_facts(summary).expense_categories if c.category == "food")

        # Assert
        assert food.alert is alert

    @pytest.mark.parametrize(
        "amount, alert", [("50.00", True), ("49.00", False)], ids=["share_5_percent", "share_below_5_percent"]
    )
    def test_small_category_is_no_alert(self, amount, alert):
        # health grows by 150% either way; only its share of expenses decides
        rest = f"{1000 - int(Decimal(amount))}.00"
        summary = SummaryBuilder(
            expense=[Item("health", amount, previous="18.00"), Item("housing", rest, previous=rest)]
        ).build()

        # Act
        health = build_facts(summary).expense_categories[1]

        # Assert
        assert (health.category, health.alert) == ("health", alert)

    def test_income_categories_are_no_alert(self):
        summary = SummaryBuilder(income=[Item("salary", "65000.00", previous="100.00")]).build()

        # Act
        facts = build_facts(summary)

        # Assert
        assert facts.income_categories[0].alert is False

    def test_alerts_list_names_alert_categories(self):
        summary = SummaryBuilder(
            expense=[
                Item("travel", "42000.00"),
                Item("housing", "18000.00", previous="18000.00"),
                Item("food", "15500.00", previous="12000.00"),
            ]
        ).build()

        # Act
        facts = build_facts(summary)

        # Assert
        assert facts.alerts == ["travel", "food"]


class TestLargestChanges:
    def test_expense_categories_that_grew_and_dropped_most(self):
        summary = SummaryBuilder(
            expense=[
                Item("food", "1000.00", previous="100.00"),
                Item("housing", "500.00", previous="400.00"),
                Item("transport", "300.00", previous="0.00"),
                Item("health", "10.00", previous="5.00"),
                Item("shopping", "50.00", previous="900.00"),
                Item("travel", "0.00", previous="200.00"),
                Item("gifts", "20.00", previous="20.00"),
            ]
        ).build()

        # Act
        facts = build_facts(summary)

        # Assert
        assert facts.expense_grew_most == ["food", "transport", "housing"]
        assert facts.expense_dropped_most == ["shopping", "travel"]


class TestBaseline:
    def test_facts_compare_with_average_of_baseline(self):
        builder = SummaryBuilder(
            expense=[
                Item(
                    "food",
                    "1000.00",
                    previous="500.00",
                    average="800.00",
                    subcategories=[Item("restaurants", "600.00", average="400.00")],
                ),
                Item("transport", "300.00", average=None),
            ]
        )

        # Act
        facts = build_facts(builder.build(), builder.baseline())

        # Assert
        assert facts.baseline_periods == BASELINE_PERIODS
        food, transport = facts.expense_categories
        assert food.baseline is not None and food.baseline.model_dump() == {
            "amount": Decimal("800.00"),
            "difference": Decimal("200.00"),
            "change_percent": 25.0,
            "status": ChangeStatus.UP,
        }
        assert food.subcategories[0].baseline is not None
        assert food.subcategories[0].baseline.difference == Decimal("200.00")
        # Not in any baseline period: new against the average too
        assert transport.baseline is not None
        assert (transport.baseline.amount, transport.baseline.status) == (Decimal("0.00"), ChangeStatus.NEW)
        assert facts.expense.baseline is not None
        assert (facts.expense.baseline.amount, facts.expense.baseline.change_percent) == (Decimal("800.00"), 62.5)

    def test_without_baseline(self):
        summary = SummaryBuilder(expense=[Item("food", "1000.00", previous="500.00")]).build()

        # Act
        facts = build_facts(summary, None)

        # Assert
        assert (facts.baseline_periods, facts.expense.baseline, facts.expense_categories[0].baseline) == (
            [],
            None,
            None,
        )

    @pytest.mark.parametrize(
        "previous, average, alert",
        [("500.00", "950.00", False), ("1000.00", "700.00", True), ("100.00", "0.00", True)],
        ids=["spike_against_cheap_month_only", "grew_against_average", "new_against_average"],
    )
    def test_alert_follows_average_not_previous_period(self, previous, average, alert):
        builder = SummaryBuilder(
            expense=[
                Item("food", "1000.00", previous=previous, average=average),
                Item("housing", "1000.00", previous="1000.00", average="1000.00"),
            ]
        )

        # Act
        facts = build_facts(builder.build(), builder.baseline())

        # Assert
        food = next(c for c in facts.expense_categories if c.category == "food")
        assert food.alert is alert


class TestSubcategoryAlerts:
    def food(self, restaurants: str = "800.00") -> Item:
        # food +5% in all, restaurants doubled and groceries dropped inside it
        groceries = f"{1050 - int(Decimal(restaurants))}.00"
        return Item(
            "food",
            "1050.00",
            previous="1000.00",
            subcategories=[Item("restaurants", restaurants, previous="400.00"), Item("groceries", groceries, "600.00")],
        )

    def test_subcategory_that_grew_raises_alert_of_its_category(self):
        summary = SummaryBuilder(expense=[self.food()]).build()

        # Act
        facts = build_facts(summary)

        # Assert
        food = facts.expense_categories[0]
        assert food.alert is False
        assert [(sub.subcategory, sub.alert) for sub in food.subcategories] == [
            ("restaurants", True),
            ("groceries", False),
        ]
        assert facts.alerts == ["food"]

    def test_small_subcategory_is_no_alert(self):
        # 49 of 1050 is 4.7% of expenses
        summary = SummaryBuilder(
            expense=[
                Item(
                    "food",
                    "1050.00",
                    previous="1000.00",
                    subcategories=[Item("delivery", "49.00", previous="10.00"), Item("groceries", "1001.00", "990.00")],
                )
            ]
        ).build()

        # Act
        facts = build_facts(summary)

        # Assert
        assert facts.alerts == []

    def test_subcategory_with_alert_is_kept_beyond_limit(self):
        subcategories = [Item(f"sub{index}", "200.00", previous="200.00") for index in range(4)]
        summary = SummaryBuilder(
            expense=[
                Item(
                    "food",
                    "900.00",
                    previous="850.00",
                    subcategories=[*subcategories, Item("delivery", "100.00", previous="50.00")],
                )
            ]
        ).build()

        # Act
        food = build_facts(summary).expense_categories[0]

        # Assert
        assert [sub.subcategory for sub in food.subcategories] == ["sub0", "sub1", "sub2", "delivery"]
