import json
import logging
import re
from datetime import UTC, datetime
from typing import Any


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_sensitive_text(record.getMessage()),
        }
        for key in (
            "service",
            "agent_name",
            "workflow_id",
            "correlation_id",
            "message_id",
            "supplier_id",
            "po_number",
            "po_version",
            "operation",
            "duration_ms",
            "outcome",
        ):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        return json.dumps(payload, default=str, separators=(",", ":"))


_SENSITIVE_VALUE = re.compile(
    r"(?i)(authorization|x-api-key|api[_-]?key|agent[_-]?seed)\s*[:=]\s*([^\s,;]+)"
)
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")


def redact_sensitive_text(value: str) -> str:
    value = _BEARER.sub("Bearer [REDACTED]", value)
    value = _SENSITIVE_VALUE.sub(lambda match: f"{match.group(1)}=[REDACTED]", value)
    return value


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
