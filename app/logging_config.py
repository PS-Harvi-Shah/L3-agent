import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

from app.config import Settings


STANDARD_LOG_RECORD_KEYS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
}


class StructuredFormatter(logging.Formatter):
    """Format log records as structured JSON lines."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        extra = {
            key: value
            for key, value in record.__dict__.items()
            if key not in STANDARD_LOG_RECORD_KEYS and not key.startswith("_")
        }
        if extra:
            payload["extra"] = extra

        return json.dumps(payload, default=str)


def configure_logging(settings: Settings) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(StructuredFormatter())

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(settings.log_level.upper())

    logging.getLogger("uvicorn.access").handlers.clear()
