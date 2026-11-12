import logging
from typing import Annotated, Any

import openai
from openai import AsyncOpenAI
from openai.types.responses import ParsedResponse
from pydantic import BaseModel, Field, JsonValue, create_model

from app.ai.tips.facts import TipFacts
from app.ai.tips.grounding import allowed_numbers, foreign_numbers
from app.core.config import settings
from app.core.exceptions import AiInvalidResponseError, AiServiceUnavailableError
from app.enums import Language, TipKind

logger = logging.getLogger(__name__)

MAX_TIPS = 5
LANGUAGE_NAMES = {Language.UK: "Ukrainian", Language.EN: "English"}

INSTRUCTIONS = """\
You explain a personal finance report to its owner.
The facts are computed by the app and are exact. Never calculate: do not add, subtract, multiply, divide or average
numbers, and never write a number that is not in the facts, not even in words like "twice" or "half". Every
amount, difference and percent you may need is already there. You may round amounts to whole units and percents to
whole numbers.
The facts are for you, not for the user: never name their fields, like alert, baseline, status or subcategory. Say
what they mean instead. Never say that something is flagged, marked or needs attention by the app: say why it matters.
difference is this period minus the previous one. status new means there is nothing to compare with: say it is new,
never give a percent. status gone means nothing this period.
baseline compares this period with the average of the baseline_periods. When it is there, it says more than the
previous period: one cheap or expensive month is no trend. Say "the average of the last three months" for it.
When baseline_periods is empty, the user has no history for an average yet: compare with the previous period only.
Write dates in words, like "1-31 October". Category keys are English words: translate them into the language.
summary: two or three sentences about the period: income, expenses, net and the main change.
tips: three to five cards, the most important first. kind warning is only for what the facts flag: one card for every
category in alerts, and one card with category null if net_below_zero is true. Never use warning for anything else.
A category can be in alerts because of a subcategory with alert: name that subcategory in the card.
If expense_dropped_most is not empty, one card is about its first category: money no longer spent is news too.
kind observation is a notable fact, advice is one practical step the facts support. category is the key of the
category the card is about; null for a card about the totals or the net. One or two sentences per card. Address the
user directly.
"""


CATEGORY_HINT = "Key of a category from the facts; null for a card about the totals or the net"


class TipAnswer(BaseModel):
    kind: TipKind
    category: str | None = Field(description=CATEGORY_HINT)
    text: str


class TipsAnswer(BaseModel):
    summary: str
    tips: list[TipAnswer]


def answer_format(facts: TipFacts) -> type[TipsAnswer]:
    """The schema lists only the categories of these facts: the model cannot name another one."""
    categories: list[JsonValue] = [c.category for c in facts.expense_categories + facts.income_categories]
    category: Any = Annotated[str, Field(json_schema_extra={"enum": categories})] | None if categories else None
    tip = create_model("TipAnswer", __base__=TipAnswer, category=(category, Field(description=CATEGORY_HINT)))
    return create_model("TipsAnswer", __base__=TipsAnswer, tips=(list[tip], ...))


def describe(facts: TipFacts, language: Language) -> str:
    return f"Language: {LANGUAGE_NAMES[language]}\n<facts>{facts.model_dump_json()}</facts>"


async def request_tips(openai_client: AsyncOpenAI, facts: TipFacts, language: Language) -> ParsedResponse[TipsAnswer]:
    # The only place that talks to the model: the evals send exactly what production sends
    return await openai_client.responses.parse(
        model=settings.openai_model,
        instructions=INSTRUCTIONS,
        input=describe(facts, language),
        text_format=answer_format(facts),
    )


async def write_tips(openai_client: AsyncOpenAI, facts: TipFacts, language: Language) -> TipsAnswer:
    try:
        response = await request_tips(openai_client, facts, language)
    except openai.APIError as e:
        logger.error("Tips not written, OpenAI request failed: %r", e)
        raise AiServiceUnavailableError() from e

    result = response.output_parsed
    if result is None:
        logger.error("Tips not written, the model returned no answer")
        raise AiInvalidResponseError()
    return _grounded(result, facts)


def _grounded(answer: TipsAnswer, facts: TipFacts) -> TipsAnswer:
    """Drops cards the facts do not support. A summary that is not supported has no replacement."""
    allowed = allowed_numbers(facts)
    if foreign := foreign_numbers(answer.summary, allowed):
        logger.error("Tips not written, summary has numbers not in the facts: %s", foreign)
        raise AiInvalidResponseError()

    categories = {c.category for c in facts.expense_categories + facts.income_categories}
    tips = []
    for tip in answer.tips:
        if foreign := foreign_numbers(tip.text, allowed):
            logger.warning("Tip dropped, numbers not in the facts: %s", foreign)
        elif tip.category is not None and tip.category not in categories:
            logger.warning("Tip dropped, category %r not in the facts", tip.category)
        elif tip.kind == TipKind.WARNING and not _flagged(tip.category, facts):
            # The text is still right, only the alarm is not: keep the card, without the warning
            logger.warning("Tip about %s is not flagged, warning made an observation", tip.category or "the period")
            tips.append(tip.model_copy(update={"kind": TipKind.OBSERVATION}))
        else:
            tips.append(tip)

    if not tips:
        logger.error("Tips not written, no tip of %s is supported by the facts", len(answer.tips))
        raise AiInvalidResponseError()
    return TipsAnswer(summary=answer.summary, tips=tips[:MAX_TIPS])


def _flagged(category: str | None, facts: TipFacts) -> bool:
    return facts.net_below_zero if category is None else category in facts.alerts
