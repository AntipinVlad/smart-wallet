import bisect
import logging
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import NamedTuple, TypedDict
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import ExchangeRateNotFoundError, WalletNotFoundError
from app.enums import CurrencyEnum, OperationType, PeriodPreset
from app.models import User
from app.repository import analytics as analytics_repository
from app.repository import wallets as wallet_repository
from app.repository.analytics import OperationFilter, OperationGroup
from app.schemas import (
    AnalyticsQuery,
    CategoryTotal,
    PeriodResponse,
    PreviousPeriod,
    SubcategoryTotal,
    SummaryResponse,
    TimelinePoint,
    TimelineQuery,
    TimelineResponse,
)
from app.services.exchange_rates import DailyRateLoader
from app.services.periods import Period, bucket_starts, default_step, local_day, preset_period, range_period

logger = logging.getLogger(__name__)

CENT = Decimal("0.01")
UNCATEGORIZED = "uncategorized"


@dataclass(frozen=True)
class Sum:
    amount: Decimal = Decimal("0.00")
    operations: int = 0

    def __add__(self, other: "Sum") -> "Sum":
        return Sum(self.amount + other.amount, self.operations + other.operations)

    def rounded(self) -> "Sum":
        return Sum(self.amount.quantize(CENT), self.operations)


class GroupSum(NamedTuple):
    """Operations of one local day, type and category, in the currency of the report."""

    day: date
    type: OperationType
    category: str
    subcategory: str | None
    sum: Sum


# Category, then subcategory: None for operations without one
Sums = dict[str, dict[str | None, Sum]]


class TypeSummary(NamedTuple):
    """Income or expenses of the period and of the previous one."""

    total: Decimal
    previous_total: Decimal
    categories: list[CategoryTotal]


class ComparedFields(TypedDict):
    amount: Decimal
    share: float
    operations: int
    previous_amount: Decimal
    change_percent: float | None


async def get_summary(
    db: AsyncSession, rate_loader: DailyRateLoader, current_user: User, query: AnalyticsQuery
) -> SummaryResponse:
    await _check_wallet(db, current_user, query.wallet_id)
    period = _period(current_user, query)
    previous = period.previous()
    groups = await _group_sums(db, rate_loader, current_user, query, period)
    previous_groups = await _group_sums(db, rate_loader, current_user, query, previous)

    income = _by_category(groups, previous_groups, OperationType.INCOME)
    expense = _by_category(groups, previous_groups, OperationType.EXPENSE)
    logger.info("Summary of user %s for %s to %s", current_user.id, period.date_from, period.date_to)
    return SummaryResponse(
        period=_period_response(period),
        currency=query.currency,
        wallet_id=query.wallet_id,
        income=income.total,
        expense=expense.total,
        net=income.total - expense.total,
        expense_by_category=expense.categories,
        income_by_category=income.categories,
        previous=PreviousPeriod(
            period=_period_response(previous),
            income=income.previous_total,
            expense=expense.previous_total,
            net=income.previous_total - expense.previous_total,
            income_change_percent=_change(income.total, income.previous_total),
            expense_change_percent=_change(expense.total, expense.previous_total),
        ),
    )


async def get_timeline(
    db: AsyncSession, rate_loader: DailyRateLoader, current_user: User, query: TimelineQuery
) -> TimelineResponse:
    await _check_wallet(db, current_user, query.wallet_id)
    period = _period(current_user, query)
    step = query.group_by or default_step(period)
    starts = bucket_starts(period, step)
    groups = await _group_sums(db, rate_loader, current_user, query, period)

    income = [Decimal(0)] * len(starts)
    expense = [Decimal(0)] * len(starts)
    for group in groups:
        # The last step that starts on or before the day
        index = bisect.bisect_right(starts, group.day) - 1
        totals = income if group.type == OperationType.INCOME else expense
        totals[index] += group.sum.amount

    logger.info("Timeline of user %s for %s to %s by %s", current_user.id, period.date_from, period.date_to, step)
    return TimelineResponse(
        period=_period_response(period),
        currency=query.currency,
        wallet_id=query.wallet_id,
        group_by=step,
        points=[_point(*values) for values in zip(starts, income, expense, strict=True)],
    )


async def _check_wallet(db: AsyncSession, current_user: User, wallet_id: int | None) -> None:
    if wallet_id is not None and not await wallet_repository.get_wallet_by_id(db, current_user.id, wallet_id):
        raise WalletNotFoundError(wallet_id)


def _period(current_user: User, query: AnalyticsQuery) -> Period:
    today = local_day(datetime.now(UTC), current_user.timezone)
    if query.date_from is not None and query.date_to is not None:
        return range_period(query.date_from, query.date_to, today)
    return preset_period(query.period or PeriodPreset.MONTH, query.date or today, today)


async def _group_sums(
    db: AsyncSession, rate_loader: DailyRateLoader, current_user: User, query: AnalyticsQuery, period: Period
) -> list[GroupSum]:
    start, end = period.bounds(ZoneInfo(current_user.timezone))
    operation_filter = OperationFilter(current_user.id, start, end, current_user.timezone, query.wallet_id)
    days = await analytics_repository.days_in_other_currencies(db, operation_filter, query.currency)
    await rate_loader.ensure_rates(db, days)
    groups = await analytics_repository.operation_groups(
        db, operation_filter, query.currency, settings.exchange_api_history_start
    )
    return [_group_sum(group, query.currency) for group in groups]


def _group_sum(group: OperationGroup, currency: CurrencyEnum) -> GroupSum:
    if group.amount is None:
        # No rate of this currency at all: the API answered for no day of it
        logger.error("No exchange rate %s/%s for %s", group.currency, currency, group.day)
        raise ExchangeRateNotFoundError(group.currency, currency)
    return GroupSum(
        group.day,
        group.type,
        group.category or UNCATEGORIZED,
        group.subcategory,
        Sum(group.amount, group.operations),
    )


def _by_category(
    groups: Iterable[GroupSum], previous_groups: Iterable[GroupSum], operation_type: OperationType
) -> TypeSummary:
    """Categories of both periods: one that stopped shows 0."""
    current, previous = _sums(groups, operation_type), _sums(previous_groups, operation_type)
    total = _total(current)
    categories = []
    for category in current.keys() | previous.keys():
        now, before = current.get(category, {}), previous.get(category, {})
        subcategories = [
            SubcategoryTotal(
                subcategory=subcategory,
                **_compared(now.get(subcategory, Sum()), before.get(subcategory, Sum()), total),
            )
            for subcategory in now.keys() | before.keys()
            if subcategory is not None
        ]
        categories.append(
            CategoryTotal(
                category=category,
                **_compared(sum(now.values(), Sum()), sum(before.values(), Sum()), total),
                subcategories=sorted(
                    subcategories, key=lambda item: (-item.amount, -item.previous_amount, item.subcategory)
                ),
            )
        )
    categories.sort(key=lambda item: (-item.amount, -item.previous_amount, item.category))
    return TypeSummary(total, _total(previous), categories)


def _sums(groups: Iterable[GroupSum], operation_type: OperationType) -> Sums:
    sums: Sums = defaultdict(lambda: defaultdict(Sum))
    for group in groups:
        if group.type == operation_type:
            sums[group.category][group.subcategory] += group.sum
    # Rounded at the finest level: the parts always add up to the totals shown
    return {
        category: {subcategory: item.rounded() for subcategory, item in items.items()}
        for category, items in sums.items()
    }


def _total(sums: Sums) -> Decimal:
    return sum((item for items in sums.values() for item in items.values()), Sum()).amount


def _compared(now: Sum, before: Sum, total: Decimal) -> ComparedFields:
    return {
        "amount": now.amount,
        "share": float(round(now.amount / total * 100, 1)) if total else 0.0,
        "operations": now.operations,
        "previous_amount": before.amount,
        "change_percent": _change(now.amount, before.amount),
    }


def _change(amount: Decimal, previous_amount: Decimal) -> float | None:
    # From nothing to something has no percent
    return float(round((amount - previous_amount) / previous_amount * 100, 1)) if previous_amount else None


def _point(start: date, income: Decimal, expense: Decimal) -> TimelinePoint:
    income, expense = income.quantize(CENT), expense.quantize(CENT)
    return TimelinePoint(date=start, income=income, expense=expense, net=income - expense)


def _period_response(period: Period) -> PeriodResponse:
    return PeriodResponse(date_from=period.date_from, date_to=period.date_to)
