import logging
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import openai
import pytest

from app.ai.tips.facts import TipFacts, build_facts
from app.ai.tips.service import TipAnswer, TipsAnswer, write_tips
from app.core.config import settings
from app.core.exceptions import AiInvalidResponseError, AiServiceUnavailableError
from app.enums import Language, TipKind
from tests.test_ai.test_tips_facts import Item, SummaryBuilder

pytestmark = pytest.mark.anyio


@pytest.fixture()
def anyio_backend():
    return "asyncio"


def facts() -> TipFacts:
    # October: food 1900.00, was 800.00 (+1100.00, +137.5%); transport 4500.00, new; salary 65000.00
    return build_facts(
        SummaryBuilder(
            expense=[Item("transport", "4500.00"), Item("food", "1900.00", previous="800.00")],
            income=[Item("salary", "65000.00", previous="65000.00")],
        ).build()
    )


def tip(text: str, category: str | None = "food", kind: TipKind = TipKind.OBSERVATION) -> TipAnswer:
    return TipAnswer(kind=kind, category=category, text=text)


def answer(*tips: TipAnswer, summary: str = "Жовтень: витрати 6400 грн, дохід 65000 грн.") -> TipsAnswer:
    return TipsAnswer(summary=summary, tips=list(tips))


def fake_openai(result: TipsAnswer | None = None, error: Exception | None = None):
    parse = AsyncMock(return_value=SimpleNamespace(output_parsed=result), side_effect=error)
    return SimpleNamespace(responses=SimpleNamespace(parse=parse))


async def test_model_gets_facts_and_language():
    client = fake_openai(answer(tip("Їжа: 1900 грн.")))

    # Act
    await write_tips(client, facts(), Language.EN)

    # Assert
    request = client.responses.parse.await_args.kwargs
    assert request["model"] == settings.openai_model
    assert issubclass(request["text_format"], TipsAnswer)
    assert "Language: English" in request["input"]
    assert facts().model_dump_json() in request["input"]


async def test_schema_allows_only_categories_of_facts():
    # The model once marked a card about total expenses with the category "expense"
    client = fake_openai(answer(tip("Їжа: 1900 грн.")))

    # Act
    await write_tips(client, facts(), Language.UK)

    # Assert
    schema = client.responses.parse.await_args.kwargs["text_format"].model_json_schema()
    category = schema["$defs"]["TipAnswer"]["properties"]["category"]
    assert category["anyOf"] == [{"enum": ["transport", "food", "salary"], "type": "string"}, {"type": "null"}]


async def test_answer_of_model_is_returned():
    model_answer = answer(
        tip("Їжа: 1900 грн, плюс 1100 грн порівняно з вереснем (+137,5%)."),
        tip("Транспорт: нова категорія, 4500 грн.", category="transport", kind=TipKind.WARNING),
        tip("Відкладіть частину зарплати.", category=None, kind=TipKind.ADVICE),
    )

    # Act
    result = await write_tips(fake_openai(model_answer), facts(), Language.UK)

    # Assert
    assert result == model_answer


@pytest.mark.parametrize(
    "text",
    [
        "Їжа: 1 900 грн.",
        "Food: 1,900.00 UAH.",
        "Їжа: 1900,00 грн, ріст 137,5 %.",
        "Їжа виросла приблизно на 138%.",
        "Їжа: 29,7% витрат.",
        "Різниця \u22121 100 грн.",
        "Період 1\u201331 жовтня 2026, їжа 1900 грн.",
        "Період 01.10.2026-31.10.2026 (2026-10-01 - 2026-10-31), їжа 1900 грн.",
        "Без чисел, але по суті.",
    ],
    ids=[
        "space_thousands",
        "comma_thousands",
        "decimal_comma",
        "rounded_percent",
        "share",
        "negative_difference",
        "days_of_period",
        "dates_in_digits",
        "no_numbers",
    ],
)
async def test_numbers_from_facts_are_accepted(text):
    # Act
    result = await write_tips(fake_openai(answer(tip(text))), facts(), Language.UK)

    # Assert
    assert [item.text for item in result.tips] == [text]


@pytest.mark.parametrize(
    "text",
    ["Їжа виросла на 1200 грн.", "Їжа: 3% вашого доходу.", "Їжа виросла в 2,4 раза."],
    ids=["wrong_difference", "percent_by_model", "ratio_by_model"],
)
async def test_tip_with_number_not_from_facts_is_dropped(text, caplog):
    good = tip("Їжа: 1900 грн.")

    # Act
    with caplog.at_level(logging.WARNING, logger="app.ai.tips.service"):
        result = await write_tips(fake_openai(answer(tip(text), good)), facts(), Language.UK)

    # Assert
    assert result.tips == [good]
    assert "not in the facts" in caplog.text


@pytest.mark.parametrize("category", ["salary", None], ids=["category_without_alert", "net_not_below_zero"])
async def test_warning_without_alert_is_an_observation(category):
    # The facts decide what needs a warning: food and transport have alerts, salary and the net do not
    model_answer = answer(tip("Зарплата 65000 грн.", category=category, kind=TipKind.WARNING))

    # Act
    result = await write_tips(fake_openai(model_answer), facts(), Language.UK)

    # Assert
    assert [item.kind for item in result.tips] == [TipKind.OBSERVATION]


async def test_warning_with_alert_stays():
    model_answer = answer(tip("Їжа: 1900 грн.", kind=TipKind.WARNING))

    # Act
    result = await write_tips(fake_openai(model_answer), facts(), Language.UK)

    # Assert
    assert result.tips[0].kind == TipKind.WARNING


async def test_tip_about_category_not_in_facts_is_dropped():
    good = tip("Їжа: 1900 грн.")

    # Act
    result = await write_tips(fake_openai(answer(tip("Подорожі.", category="travel"), good)), facts(), Language.UK)

    # Assert
    assert result.tips == [good]


async def test_summary_with_number_not_from_facts_is_invalid():
    client = fake_openai(answer(tip("Їжа: 1900 грн."), summary="Ви заощадили 60000 грн."))

    # Act / Assert
    with pytest.raises(AiInvalidResponseError):
        await write_tips(client, facts(), Language.UK)


async def test_answer_without_valid_tips_is_invalid():
    client = fake_openai(answer(tip("Їжа виросла на 1200 грн.")))

    # Act / Assert
    with pytest.raises(AiInvalidResponseError):
        await write_tips(client, facts(), Language.UK)


async def test_model_without_answer_is_invalid():
    # Act / Assert
    with pytest.raises(AiInvalidResponseError):
        await write_tips(fake_openai(None), facts(), Language.UK)


async def test_failed_request_means_service_unavailable():
    client = fake_openai(error=openai.APIConnectionError(request=None))

    # Act / Assert
    with pytest.raises(AiServiceUnavailableError):
        await write_tips(client, facts(), Language.UK)


async def test_only_first_tips_are_kept():
    tips = [tip("Їжа: 1900 грн.") for _ in range(7)]

    # Act
    result = await write_tips(fake_openai(answer(*tips)), facts(), Language.UK)

    # Assert
    assert len(result.tips) == 5


def test_facts_have_numbers_the_texts_above_quote():
    # A change in the builder shows here first, not as a puzzling failure of a text above
    food = facts().expense_categories[1]

    # Assert
    assert (food.amount, food.difference, food.change_percent, food.share) == (
        Decimal("1900.00"),
        Decimal("1100.00"),
        137.5,
        29.7,
    )
