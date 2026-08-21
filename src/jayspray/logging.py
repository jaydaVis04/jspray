from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from typing import Any

SENSITIVE_KEY = re.compile(
    r"(?:authorization|cookie|nonce|password|passwd|secret|token|session)", re.I
)
SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b(authorization|cookie|password|passwd|secret|session|token|nonce)"
    r"(\s*[:=]\s*)([^\s,;]+)"
)
BEARER_VALUE = re.compile(r"(?i)\b(Bearer\s+)[A-Za-z0-9._~+/-]+=*")
URL_USERINFO = re.compile(r"(https?://)[^/@\s]+@", re.I)


def redact_text(value: str) -> str:
    redacted = URL_USERINFO.sub(r"\1[REDACTED]@", value)
    redacted = BEARER_VALUE.sub(r"\1[REDACTED]", redacted)
    return SENSITIVE_ASSIGNMENT.sub(r"\1\2[REDACTED]", redacted)


def redact(value: Any, key: str = "") -> Any:
    if SENSITIVE_KEY.search(key):
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {str(k): redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


class StructuredFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        fields = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "message": redact_text(record.getMessage()),
        }
        context = getattr(record, "context", None)
        if isinstance(context, Mapping):
            fields.update(redact(context))
        return json.dumps(fields, sort_keys=True, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(StructuredFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)


def log_event(logger: logging.Logger, level: int, message: str, **context: Any) -> None:
    logger.log(level, message, extra={"context": redact(context)})
