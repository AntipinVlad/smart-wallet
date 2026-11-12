"""Facts the model explains: every number in them is computed here, the model only puts them into words."""

from decimal import Decimal
from enum import StrEnum, auto
from typing import TypedDict

from pydantic import BaseModel

from app.core.config import settings
from app.enums import CurrencyEnum
from app.schemas import CategoryTotal, PeriodResponse, SubcategoryTotal, SummaryResponse
from app.services.analytics import Averages, Baseline, change_percent

# Fewer lines mean fewer tokens and a model that does not drown small categories in noise
MAX_EXPENSE_CATEGORIES = 8
MAX_INCOME_CATEGORIES = 5
MAX_SUBCATEGORIES = 3
MAX_LARGEST_CHANGES = 3


class ChangeStatus(StrEnum):
    """Spelled out, so the model never reads a missing percent as "no change"."""

    NEW = auto()
    GONE = auto()
    UP = auto()
    DOWN = auto()
    SAME = auto()


class ChangeFields(TypedDict):
    amount: Decimal
    previous_amount: Decimal
    difference: Decimal
    change_percent: float | None
    status: ChangeStatus


class BaselineFact(BaseModel):
    """This period against the average of the periods before it."""

    amount: Decimal
    difference: Decimal
    change_percent: float | None
    status: ChangeStatus


class TotalFact(BaseModel):
    amount: Decimal
    previous_amount: Decimal
    difference: Decimal
    change_percent: float | None
    status: ChangeStatus
    baseline: BaselineFact | None = None


class SubcategoryFact(TotalFact):
    subcategory: str
    share: float
    alert: bool


class CategoryFact(TotalFact):
    category: str
    share: float
    operations: int
    alert: bool
    subcategories: list[SubcategoryFact]


class TipFacts(BaseModel):
    period: PeriodResponse
    previous_period: PeriodResponse
    baseline_periods: list[PeriodResponse]
    currency: CurrencyEnum
    income: TotalFact
    expense: TotalFact
    net: Decimal
    previous_net: Decimal
    expense_categories: list[CategoryFact]
    income_categories: list[CategoryFact]
    expense_grew_most: list[str]
    expense_dropped_most: list[str]
    alerts: list[str]
    net_below_zero: bool


def build_facts(summary: SummaryResponse, baseline: Baseline | None = None) -> TipFacts:
    previous = summary.previous
    expense_averages = None if baseline is None else baseline.expense
    income_averages = None if baseline is None else baseline.income
    expense = [_category(item, expense_averages, alerts=True) for item in summary.expense_by_category]
    grew = sorted((c for c in expense if c.difference > 0), key=lambda c: -c.difference)[:MAX_LARGEST_CHANGES]
    dropped = sorted((c for c in expense if c.difference < 0), key=lambda c: c.difference)[:MAX_LARGEST_CHANGES]

    shown = expense[:MAX_EXPENSE_CATEGORIES]
    # A category that is gone has 0 and sorts last, yet it is often the most telling change
    shown += [c for c in grew + dropped if c not in shown]
    return TipFacts(
        period=summary.period,
        previous_period=previous.period,
        baseline_periods=[] if baseline is None else baseline.periods,
        currency=summary.currency,
        income=_total(summary.income, previous.income, previous.income_change_percent, income_averages),
        expense=_total(summary.expense, previous.expense, previous.expense_change_percent, expense_averages),
        net=summary.net,
        previous_net=previous.net,
        expense_categories=shown,
        income_categories=[
            _category(item, income_averages, alerts=False)
            for item in summary.income_by_category[:MAX_INCOME_CATEGORIES]
        ],
        expense_grew_most=[c.category for c in grew],
        expense_dropped_most=[c.category for c in dropped],
        # One warning per category: restaurants that doubled inside food are a warning about food
        alerts=[c.category for c in shown if c.alert or any(sub.alert for sub in c.subcategories)],
        net_below_zero=summary.net < 0,
    )


def _total(
    amount: Decimal, previous_amount: Decimal, change_percent: float | None, averages: Averages | None
) -> TotalFact:
    return TotalFact(
        baseline=None if averages is None else _baseline(amount, averages.total),
        **_change(amount, previous_amount, change_percent),
    )


def _category(item: CategoryTotal, averages: Averages | None, alerts: bool) -> CategoryFact:
    baseline = None if averages is None else _baseline(item.amount, averages.categories.get(item.category))
    subcategories = [_subcategory(item.category, sub, averages, alerts) for sub in item.subcategories]
    # An alert inside a category is often the point of the card, even for its fourth subcategory
    shown = subcategories[:MAX_SUBCATEGORIES] + [sub for sub in subcategories[MAX_SUBCATEGORIES:] if sub.alert]
    return CategoryFact(
        category=item.category,
        share=item.share,
        operations=item.operations,
        alert=alerts and _needs_alert(item.amount, item.share, item.previous_amount, item.change_percent, baseline),
        subcategories=shown,
        baseline=baseline,
        **_change(item.amount, item.previous_amount, item.change_percent),
    )


def _subcategory(category: str, item: SubcategoryTotal, averages: Averages | None, alerts: bool) -> SubcategoryFact:
    average = None if averages is None else averages.subcategories.get((category, item.subcategory))
    baseline = None if averages is None else _baseline(item.amount, average)
    return SubcategoryFact(
        subcategory=item.subcategory,
        share=item.share,
        alert=alerts and _needs_alert(item.amount, item.share, item.previous_amount, item.change_percent, baseline),
        baseline=baseline,
        **_change(item.amount, item.previous_amount, item.change_percent),
    )


def _needs_alert(
    amount: Decimal,
    share: float,
    previous_amount: Decimal,
    previous_change: float | None,
    baseline: BaselineFact | None,
) -> bool:
    """The code, not the model, decides what deserves a warning: the model was not consistent about it."""
    if share < settings.tips_alert_min_share_percent:
        return False
    # The average of several periods, when there is one: a spike against one cheap month is no alarm
    before, change = (
        (previous_amount, previous_change) if baseline is None else (baseline.amount, baseline.change_percent)
    )
    if not before:
        return bool(amount)
    return change is not None and change >= settings.tips_alert_min_growth_percent


def _baseline(amount: Decimal, average: Decimal | None) -> BaselineFact:
    # Not in any period before: 0 on average, so new against the baseline too
    average = Decimal("0.00") if average is None else average
    return BaselineFact(
        amount=average,
        difference=amount - average,
        change_percent=change_percent(amount, average),
        status=_status(amount, average),
    )


def _change(amount: Decimal, previous_amount: Decimal, change_percent: float | None) -> ChangeFields:
    return {
        "amount": amount,
        "previous_amount": previous_amount,
        "difference": amount - previous_amount,
        # The percent of the summary, not a new one: both answers show the same number
        "change_percent": change_percent,
        "status": _status(amount, previous_amount),
    }


def _status(amount: Decimal, previous_amount: Decimal) -> ChangeStatus:
    # Nothing in both periods happens to totals, not to categories: a summary lists only categories with operations
    if amount == previous_amount:
        return ChangeStatus.SAME
    if not previous_amount:
        return ChangeStatus.NEW
    if not amount:
        return ChangeStatus.GONE
    return ChangeStatus.UP if amount > previous_amount else ChangeStatus.DOWN
