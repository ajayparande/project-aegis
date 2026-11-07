"""Application configuration for Project Aegis."""

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class AppMode(str, Enum):
    """Supported application operating modes."""

    RESEARCH_ONLY = "RESEARCH_ONLY"


def _resolve_path(value: str) -> Path:
    """Resolve a configured path relative to the project root."""
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


@dataclass(frozen=True, slots=True)
class Settings:
    """Immutable, validated application settings."""

    app_name: str
    mode: AppMode
    database_path: Path
    log_level: str
    log_file: Path | None

    @classmethod
    def from_env(cls) -> "Settings":
        """Build settings from environment variables with safe defaults."""

        requested_mode = (
            os.getenv(
                "AEGIS_MODE",
                AppMode.RESEARCH_ONLY.value,
            )
            .strip()
            .upper()
        )

        try:
            mode = AppMode(requested_mode)
        except ValueError as exc:
            raise ValueError(
                "Unsupported AEGIS_MODE. Only RESEARCH_ONLY is permitted in Phase 1."
            ) from exc

        log_level = os.getenv("AEGIS_LOG_LEVEL", "INFO").strip().upper()
        allowed_log_levels = {
            "DEBUG",
            "INFO",
            "WARNING",
            "ERROR",
            "CRITICAL",
        }

        if log_level not in allowed_log_levels:
            raise ValueError(f"Invalid AEGIS_LOG_LEVEL: {log_level}")

        database_path = _resolve_path(os.getenv("AEGIS_DATABASE_PATH", "data/aegis.db"))

        raw_log_file = os.getenv(
            "AEGIS_LOG_FILE",
            "logs/aegis.log",
        ).strip()

        log_file = _resolve_path(raw_log_file) if raw_log_file else None

        return cls(
            app_name=os.getenv("AEGIS_APP_NAME", "Project Aegis").strip(),
            mode=mode,
            database_path=database_path,
            log_level=log_level,
            log_file=log_file,
        )


def get_settings() -> Settings:
    """Return the current application settings."""
    return Settings.from_env()
