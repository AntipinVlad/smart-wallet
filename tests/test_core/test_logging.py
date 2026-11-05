import json
import logging
import sys

import pytest

from app.core.config import settings
from app.core.logging import JsonFormatter, TextFormatter, request_id_var, setup_logging


def make_record(message: str = "Wallet created", **extra) -> logging.LogRecord:
    return logging.getLogger("app.services.wallets").makeRecord(
        "app.services.wallets", logging.INFO, __file__, 10, message, None, None, extra=extra
    )


@pytest.fixture()
def request_id():
    token = request_id_var.set("req-123")
    yield "req-123"
    request_id_var.reset(token)


def test_json_formatter_writes_one_json_object_per_record(request_id):
    # uvicorn adds color_message: a copy of the message with terminal color codes, noise in a log collector
    record = make_record(user_id=7, color_message="Wallet \x1b[36mcreated\x1b[0m")

    # Act
    data = json.loads(JsonFormatter().format(record))

    # Assert
    assert {key: data[key] for key in ("level", "logger", "message", "request_id", "user_id")} == {
        "level": "INFO", "logger": "app.services.wallets", "message": "Wallet created", "request_id": "req-123",
        "user_id": 7,
    }
    assert data["timestamp"].endswith("+00:00")
    assert "color_message" not in data


def test_json_formatter_adds_exception_traceback():
    try:
        raise RuntimeError("database is down")
    except RuntimeError:
        record = logging.getLogger("app").makeRecord("app", logging.ERROR, __file__, 10, "Failed", None,
                                                     exc_info=sys.exc_info())

    # Act
    data = json.loads(JsonFormatter().format(record))

    # Assert
    assert "RuntimeError: database is down" in data["exception"]


def test_record_outside_request_has_no_request_id():
    record = make_record()

    # Act
    data = json.loads(JsonFormatter().format(record))
    line = TextFormatter().format(record)

    # Assert
    assert data["request_id"] is None
    assert "[-]" in line


def test_text_formatter_shows_request_id(request_id):
    # Act
    line = TextFormatter().format(make_record())

    # Assert
    assert "[req-123]" in line
    assert "Wallet created" in line


def test_setup_logging_sets_level_and_silences_uvicorn_access_log():
    try:
        # Act
        setup_logging("WARNING", "json")

        # Assert
        assert logging.getLogger().level == logging.WARNING
        assert isinstance(logging.getLogger().handlers[0].formatter, JsonFormatter)
        # Every request is logged by our middleware, with the request id; the uvicorn access log would repeat it
        assert logging.getLogger("uvicorn.access").disabled
    finally:
        setup_logging(settings.log_level, settings.log_format)
