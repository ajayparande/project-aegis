"""Local credential configuration only; no authentication, networking or order APIs.

The root .env supports literal assignments, optional quotes and comments. No
interpolation, command execution, dependency installation or process-env mutation.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from aegis.config import PROJECT_ROOT

_NAMES = frozenset(
    {
        "UPSTOX_ACCESS_TOKEN",
        "ZERODHA_API_KEY",
        "ZERODHA_API_SECRET",
        "AEGIS_MODE",
        "AEGIS_ENABLE_LIVE_ORDERS",
    }
)


class BrokerCredentialError(ValueError):
    """A controlled reason code, never a credential or source line."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _dotenv(path: Path) -> dict[str, str]:
    try:
        if not path.exists():
            return {}
        if not path.is_file() or path.stat().st_size > 65536:
            raise BrokerCredentialError("INVALID_LOCAL_CREDENTIAL_FILE")
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        raise BrokerCredentialError("LOCAL_CREDENTIAL_FILE_UNREADABLE") from None
    values: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.removeprefix("export ").partition("=")
        name, value = name.strip(), value.strip()
        if name not in _NAMES:
            continue
        if not separator or name in values:
            raise BrokerCredentialError("INVALID_LOCAL_CREDENTIAL_FILE")
        if value.startswith(("'", '"')):
            quote = value[0]
            end = value.find(quote, 1)
            if end < 0 or (
                value[end + 1 :].strip() and not value[end + 1 :].lstrip().startswith("#")
            ):
                raise BrokerCredentialError("INVALID_LOCAL_CREDENTIAL_FILE")
            value = value[1:end]
        else:
            value = value.split(" #", 1)[0].rstrip()
        values[name] = value
    return values


def _secret(values: Mapping[str, str], name: str) -> str | None:
    value = values.get(name, "")
    if not value:
        return None
    if len(value) > 8192 or any(ord(c) < 33 or ord(c) > 126 for c in value):
        raise BrokerCredentialError("INVALID_BROKER_CREDENTIAL_FORMAT")
    return value


@dataclass(frozen=True, slots=True)
class BrokerCredentials:
    """Optional broker inputs with redacted repr; availability is not API validity.

    Upstox needs only its access token. Zerodha's key/secret initialize local
    configuration, never a live session. No Zerodha client or token exchange is
    implemented here; official Kite authentication and separate activation are
    prerequisites for any future Zerodha connectivity.
    """

    upstox_access_token: str | None = field(default=None, repr=False)
    zerodha_api_key: str | None = field(default=None, repr=False)
    zerodha_api_secret: str | None = field(default=None, repr=False)

    @classmethod
    def from_env(
        cls,
        *,
        env_file: Path | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> BrokerCredentials:
        """Read root .env and explicit process overrides; never persist the result."""
        values = _dotenv(env_file if env_file is not None else PROJECT_ROOT / ".env")
        environment = os.environ if environ is None else environ
        values.update({key: environment[key] for key in _NAMES if key in environment})
        if values.get("AEGIS_MODE", "RESEARCH_ONLY").strip().upper() != "RESEARCH_ONLY":
            raise BrokerCredentialError("RESEARCH_ONLY_REQUIRED")
        if values.get("AEGIS_ENABLE_LIVE_ORDERS", "false").strip().lower() not in {
            "false",
            "0",
            "no",
            "off",
            "",
        }:
            raise BrokerCredentialError("LIVE_BROKER_ORDERS_DISABLED_REQUIRED")
        return cls(
            _secret(values, "UPSTOX_ACCESS_TOKEN"),
            _secret(values, "ZERODHA_API_KEY"),
            _secret(values, "ZERODHA_API_SECRET"),
        )

    @property
    def zerodha_configuration_ready(self) -> bool:
        return bool(self.zerodha_api_key and self.zerodha_api_secret)

    @property
    def zerodha_connectivity_enabled(self) -> bool:
        """Key/secret configuration and arbitrary env tokens cannot activate a client."""
        return False

    @property
    def zerodha_connectivity_blocker(self) -> str:
        return "OFFICIAL_KITE_AUTHENTICATION_REQUIRED"
