from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.core.exceptions import InvalidPeriodError
from app.enums import PeriodPreset, TimelineStep
from app.services.periods import bucket_starts, default_step, preset_period, range_period

TODAY = date(2026, 11, 10)


@pytest.mark.parametrize(
    "preset, anchor, expected",
    [
        (PeriodPreset.MONTH, date(2026, 10, 15), (date(2026, 10, 1), date(2026, 10, 31))),
        (PeriodPreset.MONTH, TODAY, (date(2026, 11, 1), TODAY)),
        (PeriodPreset.QUARTER, date(2026, 5, 20), (date(2026, 4, 1), date(2026, 6, 30))),
        (PeriodPreset.QUARTER, TODAY, (date(2026, 10, 1), TODAY)),
        (PeriodPreset.YEAR, date(2025, 7, 1), (date(2025, 1, 1), date(2025, 12, 31))),
    ],
    ids=["past_month", "current_month_ends_today", "past_quarter", "current_quarter_ends_today", "past_year"],
)
def test_preset_period(preset, anchor, expected):
    # Act
    period = preset_period(preset, anchor, TODAY)

    # Assert
    assert (period.date_from, period.date_to) == expected


def test_range_period():
    # Act
    period = range_period(date(2026, 9, 1), date(2026, 10, 15), TODAY)

    # Assert
    assert (period.date_from, period.date_to) == (date(2026, 9, 1), date(2026, 10, 15))
    assert period.days == 45


@pytest.mark.parametrize(
    "make_period",
    [
        lambda: preset_period(PeriodPreset.MONTH, TODAY + timedelta(days=1), TODAY),
        lambda: range_period(date(2026, 11, 1), TODAY + timedelta(days=1), TODAY),
    ],
    ids=["anchor_in_future", "range_ends_in_future"],
)
def test_period_in_future_is_rejected(make_period):
    # Act
    with pytest.raises(InvalidPeriodError):
        make_period()


@pytest.mark.parametrize(
    "period, expected",
    [
        (preset_period(PeriodPreset.MONTH, TODAY, TODAY), (date(2026, 10, 1), date(2026, 10, 10))),
        (preset_period(PeriodPreset.MONTH, date(2024, 3, 5), TODAY), (date(2024, 2, 1), date(2024, 2, 29))),
        (
            preset_period(PeriodPreset.MONTH, date(2026, 3, 30), date(2026, 3, 30)),
            (date(2026, 2, 1), date(2026, 2, 28)),
        ),
        (preset_period(PeriodPreset.QUARTER, date(2026, 2, 1), TODAY), (date(2025, 10, 1), date(2025, 12, 31))),
        (preset_period(PeriodPreset.YEAR, TODAY, TODAY), (date(2025, 1, 1), date(2025, 11, 10))),
        (range_period(date(2026, 10, 21), date(2026, 10, 30), TODAY), (date(2026, 10, 11), date(2026, 10, 20))),
        (range_period(date(2026, 10, 1), date(2026, 10, 31), TODAY), (date(2026, 8, 31), date(2026, 9, 30))),
    ],
    ids=[
        "current_month_same_days",
        "full_march_to_full_leap_february",
        "march_30_to_end_of_february",
        "full_quarter",
        "current_year_same_days",
        "range_of_same_length_before",
        "range_of_whole_month_is_not_a_month",
    ],
)
def test_previous_period(period, expected):
    # Act
    previous = period.previous()

    # Assert
    assert (previous.date_from, previous.date_to) == expected


def test_period_bounds_are_in_time_zone_of_user():
    # Daylight saving time in Kyiv ends on October 25, 2026: October starts at +03:00 and ends at +02:00
    kyiv = ZoneInfo("Europe/Kyiv")
    period = preset_period(PeriodPreset.MONTH, date(2026, 10, 15), TODAY)

    # Act
    start, end = period.bounds(kyiv)

    # Assert
    assert start == datetime(2026, 10, 1, tzinfo=kyiv)
    assert start.utcoffset() == timedelta(hours=3)
    assert end == datetime(2026, 11, 1, tzinfo=kyiv)
    assert end.utcoffset() == timedelta(hours=2)


@pytest.mark.parametrize(
    "period, expected",
    [
        (preset_period(PeriodPreset.MONTH, TODAY, TODAY), TimelineStep.DAY),
        (preset_period(PeriodPreset.QUARTER, TODAY, TODAY), TimelineStep.MONTH),
        (preset_period(PeriodPreset.YEAR, TODAY, TODAY), TimelineStep.MONTH),
        (range_period(date(2026, 9, 10), date(2026, 11, 10), TODAY), TimelineStep.DAY),
        (range_period(date(2026, 9, 1), date(2026, 11, 10), TODAY), TimelineStep.MONTH),
    ],
    ids=["month", "quarter", "year", "range_up_to_62_days", "longer_range"],
)
def test_default_step(period, expected):
    # Act
    step = default_step(period)

    # Assert
    assert step == expected


@pytest.mark.parametrize(
    "step, expected",
    [
        (TimelineStep.DAY, [date(2026, 10, 28), date(2026, 10, 29), date(2026, 10, 30), date(2026, 10, 31)]),
        (TimelineStep.WEEK, [date(2026, 10, 28)]),
        (TimelineStep.MONTH, [date(2026, 10, 28)]),
    ],
    ids=["day", "week", "month"],
)
def test_bucket_starts_inside_period(step, expected):
    # Wednesday to Saturday: the week and the month start before the period
    period = range_period(date(2026, 10, 28), date(2026, 10, 31), TODAY)

    # Act
    starts = bucket_starts(period, step)

    # Assert
    assert starts == expected


def test_week_buckets_start_on_monday():
    period = range_period(date(2026, 10, 28), date(2026, 11, 10), TODAY)

    # Act
    starts = bucket_starts(period, TimelineStep.WEEK)

    # Assert
    assert starts == [date(2026, 10, 28), date(2026, 11, 2), date(2026, 11, 9)]
