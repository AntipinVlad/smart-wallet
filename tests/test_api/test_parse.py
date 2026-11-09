import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import openai
import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.enums import CurrencyEnum
from app.models import Operation
from tests.conftest import TIMEZONE, assert_error

PARSE_URL = "/api/v1/operations/parse"


def today(timezone: str = TIMEZONE) -> date:
    return datetime.now(ZoneInfo(timezone)).date()


def call(name: str, **arguments) -> SimpleNamespace:
    # What the Responses API returns for a function call: the arguments are a JSON string
    return SimpleNamespace(type="function_call", name=name, call_id=f"call_{name}", arguments=json.dumps(arguments))


def expense(wallet_id: int | None, amount: str = "85", **arguments) -> SimpleNamespace:
    defaults = {"currency": None, "date": today().isoformat(), "description": "кава"}
    return call("add_expense", wallet_id=wallet_id, amount=amount, **(defaults | arguments))


def income(wallet_id: int | None, amount: str = "30000", **arguments) -> SimpleNamespace:
    defaults = {"currency": None, "date": today().isoformat(), "description": "зарплата"}
    return call("add_income", wallet_id=wallet_id, amount=amount, **(defaults | arguments))


def transfer(from_wallet_id: int | None, to_wallet_id: int | None, amount: str = "1000", **arguments):
    defaults = {"currency": None, "date": today().isoformat()}
    return call(
        "create_transfer",
        from_wallet_id=from_wallet_id,
        to_wallet_id=to_wallet_id,
        amount=amount,
        **(defaults | arguments),
    )


@pytest.fixture()
def model(client, monkeypatch):
    """The OpenAI client of the app. Set model.returns(...) to the function calls the model makes."""
    create = AsyncMock(return_value=SimpleNamespace(output=[]))
    monkeypatch.setitem(client.app_state, "openai", SimpleNamespace(responses=SimpleNamespace(create=create)))

    def returns(*calls: SimpleNamespace) -> None:
        create.return_value = SimpleNamespace(output=list(calls))

    return SimpleNamespace(create=create, returns=returns)


def parse(client, auth, user, text: str = "кава 85 грн з картки"):
    return client.post(PARSE_URL, json={"text": text}, headers=auth(user))


def operations_count(db_session) -> int:
    return db_session.scalar(select(func.count()).select_from(Operation))


def local_date(created_at: str, timezone: str = TIMEZONE) -> date:
    return datetime.fromisoformat(created_at).astimezone(ZoneInfo(timezone)).date()


class TestDrafts:
    def test_expense_draft_is_returned_and_not_saved(self, client, db_session, make_user, make_wallet, auth, model):
        user = make_user()
        card = make_wallet(user, name="card", balance=Decimal(200))
        make_wallet(user, name="cash")
        model.returns(expense(card.id, amount="85", description="кава"))
        started = datetime.now(UTC)

        # Act
        response = parse(client, auth, user)

        # Assert
        assert response.status_code == 200
        (draft,) = response.json()
        assert draft["type"] == "expense"
        assert draft["wallet_id"] == card.id
        assert Decimal(draft["amount"]) == Decimal(85)
        assert draft["currency"] == "uah"
        assert draft["original"] is None
        assert draft["exchange_rate"] is None
        assert draft["description"] == "кава"
        assert started - timedelta(seconds=1) <= datetime.fromisoformat(draft["created_at"]) <= datetime.now(UTC)
        assert operations_count(db_session) == 0
        db_session.refresh(card)
        assert card.balance == Decimal(200)

    def test_income_draft(self, client, make_user, make_wallet, auth, model):
        user = make_user()
        card = make_wallet(user, name="card")
        make_wallet(user, name="cash")
        model.returns(income(card.id, amount="30000", description="зарплата"))

        # Act
        response = parse(client, auth, user, "зарплата 30000 на картку")

        # Assert
        assert response.status_code == 200
        (draft,) = response.json()
        assert (draft["type"], draft["wallet_id"], Decimal(draft["amount"])) == ("income", card.id, Decimal(30000))

    def test_transfer_draft(self, client, make_user, make_wallet, auth, model):
        user = make_user()
        card = make_wallet(user, name="card")
        savings = make_wallet(user, name="savings")
        model.returns(transfer(card.id, savings.id, amount="1000"))

        # Act
        response = parse(client, auth, user, "переказав 1000 з картки на savings")

        # Assert
        assert response.status_code == 200
        (draft,) = response.json()
        assert draft["type"] == "transfer"
        assert (draft["from_wallet_id"], draft["to_wallet_id"]) == (card.id, savings.id)
        assert (Decimal(draft["amount"]), draft["currency"]) == (Decimal(1000), "uah")

    def test_several_operations_in_one_text(self, client, make_user, make_wallet, auth, model):
        user = make_user()
        card = make_wallet(user, name="card")
        savings = make_wallet(user, name="savings")
        model.returns(expense(card.id, amount="85"), transfer(card.id, savings.id, amount="1000"))

        # Act
        response = parse(client, auth, user, "кава 85, переказав 1000 на savings")

        # Assert
        assert response.status_code == 200
        assert [draft["type"] for draft in response.json()] == ["expense", "transfer"]

    def test_past_date_from_text(self, client, make_user, make_wallet, auth, model):
        user = make_user()
        card = make_wallet(user, name="card")
        make_wallet(user, name="cash")
        yesterday = today() - timedelta(days=1)
        model.returns(expense(card.id, date=yesterday.isoformat()))

        # Act
        response = parse(client, auth, user, "вчора кава 85")

        # Assert
        assert response.status_code == 200
        assert local_date(response.json()[0]["created_at"]) == yesterday

    def test_draft_can_be_confirmed_by_operation_endpoint(self, client, make_user, make_wallet, auth, model):
        user = make_user()
        card = make_wallet(user, name="card", balance=Decimal(200))
        make_wallet(user, name="cash")
        model.returns(expense(card.id, amount="85", date=(today() - timedelta(days=1)).isoformat()))
        draft = parse(client, auth, user).json()[0]

        # Act
        confirmed = client.post(
            "/api/v1/operations/expense",
            json={key: draft[key] for key in ("wallet_id", "amount", "description", "created_at")},
            headers=auth(user),
        )

        # Assert
        assert confirmed.status_code == 201
        assert datetime.fromisoformat(confirmed.json()["created_at"]) == datetime.fromisoformat(draft["created_at"])


class TestWallet:
    def test_not_named_wallet_stays_empty(self, client, make_user, make_wallet, auth, model):
        user = make_user()
        make_wallet(user, name="card")
        make_wallet(user, name="cash")
        model.returns(expense(None))

        # Act
        response = parse(client, auth, user, "кава 85")

        # Assert
        assert response.status_code == 200
        (draft,) = response.json()
        assert draft["wallet_id"] is None
        assert draft["currency"] is None

    def test_only_wallet_is_used_when_not_named(self, client, make_user, make_wallet, auth, model):
        user = make_user()
        card = make_wallet(user, name="card")
        model.returns(expense(None))

        # Act
        response = parse(client, auth, user, "кава 85")

        # Assert
        assert response.status_code == 200
        assert response.json()[0]["wallet_id"] == card.id

    def test_model_can_choose_only_wallets_of_user(self, client, make_user, make_wallet, auth, model):
        user = make_user(login="owner")
        card = make_wallet(user, name="card")
        cash = make_wallet(user, name="cash")
        foreign = make_wallet(make_user(login="other"), name="foreign")

        # Act
        parse(client, auth, user)

        # Assert
        tools = {tool["name"]: tool["parameters"]["properties"] for tool in model.create.await_args.kwargs["tools"]}
        wallet_fields = [
            tools["add_expense"]["wallet_id"],
            tools["add_income"]["wallet_id"],
            tools["create_transfer"]["from_wallet_id"],
            tools["create_transfer"]["to_wallet_id"],
        ]
        for wallet_field in wallet_fields:
            allowed = [option["enum"] for option in wallet_field["anyOf"] if "enum" in option]
            assert allowed == [[card.id, cash.id]]
            assert foreign.id not in allowed[0]


class TestCurrency:
    def test_foreign_currency_is_converted_to_wallet_currency(
        self, client, make_user, make_wallet, auth, model, mock_exchange_rate
    ):
        user = make_user()
        card = make_wallet(user, name="card", currency=CurrencyEnum.UAH)
        make_wallet(user, name="cash")
        model.returns(expense(card.id, amount="20", currency="usd"))

        # Act
        response = parse(client, auth, user, "20 доларів з картки")

        # Assert
        assert response.status_code == 200
        (draft,) = response.json()
        assert (Decimal(draft["amount"]), draft["currency"]) == (Decimal(800), "uah")
        assert draft["original"] == {"amount": "20.00", "currency": "usd"}
        assert Decimal(draft["exchange_rate"]) == Decimal(40)
        mock_exchange_rate.assert_awaited_once_with(
            client.app_state["http_session"], CurrencyEnum.USD, CurrencyEnum.UAH
        )

    def test_transfer_is_converted_to_source_wallet_currency(
        self, client, make_user, make_wallet, auth, model, mock_exchange_rate
    ):
        user = make_user()
        dollars = make_wallet(user, name="dollars", currency=CurrencyEnum.USD)
        card = make_wallet(user, name="card", currency=CurrencyEnum.UAH)
        model.returns(transfer(dollars.id, card.id, amount="400", currency="uah"))

        # Act
        response = parse(client, auth, user, "переказав 400 грн з dollars на card")

        # Assert
        assert response.status_code == 200
        (draft,) = response.json()
        assert (Decimal(draft["amount"]), draft["currency"]) == (Decimal(16000), "usd")
        mock_exchange_rate.assert_awaited_once_with(
            client.app_state["http_session"], CurrencyEnum.UAH, CurrencyEnum.USD
        )

    @pytest.mark.parametrize("currency", [None, "uah"], ids=["not_named", "same_as_wallet"])
    def test_wallet_currency_is_not_converted(
        self, client, make_user, make_wallet, auth, model, mock_exchange_rate, currency
    ):
        user = make_user()
        card = make_wallet(user, name="card", currency=CurrencyEnum.UAH)
        make_wallet(user, name="cash")
        model.returns(expense(card.id, amount="85", currency=currency))

        # Act
        response = parse(client, auth, user)

        # Assert
        (draft,) = response.json()
        assert (Decimal(draft["amount"]), draft["original"], draft["exchange_rate"]) == (Decimal(85), None, None)
        mock_exchange_rate.assert_not_awaited()

    def test_currency_without_wallet_is_kept_as_written(
        self, client, make_user, make_wallet, auth, model, mock_exchange_rate
    ):
        user = make_user()
        make_wallet(user, name="card")
        make_wallet(user, name="cash")
        model.returns(expense(None, amount="20", currency="usd"))

        # Act
        response = parse(client, auth, user, "20 доларів")

        # Assert
        (draft,) = response.json()
        assert (Decimal(draft["amount"]), draft["currency"], draft["original"]) == (Decimal(20), "usd", None)
        mock_exchange_rate.assert_not_awaited()


class TestModelRequest:
    def test_model_gets_text_wallets_and_users_today(self, client, make_user, make_wallet, auth, model):
        # +14 hours: the local date differs from the UTC date for most of the day
        user = make_user(timezone="Pacific/Kiritimati")
        make_wallet(user, name="Monobank", currency=CurrencyEnum.UAH)
        make_wallet(user, name="Savings", currency=CurrencyEnum.USD)

        # Act
        parse(client, auth, user, "кава 85 грн з картки")

        # Assert
        request = model.create.await_args.kwargs
        assert request["model"] == settings.openai_model
        assert {tool["name"] for tool in request["tools"]} == {"add_expense", "add_income", "create_transfer"}
        assert request["tool_choice"] == "auto"
        assert request["parallel_tool_calls"] is True
        assert "кава 85 грн з картки" in request["input"]
        assert "Monobank" in request["input"]
        assert "Savings" in request["input"]
        assert today("Pacific/Kiritimati").isoformat() in request["input"]

    def test_model_gives_amount_as_number(self, client, make_user, make_wallet, auth, model):
        # With a string allowed the model copies "1,5к" from the text
        user = make_user()
        make_wallet(user)

        # Act
        parse(client, auth, user)

        # Assert
        for tool in model.create.await_args.kwargs["tools"]:
            amount = tool["parameters"]["properties"]["amount"]
            assert amount["type"] == "number", tool["name"]
            assert "anyOf" not in amount, tool["name"]


class TestErrors:
    def test_text_without_operation(self, client, make_user, make_wallet, auth, model):
        user = make_user()
        make_wallet(user)
        model.returns()

        # Act
        response = parse(client, auth, user, "привіт, як справи?")

        # Assert
        assert_error(response, 422, "OPERATION_NOT_RECOGNIZED")

    def test_model_unavailable(self, client, make_user, make_wallet, auth, model):
        user = make_user()
        make_wallet(user)
        model.create.side_effect = openai.APIConnectionError(request=None)

        # Act
        response = parse(client, auth, user)

        # Assert
        assert_error(response, 503, "AI_SERVICE_UNAVAILABLE")

    @pytest.mark.parametrize(
        "answer",
        [
            lambda wallets: expense(wallets["foreign"]),
            lambda wallets: transfer(wallets["foreign"], wallets["card"]),
            lambda wallets: transfer(wallets["card"], wallets["foreign"]),
            lambda wallets: expense(wallets["card"], amount="-5"),
            lambda wallets: call("delete_wallet", wallet_id=wallets["card"]),
        ],
        ids=["foreign_wallet", "foreign_source", "foreign_destination", "negative_amount", "unknown_function"],
    )
    def test_invalid_model_answer_creates_no_draft(self, client, make_user, make_wallet, auth, model, answer):
        user = make_user(login="owner")
        wallets = {
            "card": make_wallet(user, name="card").id,
            "cash": make_wallet(user, name="cash").id,
            "foreign": make_wallet(make_user(login="other"), name="foreign").id,
        }
        model.returns(answer(wallets))

        # Act
        response = parse(client, auth, user)

        # Assert
        assert_error(response, 502, "AI_INVALID_RESPONSE")

    @pytest.mark.parametrize("payload", [{}, {"text": ""}, {"text": "a" * 501}], ids=["no_text", "empty", "too_long"])
    def test_parse_rejects_invalid_request(self, client, make_user, auth, model, payload):
        user = make_user()

        # Act
        response = client.post(PARSE_URL, json=payload, headers=auth(user))

        # Assert
        assert_error(response, 422, "VALIDATION_ERROR")
        model.create.assert_not_awaited()

    def test_parse_requests_are_limited_per_user(self, client, make_user, make_wallet, auth, model, monkeypatch):
        monkeypatch.setattr(settings, "parse_rate_limit_per_user", 2)
        user = make_user(login="user")
        other = make_user(login="other")
        model.returns(expense(make_wallet(user).id))
        for _ in range(2):
            parse(client, auth, user)

        # Act
        response = parse(client, auth, user)
        other_response = parse(client, auth, other)

        # Assert
        assert_error(response, 429, "RATE_LIMIT_EXCEEDED")
        assert other_response.status_code != 429
        assert model.create.await_count == 3
