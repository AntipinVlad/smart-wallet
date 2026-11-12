import datetime
from decimal import Decimal
from typing import Self

from pydantic import BaseModel, Field, model_validator

from app.core.config import settings
from app.enums import (
    CurrencyEnum,
    Language,
    PeriodPreset,
    PeriodSeries,
    ReportType,
    TimelineSeries,
    TimelineStep,
    TipKind,
)


class AnalyticsQuery(BaseModel):
    period: PeriodPreset | None = Field(
        None, description="Month, quarter or year that holds `date`. The current month if no period is given"
    )
    date: datetime.date | None = Field(None, description="A day of the period, today by default")
    date_from: datetime.date | None = Field(None, description="First day of a range, instead of a period")
    date_to: datetime.date | None = Field(None, description="Last day of a range, included")
    wallet_id: int | None = None
    currency: CurrencyEnum = Field(CurrencyEnum.UAH, description="Currency of the report")

    @model_validator(mode="after")
    def check_period(self) -> Self:
        date_from, date_to = self.date_from, self.date_to
        if date_from is None and date_to is None:
            if self.date is not None and self.period is None:
                raise ValueError("date needs a period")
            return self
        if self.period is not None or self.date is not None:
            raise ValueError("Give either period and date, or date_from and date_to")
        if date_from is None or date_to is None:
            raise ValueError("date_from and date_to go together")
        if date_from > date_to:
            raise ValueError("date_from must not be after date_to")
        if (date_to - date_from).days + 1 > settings.analytics_max_range_days:
            raise ValueError(f"A range must not be longer than {settings.analytics_max_range_days} days")
        return self


class PeriodResponse(BaseModel):
    date_from: datetime.date
    date_to: datetime.date


class ComparedTotal(BaseModel):
    amount: Decimal
    share: float = Field(description="Percent of all income or all expenses of the period")
    operations: int
    previous_amount: Decimal = Field(description="Amount in the previous period")
    change_percent: float | None = Field(description="Change against the previous period; null if it had nothing")


class SubcategoryTotal(ComparedTotal):
    subcategory: str


class CategoryTotal(ComparedTotal):
    category: str = Field(description="uncategorized for operations the worker has not categorized")
    subcategories: list[SubcategoryTotal]


class PreviousPeriod(BaseModel):
    period: PeriodResponse = Field(
        description="The same days of the previous month, quarter or year, or a range of the same length right before"
    )
    income: Decimal
    expense: Decimal
    net: Decimal
    income_change_percent: float | None
    expense_change_percent: float | None


class SummaryResponse(BaseModel):
    period: PeriodResponse
    currency: CurrencyEnum
    wallet_id: int | None
    income: Decimal
    expense: Decimal
    net: Decimal = Field(description="Income minus expenses")
    expense_by_category: list[CategoryTotal]
    income_by_category: list[CategoryTotal]
    previous: PreviousPeriod


class CategoriesQuery(AnalyticsQuery):
    type: ReportType = Field(ReportType.EXPENSE, description="Expenses or income")


class CategoryDataset(BaseModel):
    key: PeriodSeries
    data: list[float] = Field(description="Amount of every category, in the order of labels")


class CategoriesResponse(BaseModel):
    """Chart.js data: labels and datasets go to a pie, doughnut or bar chart as they are."""

    period: PeriodResponse
    previous_period: PeriodResponse = Field(
        description="The same days of the previous month, quarter or year, or a range of the same length right before"
    )
    currency: CurrencyEnum
    wallet_id: int | None
    type: ReportType
    labels: list[str] = Field(
        description="Categories of both periods, largest first; uncategorized for operations without a category"
    )
    datasets: list[CategoryDataset] = Field(description="The period and the previous one; 0 for a missing category")


class TimelineQuery(AnalyticsQuery):
    group_by: TimelineStep | None = Field(
        None, description="Step of the points. By default days for a month or up to 62 days, months otherwise"
    )


class TimelineDataset(BaseModel):
    key: TimelineSeries
    data: list[float] = Field(description="Amount of every step, in the order of labels")


class TimelineResponse(BaseModel):
    """Chart.js data: labels and datasets go to the chart as they are."""

    period: PeriodResponse
    currency: CurrencyEnum
    wallet_id: int | None
    group_by: TimelineStep
    labels: list[datetime.date] = Field(
        description="First day of every step of the period; a step cut by the period starts with it"
    )
    datasets: list[TimelineDataset] = Field(description="Income, expenses and net, with zeros for steps without them")


class TipResponse(BaseModel):
    kind: TipKind = Field(
        description="observation: a notable fact, warning: spending that grew, advice: a step to take"
    )
    category: str | None = Field(description="Category the card is about, as in the summary; null for the period")
    text: str


class TipsResponse(BaseModel):
    """AI tips: every number in them is a number of the summary."""

    period: PeriodResponse
    currency: CurrencyEnum
    wallet_id: int | None
    language: Language
    summary: str | None = Field(description="The period in two or three sentences; null if it has no operations")
    tips: list[TipResponse]
