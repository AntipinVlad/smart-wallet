import json
import logging
import logging.config
from collections.abc import Callable
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any, Literal

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]
LogFormat = Literal["text", "json"]

# A ContextVar: every asyncio task, so every request, has its own value
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

# Anything else on a record came from extra= and is written as a field. color_message: uvicorn's colored copy
_STANDARD_RECORD_ATTRIBUTES = set(vars(logging.makeLogRecord({}))) | {
    "message",
    "asctime",
    "request_id",
    "color_message",
}


class JsonFormatter(logging.Formatter):
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
    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)-7s [%(request_id)s] %(name)s: %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        if getattr(record, "request_id", None) is None:
            record.request_id = "-"
        return super().format(record)


class _RequestIdRecordFactory:
    # A record factory, not a filter: it also covers handlers added by others, like pytest caplog
    def __init__(self, factory: Callable[..., logging.LogRecord]) -> None:
        self._factory = factory

    def __call__(self, *args: Any, **kwargs: Any) -> logging.LogRecord:
        record = self._factory(*args, **kwargs)
        record.request_id = request_id_var.get()
        return record


def setup_logging(level: LogLevel, log_format: LogFormat) -> None:
    formatter = JsonFormatter if log_format == "json" else TextFormatter
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {"default": {"()": formatter}},
            "handlers": {"default": {"class": "logging.StreamHandler", "formatter": "default"}},
            "root": {"level": level, "handlers": ["default"]},
            "loggers": {
                # Without uvicorn's own handlers its messages get our format
                "uvicorn": {"handlers": [], "propagate": True},
                "uvicorn.error": {"handlers": [], "propagate": True},
                "uvicorn.access": {"handlers": [], "propagate": False},
            },
        }
    )
    # RequestContextMiddleware already logs every request, with its id
    logging.getLogger("uvicorn.access").disabled = True
    factory = logging.getLogRecordFactory()
    if not isinstance(factory, _RequestIdRecordFactory):
        logging.setLogRecordFactory(_RequestIdRecordFactory(factory))
