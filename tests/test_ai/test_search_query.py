import asyncio
import logging
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from app.ai.search_query.service import (
    INSTRUCTIONS,
    SearchQueryAnswer,
    needs_understanding,
    understand_query,
    understood_bounds,
)
from app.core.config import settings
from app.core.exceptions import AiInvalidResponseError

TODAY = date(2026, 10, 15)
KYIV = ZoneInfo("Europe/Kyiv")


@pytest.fixture()
def anyio_backend():
    return "asyncio", {"loop_factory": asyncio.SelectorEventLoop}


def fake_openai(answer: SearchQueryAnswer | None):
    parse = AsyncMock(return_value=SimpleNamespace(output_parsed=answer))
    return SimpleNamespace(responses=SimpleNamespace(parse=parse))


class TestNeedsUnderstanding:
    @pytest.mark.parametrize(
        "query",
        ["кава", "Netflix", "таксі додому", "Starbucks cappuccino"],
    )
    def test_short_search_is_not_sent_to_model(self, query):
        assert needs_understanding(query) is False

    @pytest.mark.parametrize(
        "query",
        [
            "кава вчора",
            "Кава Вчора",
            "таксі у вересні",  # noqa: RUF001
            "coffee yesterday",
            "скільки на таксі",
            "коли стоматолог",
            "доставка суші Glovo",
        ],
        ids=["time_word", "any_case", "month", "english_time_word", "question_word", "question_when", "three_words"],
    )
    def test_question_or_time_or_long_search_is_sent_to_model(self, query):
        # A needless call costs a second; a missed one ignores the days the user asked about
        assert needs_understanding(query) is True


class TestUnderstandQuery:
    @pytest.mark.anyio
    async def test_model_gets_the_query_as_data_and_today_with_weekday(self):
        client = fake_openai(SearchQueryAnswer(text="кава", date_from=None, date_to=None))

        # Act
        await understand_query(client, "скільки кави", TODAY)

        # Assert
        assert client.responses.parse.await_args.kwargs == {
            "model": settings.openai_model,
            "instructions": INSTRUCTIONS,
            "input": "Today: 2026-10-15, Thursday\n<search>скільки кави</search>",
            "text_format": SearchQueryAnswer,
        }

    @pytest.mark.anyio
    async def test_answer_of_the_model_is_returned(self):
        answer = SearchQueryAnswer(text="кава", date_from=date(2026, 10, 1), date_to=date(2026, 10, 31))

        # Act / Assert
        assert await understand_query(fake_openai(answer), "кава в жовтні", TODAY) == answer

    @pytest.mark.anyio
    async def test_no_answer_is_invalid_response(self):
        with pytest.raises(AiInvalidResponseError):
            await understand_query(fake_openai(None), "кава в жовтні", TODAY)

    @pytest.mark.anyio
    async def test_empty_subject_keeps_the_query_and_the_days(self):
        answer = SearchQueryAnswer(text=" ", date_from=date(2026, 10, 1), date_to=date(2026, 10, 31))

        question = "скільки я витратив у жовтні"  # noqa: RUF001

        # Act
        understood = await understand_query(fake_openai(answer), question, TODAY)

        # Assert
        assert understood == SearchQueryAnswer(text=question, date_from=date(2026, 10, 1), date_to=date(2026, 10, 31))

    @pytest.mark.anyio
    async def test_reversed_days_are_dropped(self, caplog):
        answer = SearchQueryAnswer(text="кава", date_from=date(2026, 10, 31), date_to=date(2026, 10, 1))

        # Act
        with caplog.at_level(logging.WARNING, logger="app.ai.search_query.service"):
            understood = await understand_query(fake_openai(answer), "кава в жовтні", TODAY)

        # Assert
        assert understood == SearchQueryAnswer(text="кава", date_from=None, date_to=None)
        assert "кава" not in caplog.text
        assert [record.levelno for record in caplog.records] == [logging.WARNING]


class TestUnderstoodBounds:
    def test_days_become_local_midnights_across_a_clock_change(self):
        # Kyiv moves from +03:00 to +02:00 on 25 October 2026
        answer = SearchQueryAnswer(text="кава", date_from=date(2026, 10, 1), date_to=date(2026, 10, 31))

        # Act
        date_from, date_to = understood_bounds(answer, KYIV)

        # Assert
        assert date_from == datetime(2026, 9, 30, 21, tzinfo=UTC)
        # The search filter includes date_to: the last moment of the day, not the next midnight
        assert date_to == datetime(2026, 10, 31, 22, tzinfo=UTC) - timedelta(microseconds=1)

    def test_missing_days_stay_open(self):
        answer = SearchQueryAnswer(text="кава", date_from=date(2026, 10, 1), date_to=None)

        # Act / Assert
        assert understood_bounds(answer, KYIV) == (datetime(2026, 9, 30, 21, tzinfo=UTC), None)
