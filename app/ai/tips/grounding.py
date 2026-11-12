"""Checks that a text quotes only numbers of the facts: a number the model computed itself may be wrong."""

import re
from collections.abc import Iterable
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.ai.tips.facts import TipFacts

# 1 900 / 1,900 with groups of three, then an optional fraction after a dot or a comma
NUMBER = re.compile(r"(?P<whole>\d{1,3}(?:[ \u00a0\u202f,]\d{3})+|\d+)(?:[.,](?P<fraction>\d+))?")
# Dates are not amounts: 01.10.2026 would read as 1.10 and 2026
DATE = re.compile(r"\d{4}-\d{2}-\d{2}|\d{1,2}\.\d{1,2}\.\d{4}")
ROUNDINGS = (Decimal(1), Decimal("0.1"))


def allowed_numbers(facts: TipFacts) -> set[Decimal]:
    """The numbers of the facts, also rounded to whole units and tenths, as a text would quote them."""
    allowed = set()
    for value in _values(facts.model_dump()):
        allowed.add(value)
        allowed.update(value.quantize(step, rounding=ROUND_HALF_UP) for step in ROUNDINGS)
    return allowed


def foreign_numbers(text: str, allowed: set[Decimal]) -> list[Decimal]:
    return [number for number in _numbers(text) if number not in allowed]


def _numbers(text: str) -> Iterable[Decimal]:
    for match in NUMBER.finditer(DATE.sub(" ", text)):
        whole = re.sub(r"\D", "", match["whole"])
        yield Decimal(f"{whole}.{match['fraction']}" if match["fraction"] else whole)


def _values(value: Any) -> Iterable[Decimal]:
    # Signs are not compared: "-1100" and "1100 less" are the same fact
    if isinstance(value, dict):
        for item in value.values():
            yield from _values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _values(item)
    elif isinstance(value, date):
        yield from (Decimal(part) for part in (value.day, value.month, value.year))
    elif isinstance(value, bool | str) or value is None:
        return
    elif isinstance(value, int | float | Decimal):
        yield abs(Decimal(str(value)))
