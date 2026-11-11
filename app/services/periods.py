import calendar
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.core.exceptions import InvalidPeriodError
from app.enums import PeriodPreset, TimelineStep

MONTHS_BY_PRESET = {PeriodPreset.MONTH: 1, PeriodPreset.QUARTER: 3, PeriodPreset.YEAR: 12}
# Up to two months a day is one point of a chart; longer ranges are shown by month
MAX_DAILY_RANGE_DAYS = 62
DAY = timedelta(days=1)


@dataclass(frozen=True)
class Period:
    """Days of a report, both included, in the time zone of the user."""

    date_from: date
    date_to: date
    # Length of a preset in months; None for a range the user chose
    months: int | None = None

    @property
    def days(self) -> int:
        return (self.date_to - self.date_from).days + 1

    def previous(self) -> "Period":
        if self.months is None:
            date_to = self.date_from - DAY
            return Period(date_to - timedelta(days=self.days - 1), date_to)

        date_from = shift_months(self.date_from, -self.months)
        previous_end = self.date_from - DAY
        if self.date_to == shift_months(self.date_from, self.months) - DAY:
            return Period(date_from, previous_end, self.months)
        # A period still going on is compared with the same days of the previous one
        return Period(date_from, min(shift_months(self.date_to, -self.months), previous_end), self.months)

    def bounds(self, timezone: ZoneInfo) -> tuple[datetime, datetime]:
        """Start and end (excluded) of the period: midnights in the time zone of the user."""
        start = datetime.combine(self.date_from, time.min, tzinfo=timezone)
        return start, datetime.combine(self.date_to + DAY, time.min, tzinfo=timezone)


def local_day(moment: datetime, timezone: str) -> date:
    return moment.astimezone(ZoneInfo(timezone)).date()


def preset_period(preset: PeriodPreset, anchor: date, today: date) -> Period:
    """The month, quarter or year that holds the anchor day. The current one ends today."""
    if anchor > today:
        raise InvalidPeriodError("Date must not be in the future")
    months = MONTHS_BY_PRESET[preset]
    first_month = (anchor.month - 1) // months * months + 1
    date_from = date(anchor.year, first_month, 1)
    return Period(date_from, min(shift_months(date_from, months) - DAY, today), months)


def range_period(date_from: date, date_to: date, today: date) -> Period:
    if date_to > today:
        raise InvalidPeriodError("Date must not be in the future")
    return Period(date_from, date_to)


def default_step(period: Period) -> TimelineStep:
    if period.months == 1 or (period.months is None and period.days <= MAX_DAILY_RANGE_DAYS):
        return TimelineStep.DAY
    return TimelineStep.MONTH


def bucket_starts(period: Period, step: TimelineStep) -> list[date]:
    """First day of every point of a timeline. A week or a month cut by the period starts with the period."""
    starts = [period.date_from]
    while (following := next_bucket(starts[-1], step)) <= period.date_to:
        starts.append(following)
    return starts


def next_bucket(day: date, step: TimelineStep) -> date:
    match step:
        case TimelineStep.DAY:
            return day + DAY
        case TimelineStep.WEEK:
            # Weeks start on Monday, as in ISO 8601 and date_trunc('week') of PostgreSQL
            return day + timedelta(days=7 - day.weekday())
        case TimelineStep.MONTH:
            return shift_months(day.replace(day=1), 1)


def shift_months(day: date, months: int) -> date:
    """The same day of another month; the last day if that month is shorter (March 31 becomes February 28)."""
    month_index = day.year * 12 + day.month - 1 + months
    year, month = divmod(month_index, 12)
    return date(year, month + 1, min(day.day, calendar.monthrange(year, month + 1)[1]))
