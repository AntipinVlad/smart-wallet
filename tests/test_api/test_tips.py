import logging
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import openai
import pytest
from redis.asyncio import Redis

from app.ai.tips.service import TipAnswer, TipsAnswer
from app.core.config import settings
from app.enums import Language, TipKind
from tests.conftest import assert_error

OCTOBER = {"period": "month", "date": "2026-10-15"}
DAY_IN_OCTOBER = datetime(2026, 10, 5, 12, tzinfo=UTC)
FOOD_TIP = TipAnswer(kind=TipKind.OBSERVATION, category="food", text="Їжа: 1900 грн, нова категорія.")
ANSWER = TipsAnswer(summary="Жовтень: витрати 1900 грн.", tips=[FOOD_TIP])


@pytest.fixture()
def model(client, monkeypatch):
    """The OpenAI client of the app; it answers with ANSWER unless told otherwise."""
    parse = AsyncMock(return_value=SimpleNamespace(output_parsed=ANSWER))
    monkeypatch.setitem(client.app_state, "openai", SimpleNamespace(responses=SimpleNamespace(parse=parse)))
    return parse


@pytest.fixture()
def food_in_october(make_user, make_wallet, make_operation):
    user = make_user()
    make_operation(make_wallet(user), Decimal(1900), DAY_IN_OCTOBER, category="food", subcategory="groceries")
    return user


def tips(client, auth, user, **params):
    return client.get("/api/v1/analytics/tips", params=OCTOBER | params, headers=auth(user))


def model_input(model) -> str:
    return model.await_args.kwargs["input"]


class TestTips:
    def test_tips_of_period(self, client, auth, model, food_in_october):
        # Act
        response = tips(client, auth, food_in_october)

        # Assert
        assert response.status_code == 200
        assert response.json() == {
            "period": {"date_from": "2026-10-01", "date_to": "2026-10-31"},
            "currency": "uah",
            "wallet_id": None,
            "language": "uk",
            "summary": "Жовтень: витрати 1900 грн.",
            "tips": [{"kind": "observation", "category": "food", "text": "Їжа: 1900 грн, нова категорія."}],
        }

    def test_model_gets_facts_of_period_in_language_of_user(
        self, client, auth, model, make_user, make_wallet, make_operation
    ):
        user = make_user(language=Language.EN)
        make_operation(make_wallet(user), Decimal(1900), DAY_IN_OCTOBER, category="food")

        # Act
        response = tips(client, auth, user)

        # Assert
        assert response.json()["language"] == "en"
        assert "Language: English" in model_input(model)
        assert '"category":"food"' in model_input(model)
        assert '"amount":"1900.00"' in model_input(model)

    def test_model_gets_average_of_three_months_before(
        self, client, auth, model, food_in_october, make_wallet, make_operation
    ):
        cash = make_wallet(food_in_october, name="cash")
        for month, amount in ((7, 900), (8, 1200), (9, 1500)):
            make_operation(cash, Decimal(amount), datetime(2026, month, 1, 12, tzinfo=UTC), category="food")

        # Act
        tips(client, auth, food_in_october)

        # Assert
        assert '"baseline_periods":[{"date_from":"2026-09-01"' in model_input(model)
        assert '"baseline":{"amount":"1200.00","difference":"700.00"' in model_input(model)

    def test_without_three_months_of_history_there_is_no_baseline(self, client, auth, model, food_in_october):
        # Act
        tips(client, auth, food_in_october)

        # Assert
        assert '"baseline_periods":[]' in model_input(model)

    def test_empty_period_has_no_tips_and_no_model_call(self, client, auth, model, make_user):
        # Act
        response = tips(client, auth, make_user())

        # Assert
        assert (response.json()["summary"], response.json()["tips"]) == (None, [])
        model.assert_not_awaited()


class TestCache:
    def test_same_facts_are_answered_from_cache(self, client, auth, model, food_in_october):
        tips(client, auth, food_in_october)

        # Act
        response = tips(client, auth, food_in_october)

        # Assert
        assert response.json()["tips"][0]["text"] == FOOD_TIP.text
        assert model.await_count == 1

    def test_new_operation_changes_facts_and_asks_model_again(
        self, client, auth, model, food_in_october, make_wallet, make_operation
    ):
        tips(client, auth, food_in_october)
        make_operation(make_wallet(food_in_october, name="cash"), Decimal(100), DAY_IN_OCTOBER, category="food")
        model.return_value = SimpleNamespace(
            output_parsed=TipsAnswer(summary="Жовтень: витрати 2000 грн.", tips=[FOOD_TIP])
        )

        # Act
        response = tips(client, auth, food_in_october)

        # Assert
        assert response.json()["summary"] == "Жовтень: витрати 2000 грн."
        assert model.await_count == 2

    def test_other_language_asks_model_again(self, client, auth, model, food_in_october):
        tips(client, auth, food_in_october)
        client.patch("/api/v1/users/me", json={"language": "en"}, headers=auth(food_in_october))

        # Act
        tips(client, auth, food_in_october)

        # Assert
        assert model.await_count == 2

    def test_tips_work_when_redis_is_unavailable(self, client, auth, model, food_in_october, monkeypatch, caplog):
        unavailable = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.5)
        monkeypatch.setitem(client.app_state, "redis", unavailable)

        # Act
        with caplog.at_level(logging.ERROR):
            response = tips(client, auth, food_in_october)

        # Assert
        assert response.status_code == 200
        assert "Tips cache" in caplog.text


class TestErrors:
    def test_rate_limit(self, client, auth, model, make_user, monkeypatch):
        monkeypatch.setattr(settings, "tips_rate_limit_per_user", 1)
        user = make_user()
        tips(client, auth, user)

        # Act
        response = tips(client, auth, user)

        # Assert
        assert_error(response, 429, "RATE_LIMIT_EXCEEDED")

    def test_model_unavailable(self, client, auth, model, food_in_october):
        model.side_effect = openai.APIConnectionError(request=None)

        # Act
        response = tips(client, auth, food_in_october)

        # Assert
        assert_error(response, 503, "AI_SERVICE_UNAVAILABLE")

    def test_answer_with_numbers_not_from_facts(self, client, auth, model, food_in_october):
        wrong = TipAnswer(kind=TipKind.WARNING, category="food", text="Їжа: 2500 грн.")
        model.return_value = SimpleNamespace(output_parsed=TipsAnswer(summary="Жовтень.", tips=[wrong]))

        # Act
        response = tips(client, auth, food_in_october)

        # Assert
        assert_error(response, 502, "AI_INVALID_RESPONSE")

    def test_invalid_answer_is_not_cached(self, client, auth, model, food_in_october):
        model.return_value = SimpleNamespace(output_parsed=None)
        tips(client, auth, food_in_october)
        model.return_value = SimpleNamespace(output_parsed=ANSWER)

        # Act
        response = tips(client, auth, food_in_october)

        # Assert
        assert response.status_code == 200

    def test_wallet_of_another_user(self, client, auth, model, make_user, make_wallet):
        foreign = make_wallet(make_user(login="other"))

        # Act
        response = tips(client, auth, make_user(login="owner"), wallet_id=foreign.id)

        # Assert
        assert_error(response, 404, "WALLET_NOT_FOUND")

    def test_tips_require_login(self, client):
        # Act
        response = client.get("/api/v1/analytics/tips")

        # Assert
        assert_error(response, 401, "NOT_AUTHENTICATED")
