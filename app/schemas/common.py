from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated
from zoneinfo import available_timezones

from pydantic import AfterValidator, AwareDatetime, Field, StringConstraints

Money = Annotated[Decimal, Field(max_digits=18, decimal_places=2)]
PositiveMoney = Annotated[Money, Field(gt=0)]
NonNegativeMoney = Annotated[Money, Field(ge=0)]

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=127)]


def _not_in_future(value: datetime) -> datetime:
    if value > datetime.now(UTC):
        raise ValueError("Date must not be in the future")
    return value


# A time zone is required: a naive time is ambiguous
PastDatetime = Annotated[AwareDatetime, AfterValidator(_not_in_future)]


TIMEZONES = frozenset(available_timezones())


def _known_timezone(value: str) -> str:
    if value not in TIMEZONES:
        raise ValueError("Unknown time zone, expected an IANA name such as Europe/Kyiv")
    return value


# An IANA name, not an offset: the offset changes with daylight saving time
TimeZone = Annotated[str, Field(max_length=64), AfterValidator(_known_timezone)]
