import logging
import re
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import openai
import pytest
from redis.asyncio import Redis

from app.ai.search_query.service import SearchQueryAnswer
from app.core.config import settings
from tests.conftest import assert_error, at_angle, sync_redis


async def echo_search(input: str, **_):
    """The model keeps the search as it is and finds no days in it."""
    query = re.search(r"<search>(.*)</search>", input).group(1)
    return SimpleNamespace(output_parsed=SearchQueryAnswer(text=query, date_from=None, date_to=None))


def understood_as(fake, text: str, date_from: date | None = None, date_to: date | None = None):
    answer = SearchQueryAnswer(text=text, date_from=date_from, date_to=date_to)
    fake.responses.parse.side_effect = None
    fake.responses.parse.return_value = SimpleNamespace(output_parsed=answer)


@pytest.fixture(autouse=True)
def fake_openai(client, monkeypatch):
    """Every query points at at_angle(0); operations without a vector are found by the text alone."""
    data = [SimpleNamespace(index=0, embedding=at_angle(0))]
    fake = SimpleNamespace(
        embeddings=SimpleNamespace(create=AsyncMock(return_value=SimpleNamespace(data=data))),
        responses=SimpleNamespace(parse=AsyncMock(side_effect=echo_search)),
    )
    fake.with_options = Mock(return_value=fake)
    monkeypatch.setitem(client.app_state, "openai", fake)
    return fake


@pytest.fixture()
def make_embedded(db_session, make_operation):
    def _make_embedded(wallet, description: str, degrees: float):
        operation = make_operation(wallet, description=description)
        operation.embedding, operation.embedding_model = at_angle(degrees), settings.openai_embedding_model
        db_session.commit()
        return operation

    return _make_embedded


def search(client, auth, user, q: str, **params):
    return client.get("/api/v1/operations/search", params={"q": q} | params, headers=auth(user))


def found_ids(response) -> list[int]:
    assert response.status_code == 200, response.text
    return [operation["id"] for operation in response.json()]


def test_finds_operation_by_word_in_description(client, make_user, make_wallet, make_operation, auth):
    user = make_user()
    wallet = make_wallet(user)
    coffee = make_operation(wallet, description="кава зранку")
    make_operation(wallet, description="таксі додому")

    # Act
    response = search(client, auth, user, "кава")

    # Assert
    assert found_ids(response) == [coffee.id]
    assert response.json()[0]["description"] == "кава зранку"


def test_finds_other_form_of_the_word(client, make_user, make_wallet, make_operation, auth):
    user = make_user()
    coffee = make_operation(make_wallet(user), description="кава зранку")

    # Act
    response = search(client, auth, user, "каву")

    # Assert
    assert found_ids(response) == [coffee.id]


def test_finds_word_regardless_of_case(client, make_user, make_wallet, make_operation, auth):
    user = make_user()
    netflix = make_operation(make_wallet(user), description="Netflix")

    # Act
    response = search(client, auth, user, "NETFLIX")

    # Assert
    assert found_ids(response) == [netflix.id]


def test_exact_word_ranks_above_similar_word(client, make_user, make_wallet, make_operation, auth):
    user = make_user()
    wallet = make_wallet(user)
    similar = make_operation(wallet, description="каву з собою", created_at=datetime(2026, 2, 1, tzinfo=UTC))
    exact = make_operation(wallet, description="кава зранку", created_at=datetime(2026, 1, 1, tzinfo=UTC))

    # Act
    response = search(client, auth, user, "кава")

    # Assert
    assert found_ids(response) == [exact.id, similar.id]


@pytest.mark.parametrize("q", ["здоров'я", "здоров’я", "health", "Health"])  # noqa: RUF001
def test_finds_operation_by_category_name(client, make_user, make_wallet, make_operation, auth, q):
    user = make_user()
    wallet = make_wallet(user)
    dentist = make_operation(wallet, description="пломба", category="health", subcategory="doctor")
    make_operation(wallet, description="таксі", category="transport", subcategory="taxi")

    # Act
    response = search(client, auth, user, q)

    # Assert
    assert found_ids(response) == [dentist.id]


def test_finds_operation_by_subcategory_name(client, make_user, make_wallet, make_operation, auth):
    user = make_user()
    wallet = make_wallet(user)
    vitamins = make_operation(wallet, description="вітаміни D3", category="health", subcategory="pharmacy")
    make_operation(wallet, description="пломба", category="health", subcategory="doctor")

    # Act
    response = search(client, auth, user, "аптека")

    # Assert
    assert found_ids(response) == [vitamins.id]


def test_unrelated_query_finds_nothing(client, make_user, make_wallet, make_operation, auth):
    user = make_user()
    make_operation(make_wallet(user), description="кава зранку")

    # Act
    response = search(client, auth, user, "кіно")

    # Assert
    assert found_ids(response) == []


def test_operations_of_other_users_are_not_found(client, make_user, make_wallet, make_operation, auth):
    user = make_user(login="owner")
    own = make_operation(make_wallet(user), description="кава")
    make_operation(make_wallet(make_user(login="other")), description="кава")

    # Act
    response = search(client, auth, user, "кава")

    # Assert
    assert found_ids(response) == [own.id]


def test_search_by_wallet(client, make_user, make_wallet, make_operation, auth):
    user = make_user()
    card = make_wallet(user, name="card")
    on_card = make_operation(card, description="кава")
    make_operation(make_wallet(user, name="cash"), description="кава")

    # Act
    response = search(client, auth, user, "кава", wallet_id=card.id)

    # Assert
    assert found_ids(response) == [on_card.id]


@pytest.mark.parametrize("wallet_owner, missing_id", [(None, 999), ("other", None)], ids=["missing", "of_other_user"])
def test_search_by_unavailable_wallet(client, make_user, make_wallet, auth, wallet_owner, missing_id):
    user = make_user(login="user")
    wallet_id = make_wallet(make_user(login=wallet_owner)).id if wallet_owner else missing_id

    # Act
    response = search(client, auth, user, "кава", wallet_id=wallet_id)

    # Assert
    assert_error(response, 404, "WALLET_NOT_FOUND")


def test_search_by_date_range(client, make_user, make_wallet, make_operation, auth):
    user = make_user()
    wallet = make_wallet(user)
    make_operation(wallet, description="кава", created_at=datetime(2026, 1, 1, tzinfo=UTC))
    inside = make_operation(wallet, description="кава", created_at=datetime(2026, 1, 15, tzinfo=UTC))
    make_operation(wallet, description="кава", created_at=datetime(2026, 2, 1, tzinfo=UTC))

    # Act
    response = search(client, auth, user, "кава", date_from="2026-01-10T00:00:00Z", date_to="2026-01-20T00:00:00Z")

    # Assert
    assert found_ids(response) == [inside.id]


def test_limit_caps_the_number_of_results(client, make_user, make_wallet, make_operation, auth):
    user = make_user()
    wallet = make_wallet(user)
    for day in (1, 2, 3):
        make_operation(wallet, description="кава", created_at=datetime(2026, 1, day, tzinfo=UTC))

    # Act
    response = search(client, auth, user, "кава", limit=2)

    # Assert
    assert len(found_ids(response)) == 2


@pytest.mark.parametrize(
    "params",
    [
        {"q": ""},
        {"q": "   "},
        {"q": "x" * 256},
        {"q": "кава", "limit": 0},
        {"q": "кава", "limit": settings.max_page_size + 1},
    ],
    ids=["empty", "only_spaces", "too_long", "zero_limit", "limit_above_max"],
)
def test_search_rejects_invalid_request(client, make_user, auth, params):
    user = make_user()

    # Act
    response = client.get("/api/v1/operations/search", params=params, headers=auth(user))

    # Assert
    assert_error(response, 422, "VALIDATION_ERROR")


def test_search_requires_login(client):
    # Act
    response = client.get("/api/v1/operations/search", params={"q": "кава"})

    # Assert
    assert_error(response, 401, "NOT_AUTHENTICATED")


def test_finds_operation_by_meaning_without_common_letters(client, make_user, make_wallet, make_embedded, auth):
    user = make_user()
    wallet = make_wallet(user)
    latte = make_embedded(wallet, "лате", degrees=10)
    make_embedded(wallet, "таксі додому", degrees=90)

    # Act
    response = search(client, auth, user, "кава")

    # Assert
    assert found_ids(response) == [latte.id]


def test_operation_found_by_text_and_meaning_comes_first(client, make_user, make_wallet, make_embedded, auth):
    user = make_user()
    wallet = make_wallet(user)
    latte = make_embedded(wallet, "лате", degrees=10)
    coffee = make_embedded(wallet, "кава зранку", degrees=20)

    # Act
    response = search(client, auth, user, "кава")

    # Assert
    assert found_ids(response) == [coffee.id, latte.id]


def test_openai_error_falls_back_to_text_search(
    client, make_user, make_wallet, make_embedded, auth, fake_openai, caplog
):
    user = make_user()
    wallet = make_wallet(user)
    coffee = make_embedded(wallet, "кава зранку", degrees=20)
    make_embedded(wallet, "лате", degrees=10)
    fake_openai.embeddings.create.side_effect = openai.APIConnectionError(request=None)

    # Act
    with caplog.at_level(logging.ERROR, logger="app.services.search"):
        response = search(client, auth, user, "кава")

    # Assert
    assert found_ids(response) == [coffee.id]
    assert [record.levelno for record in caplog.records if record.name == "app.services.search"] == [logging.ERROR]
    # The query is what the user spends money on: it stays out of the logs
    assert "кава" not in caplog.text


def test_query_embedding_waits_briefly_and_is_not_retried(client, make_user, make_wallet, auth, fake_openai):
    user = make_user()
    make_wallet(user)

    # Act
    found_ids(search(client, auth, user, "кава"))

    # Assert
    assert {
        "timeout": settings.search_embedding_timeout_seconds,
        "max_retries": 0,
    } in [call.kwargs for call in fake_openai.with_options.call_args_list]


class TestQueryVectorCache:
    def test_repeated_query_is_embedded_once(self, client, make_user, make_wallet, make_embedded, auth, fake_openai):
        user = make_user()
        latte = make_embedded(make_wallet(user), "лате", degrees=10)
        found_ids(search(client, auth, user, "кава"))

        # Act
        response = search(client, auth, user, "кава")

        # Assert
        assert found_ids(response) == [latte.id]
        fake_openai.embeddings.create.assert_awaited_once()

    def test_vector_is_shared_by_users(self, client, make_user, make_wallet, auth, fake_openai):
        first, second = make_user(login="first"), make_user(login="second")
        found_ids(search(client, auth, first, "кава"))

        # Act
        found_ids(search(client, auth, second, "кава"))

        # Assert
        fake_openai.embeddings.create.assert_awaited_once()

    def test_new_model_does_not_get_vector_of_old_one(self, client, make_user, auth, fake_openai, monkeypatch):
        user = make_user()
        found_ids(search(client, auth, user, "кава"))
        monkeypatch.setattr(settings, "openai_embedding_model", "a-newer-embedding-model")

        # Act
        found_ids(search(client, auth, user, "кава"))

        # Assert
        assert fake_openai.embeddings.create.await_count == 2

    def test_query_text_is_not_stored_in_keys(self, client, make_user, auth):
        user = make_user()

        # Act
        found_ids(search(client, auth, user, "кава"))

        # Assert
        keys = [key.decode() for key in sync_redis.keys("search:*")]
        assert len(keys) == 1
        assert "кава" not in keys[0]
        assert 0 < sync_redis.ttl(keys[0]) <= settings.search_query_vector_ttl_seconds

    def test_search_works_when_redis_is_unavailable(
        self, client, make_user, make_wallet, make_embedded, auth, fake_openai, monkeypatch, caplog
    ):
        user = make_user()
        latte = make_embedded(make_wallet(user), "лате", degrees=10)
        unavailable = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.5)
        monkeypatch.setitem(client.app_state, "redis", unavailable)

        # Act
        with caplog.at_level(logging.ERROR, logger="app.services.search"):
            response = search(client, auth, user, "кава")

        # Assert
        assert found_ids(response) == [latte.id]
        errors = [record for record in caplog.records if record.name == "app.services.search"]
        # Reading and writing the cache both failed; the search itself did not
        assert [record.levelno for record in errors] == [logging.ERROR, logging.ERROR]
        assert "кава" not in caplog.text


def test_search_over_rate_limit_is_answered_by_text_without_openai(
    client, make_user, make_wallet, make_embedded, auth, fake_openai, monkeypatch
):
    monkeypatch.setattr(settings, "search_rate_limit_per_user", 1)
    user = make_user()
    wallet = make_wallet(user)
    coffee = make_embedded(wallet, "кава зранку", degrees=20)
    latte = make_embedded(wallet, "лате", degrees=10)
    assert found_ids(search(client, auth, user, "кава")) == [coffee.id, latte.id]

    # Act
    response = search(client, auth, user, "кави")

    # Assert
    assert found_ids(response) == [coffee.id]
    fake_openai.embeddings.create.assert_awaited_once()


def test_rate_limit_of_one_user_does_not_limit_another(
    client, make_user, make_wallet, make_embedded, auth, fake_openai, monkeypatch
):
    monkeypatch.setattr(settings, "search_rate_limit_per_user", 1)
    first, second = make_user(login="first"), make_user(login="second")
    found_ids(search(client, auth, first, "кава"))
    latte = make_embedded(make_wallet(second), "лате", degrees=10)

    # Act
    response = search(client, auth, second, "кави")

    # Assert
    assert found_ids(response) == [latte.id]


class TestQuestion:
    QUESTION = "скільки я витратив на каву в жовтні"

    def test_question_is_searched_by_its_subject_within_its_days(
        self, client, make_user, make_wallet, make_operation, auth, fake_openai
    ):
        user = make_user()
        wallet = make_wallet(user)
        october = make_operation(wallet, description="кава зранку", created_at=datetime(2026, 10, 5, tzinfo=UTC))
        make_operation(wallet, description="кава зранку", created_at=datetime(2026, 9, 5, tzinfo=UTC))
        make_operation(wallet, description="таксі додому", created_at=datetime(2026, 10, 6, tzinfo=UTC))
        understood_as(fake_openai, "кава", date(2026, 10, 1), date(2026, 10, 31))

        # Act
        response = search(client, auth, user, self.QUESTION)

        # Assert
        assert found_ids(response) == [october.id]
        assert f"<search>{self.QUESTION}</search>" in fake_openai.responses.parse.await_args.kwargs["input"]

    def test_days_of_the_user_are_local(self, client, make_user, make_wallet, make_operation, auth, fake_openai):
        user = make_user(timezone="Europe/Kyiv")
        wallet = make_wallet(user)
        # 1 October 00:30 in Kyiv, still 30 September in UTC
        first_minutes = make_operation(wallet, description="кава", created_at=datetime(2026, 9, 30, 21, 30, tzinfo=UTC))
        make_operation(wallet, description="кава", created_at=datetime(2026, 9, 30, 20, 30, tzinfo=UTC))
        understood_as(fake_openai, "кава", date(2026, 10, 1), date(2026, 10, 31))

        # Act
        response = search(client, auth, user, self.QUESTION)

        # Assert
        assert found_ids(response) == [first_minutes.id]

    def test_days_of_the_filter_and_of_the_question_both_apply(
        self, client, make_user, make_wallet, make_operation, auth, fake_openai
    ):
        user = make_user()
        wallet = make_wallet(user)
        make_operation(wallet, description="кава", created_at=datetime(2026, 10, 5, tzinfo=UTC))
        both = make_operation(wallet, description="кава", created_at=datetime(2026, 10, 20, tzinfo=UTC))
        make_operation(wallet, description="кава", created_at=datetime(2026, 11, 5, tzinfo=UTC))
        understood_as(fake_openai, "кава", date(2026, 10, 1), date(2026, 10, 31))

        # Act
        response = search(client, auth, user, self.QUESTION, date_from="2026-10-10T00:00:00Z")

        # Assert
        assert found_ids(response) == [both.id]

    def test_short_search_is_not_sent_to_model(self, client, make_user, make_wallet, auth, fake_openai):
        user = make_user()
        make_wallet(user)

        # Act
        found_ids(search(client, auth, user, "кава"))

        # Assert
        fake_openai.responses.parse.assert_not_awaited()

    def test_model_waits_briefly_and_is_not_retried(self, client, make_user, make_wallet, auth, fake_openai):
        user = make_user()
        make_wallet(user)

        # Act
        found_ids(search(client, auth, user, self.QUESTION))

        # Assert
        assert {
            "timeout": settings.search_understanding_timeout_seconds,
            "max_retries": 0,
        } in [call.kwargs for call in fake_openai.with_options.call_args_list]

    @pytest.mark.parametrize(
        "failure",
        [openai.APIConnectionError(request=None), None],
        ids=["openai_error", "no_answer"],
    )
    def test_failed_understanding_searches_the_question_as_it_is(
        self, client, make_user, make_wallet, make_operation, auth, fake_openai, caplog, failure
    ):
        user = make_user()
        coffee = make_operation(make_wallet(user), description="кава в жовтні")
        if failure is None:
            fake_openai.responses.parse.side_effect = None
            fake_openai.responses.parse.return_value = SimpleNamespace(output_parsed=None)
        else:
            fake_openai.responses.parse.side_effect = failure

        # Act
        with caplog.at_level(logging.ERROR):
            response = search(client, auth, user, "кава в жовтні")

        # Assert
        assert found_ids(response) == [coffee.id]
        assert "кава" not in caplog.text

    def test_search_over_rate_limit_is_not_sent_to_model(
        self, client, make_user, make_wallet, auth, fake_openai, monkeypatch
    ):
        monkeypatch.setattr(settings, "search_rate_limit_per_user", 1)
        user = make_user()
        make_wallet(user)
        found_ids(search(client, auth, user, "кава"))

        # Act
        found_ids(search(client, auth, user, self.QUESTION))

        # Assert
        fake_openai.responses.parse.assert_not_awaited()
