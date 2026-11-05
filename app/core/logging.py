import json
import logging
import logging.config
from contextvars import ContextVar
from datetime import datetime, UTC
from typing import Any, Literal

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]
LogFormat = Literal["text", "json"]

# The id of the request being handled. A ContextVar has its own value in every asyncio task, so concurrent
# requests do not see each other's id. Set by RequestContextMiddleware, None outside a request
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

# Attributes every LogRecord has. Everything else on a record came from `extra=` and is written as a field.
# color_message: uvicorn's copy of the message with terminal colors
_STANDARD_RECORD_ATTRIBUTES = set(vars(logging.makeLogRecord({}))) | {"message", "asctime", "request_id",
                                                                      "color_message"}


class JsonFormatter(logging.Formatter):
    """One JSON object per line: log collectors (Loki, ELK, Datadog) can search by every field."""

    def format(self, record: logging.LogRecord) -> str:
        data: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", None),
        }
        data.update({key: value for key, value in vars(record).items() if key not in _STANDARD_RECORD_ATTRIBUTES})
        if record.exc_info:
            data["exception"] = self.formatException(record.exc_info)
        return json.dumps(data, default=str, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    """Readable format for local development."""

    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)-7s [%(request_id)s] %(name)s: %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        if getattr(record, "request_id", None) is None:
            record.request_id = "-"
        return super().format(record)


def _add_request_id(factory: Any) -> Any:
    # A record factory, not a handler filter: it runs for every record, also for handlers added by others
    # (pytest caplog, for example), so request_id is always there
    def make_record(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = factory(*args, **kwargs)
        record.request_id = request_id_var.get()
        return record

    make_record.adds_request_id = True
    return make_record


def setup_logging(level: LogLevel, log_format: LogFormat) -> None:
    formatter = JsonFormatter if log_format == "json" else TextFormatter
    logging.config.dictConfig({
        "version": 1,
        # Loggers created before this call (by uvicorn, sqlalchemy) keep working
        "disable_existing_loggers": False,
        "formatters": {"default": {"()": formatter}},
        "handlers": {"default": {"class": "logging.StreamHandler", "formatter": "default"}},
        "root": {"level": level, "handlers": ["default"]},
        "loggers": {
            # uvicorn has its own handlers: drop them, so its messages go through ours in the same format
            "uvicorn": {"handlers": [], "propagate": True},
            "uvicorn.error": {"handlers": [], "propagate": True},
            "uvicorn.access": {"handlers": [], "propagate": False},
        },
    })
    # Every request is logged by RequestContextMiddleware with the request id, this would repeat it
    logging.getLogger("uvicorn.access").disabled = True
    factory = logging.getLogRecordFactory()
    if not getattr(factory, "adds_request_id", False):
        logging.setLogRecordFactory(_add_request_id(factory))
