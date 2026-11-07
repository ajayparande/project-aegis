"""Structured logging with basic secret redaction."""

import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SECRET_PATTERNS = [
    re.compile(
        r"(?i)(api[_-]?key|api[_-]?secret|access[_-]?token|"
        r"refresh[_-]?token|password|client[_-]?secret)"
        r"(\s*[=:]\s*)([^\s,;]+)"
    ),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+"),
]


def redact_secrets(value: str) -> str:
    """Remove common credentials and tokens from log messages."""

    redacted = value

    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub(
            lambda match: (
                f"{match.group(1)}{match.group(2)}[REDACTED]"
                if len(match.groups()) == 3
                else f"{match.group(1)}[REDACTED]"
            ),
            redacted,
        )

    return redacted


class StructuredFormatter(logging.Formatter):
    """Format log records as structured JSON."""

    def format(self, record: logging.LogRecord) -> str:
        message = redact_secrets(record.getMessage())

        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": message,
        }

        if record.exc_info:
            payload["exception"] = redact_secrets(self.formatException(record.exc_info))

        return json.dumps(payload, ensure_ascii=False)


def setup_logging(
    level: str = "INFO",
    log_file: Path | None = None,
) -> logging.Logger:
    """Configure application logging."""

    logger = logging.getLogger("aegis")
    logger.setLevel(level.upper())
    logger.propagate = False

    # Prevent duplicate handlers if setup is called more than once.
    if logger.handlers:
        return logger

    formatter = StructuredFormatter()

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)

        file_handler = logging.FileHandler(
            log_file,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger
