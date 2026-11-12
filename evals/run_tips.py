import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import openai
from openai import AsyncOpenAI

from app.ai.tips.facts import CategoryFact, ChangeStatus, TipFacts, build_facts
from app.ai.tips.grounding import allowed_numbers, foreign_numbers
from app.ai.tips.service import MAX_TIPS, TipsAnswer, request_tips
from app.core.config import settings
from app.enums import CurrencyEnum, Language, TipKind
from app.schemas import CategoryTotal, PeriodResponse, PreviousPeriod, SubcategoryTotal, SummaryResponse
from app.services.analytics import Averages, Baseline

DATASET = Path(__file__).with_name("tips.jsonl")
CONCURRENCY = 5
MIN_TIPS = 3
PERIOD = PeriodResponse(date_from=date(2026, 10, 1), date_to=date(2026, 10, 31))
PREVIOUS_PERIOD = PeriodResponse(date_from=date(2026, 9, 1), date_to=date(2026, 9, 30))
BASELINE_PERIODS = [
    PREVIOUS_PERIOD,
    PeriodResponse(date_from=date(2026, 8, 1), date_to=date(2026, 8, 31)),
    PeriodResponse(date_from=date(2026, 7, 1), date_to=date(2026, 7, 31)),
]
# Letters only one of the two languages has: a Russian answer to a Ukrainian user is a mistake
RUSSIAN_ONLY = re.compile("[ыэъё]", re.IGNORECASE)
UKRAINIAN_ONLY = re.compile("[іїєґ]", re.IGNORECASE)
CYRILLIC = re.compile("[Ѐ-ӿ]")
PERCENT = re.compile(r"(\d[\d \u00a0\u202f.,]*\d|\d)\s*%")
# Grounding sees only digits: a multiplier in words is a computation it misses
MULTIPLIERS = re.compile(
    r"\b(twice|double[ds]?|triple[ds]?|half|halved|вдвічі|удвічі|втричі|утричі|вчетверо|учетверо|половин\w*)\b",
    re.IGNORECASE,
)
# Names of the facts, or any snake_case key: the user should read what they mean. A bare "status" is plain English
FIELD_NAMES = re.compile(
    r"\b(alerts?|baselines?|status (?:new|gone|up|down|same)|subcategor\w*|[a-z]+_[a-z_]+)\b", re.IGNORECASE
)


@dataclass
class Scenario:
    name: str
    language: Language
    facts: TipFacts
    expect_categories: list[str]


@dataclass
class Outcome:
    scenario: Scenario
    answer: TipsAnswer | None
    error: str | None = None
    failures: list[str] = field(default_factory=list)
    dropped: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


def change(amount: Decimal, previous: Decimal) -> float | None:
    # The same rule as the summary: no percent from nothing
    return float(round((amount - previous) / previous * 100, 1)) if previous else None


def compared(row: dict[str, Any], total: Decimal) -> dict[str, Any]:
    amount, previous = Decimal(row["amount"]), Decimal(row.get("previous", "0.00"))
    return {
        "amount": amount,
        "share": float(round(amount / total * 100, 1)) if total else 0.0,
        "operations": row.get("operations", 1),
        "previous_amount": previous,
        "change_percent": change(amount, previous),
    }


def categories(rows: list[dict[str, Any]]) -> tuple[list[CategoryTotal], Decimal, Decimal]:
    total = sum((Decimal(row["amount"]) for row in rows), Decimal("0.00"))
    previous = sum((Decimal(row.get("previous", "0.00")) for row in rows), Decimal("0.00"))
    items = [
        CategoryTotal(
            category=row["category"],
            subcategories=[
                SubcategoryTotal(subcategory=sub["subcategory"], **compared(sub, total))
                for sub in row.get("subcategories", [])
            ],
            **compared(row, total),
        )
        for row in rows
    ]
    items.sort(key=lambda item: (-item.amount, -item.previous_amount, item.category))
    return items, total, previous


def averages(rows: list[dict[str, Any]]) -> Averages:
    """A scenario with history gives every category an "average"; one without it was not there before."""
    by_category = {row["category"]: Decimal(row["average"]) for row in rows if "average" in row}
    by_subcategory = {
        (row["category"], sub["subcategory"]): Decimal(sub["average"])
        for row in rows
        for sub in row.get("subcategories", [])
        if "average" in sub
    }
    return Averages(
        total=sum(by_category.values(), Decimal("0.00")), categories=by_category, subcategories=by_subcategory
    )


def load_scenarios() -> list[Scenario]:
    scenarios = []
    for line in DATASET.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        expense, expense_total, previous_expense = categories(row["expense"])
        income, income_total, previous_income = categories(row["income"])
        baseline = (
            Baseline(periods=BASELINE_PERIODS, income=averages(row["income"]), expense=averages(row["expense"]))
            if row.get("history")
            else None
        )
        summary = SummaryResponse(
            period=PERIOD,
            currency=CurrencyEnum.UAH,
            wallet_id=None,
            income=income_total,
            expense=expense_total,
            net=income_total - expense_total,
            expense_by_category=expense,
            income_by_category=income,
            previous=PreviousPeriod(
                period=PREVIOUS_PERIOD,
                income=previous_income,
                expense=previous_expense,
                net=previous_income - previous_expense,
                income_change_percent=change(income_total, previous_income),
                expense_change_percent=change(expense_total, previous_expense),
            ),
        )
        scenarios.append(
            Scenario(
                name=row["name"],
                language=Language(row["lang"]),
                facts=build_facts(summary, baseline),
                expect_categories=row["expect_categories"],
            )
        )
    return scenarios


def check(scenario: Scenario, answer: TipsAnswer) -> tuple[list[str], int]:
    """What production would reject, and what a good answer should have."""
    facts, failures = scenario.facts, []
    allowed = allowed_numbers(facts)
    if foreign := foreign_numbers(answer.summary, allowed):
        failures.append(f"summary has numbers not in the facts: {foreign}")

    known = {c.category: c for c in facts.expense_categories + facts.income_categories}
    dropped = 0
    for tip in answer.tips:
        foreign = foreign_numbers(tip.text, allowed)
        unknown = tip.category is not None and tip.category not in known
        dropped += bool(foreign or unknown)
        if foreign:
            failures.append(f"tip has numbers not in the facts {foreign}: {tip.text!r}")
        if unknown:
            failures.append(f"tip about unknown category {tip.category!r}")
        elif tip.category is not None and known[tip.category].status == ChangeStatus.NEW:
            # A share of expenses is fine, a change percent is not: there is nothing to compare with
            shares = share_numbers(known[tip.category])
            if changes := [p for p in PERCENT.findall(tip.text) if foreign_numbers(p, shares)]:
                failures.append(f"change percent {changes} for new category {tip.category}: {tip.text!r}")

    if not MIN_TIPS <= len(answer.tips) <= MAX_TIPS:
        failures.append(f"{len(answer.tips)} tips, expected {MIN_TIPS} to {MAX_TIPS}")

    text = " ".join([answer.summary, *(tip.text for tip in answer.tips)])
    if scenario.language == Language.UK and (RUSSIAN_ONLY.search(text) or not UKRAINIAN_ONLY.search(text)):
        failures.append("not Ukrainian")
    if scenario.language == Language.EN and CYRILLIC.search(text):
        failures.append("not English")
    if multipliers := sorted({match.lower() for match in MULTIPLIERS.findall(text)}):
        failures.append(f"multipliers in words: {multipliers}")
    if names := sorted({match.lower() for match in FIELD_NAMES.findall(text)}):
        failures.append(f"names of facts in the text: {names}")

    covered = {tip.category for tip in answer.tips}
    failures += [f"no tip about {name}" for name in scenario.expect_categories if name not in covered]
    # Money no longer spent is news too: the prompt asks for a card about the largest drop
    if facts.expense_dropped_most and facts.expense_dropped_most[0] not in covered:
        failures.append(f"no tip about the largest drop: {facts.expense_dropped_most[0]}")
    # Warnings follow the flags of the facts: production turns an unflagged one into an observation
    warned = {tip.category for tip in answer.tips if tip.kind == TipKind.WARNING}
    flagged: set[str | None] = set(facts.alerts) | ({None} if facts.net_below_zero else set())
    failures += [f"no warning about {name or 'the net below zero'}" for name in flagged - warned]
    failures += [f"warning without a flag: {name or 'the period'}" for name in warned - flagged]
    return failures, dropped


def share_numbers(category: CategoryFact) -> set[Decimal]:
    """Percents a card about a new category may give: its shares, and its change against the average if any."""
    percents = {Decimal(str(category.share))} | {Decimal(str(sub.share)) for sub in category.subcategories}
    if category.baseline is not None and category.baseline.change_percent is not None:
        percents.add(Decimal(str(category.baseline.change_percent)))
    return percents | {percent.quantize(Decimal(1), rounding=ROUND_HALF_UP) for percent in percents}


async def evaluate(client: AsyncOpenAI, scenario: Scenario, limit: asyncio.Semaphore) -> Outcome:
    async with limit:
        try:
            response = await request_tips(client, scenario.facts, scenario.language)
        except openai.APIError as e:
            return Outcome(scenario, answer=None, error=repr(e))
    answer = response.output_parsed
    usage = response.usage
    outcome = Outcome(
        scenario,
        answer=answer,
        error=None if answer else "no answer",
        input_tokens=usage.input_tokens if usage else 0,
        output_tokens=usage.output_tokens if usage else 0,
    )
    if answer is not None:
        outcome.failures, outcome.dropped = check(scenario, answer)
    return outcome


def report(outcomes: list[Outcome]) -> None:
    passed = sum(not outcome.error and not outcome.failures for outcome in outcomes)
    tips = sum(len(outcome.answer.tips) for outcome in outcomes if outcome.answer)
    dropped = sum(outcome.dropped for outcome in outcomes)
    print(f"Model: {settings.openai_model}, scenarios: {len(outcomes)}\n")
    print(f"Passed all checks: {passed}/{len(outcomes)}")
    print(f"Tips production would drop: {dropped}/{tips}\n")

    for outcome in outcomes:
        scenario = outcome.scenario
        status = "ERROR" if outcome.error else ("ok" if not outcome.failures else "FAIL")
        print(f"[{status}] {scenario.name} ({scenario.language})")
        if outcome.error:
            print(f"    {outcome.error}")
        for failure in outcome.failures:
            print(f"    - {failure}")
        if outcome.answer:
            print(f"    summary: {outcome.answer.summary}")
            for tip in outcome.answer.tips:
                print(f"    {tip.kind:<11} {tip.category or '-':<14} {tip.text}")
        print()

    input_tokens = sum(outcome.input_tokens for outcome in outcomes)
    output_tokens = sum(outcome.output_tokens for outcome in outcomes)
    print(f"Tokens: {input_tokens} input, {output_tokens} output. Multiply by the prices of the model to get the cost")


async def main() -> None:
    scenarios = load_scenarios()
    limit = asyncio.Semaphore(CONCURRENCY)
    async with AsyncOpenAI(
        api_key=settings.openai_api_key.get_secret_value(), timeout=settings.openai_timeout_seconds
    ) as client:
        outcomes = await asyncio.gather(*(evaluate(client, scenario, limit) for scenario in scenarios))
    report(list(outcomes))


if __name__ == "__main__":
    asyncio.run(main())
