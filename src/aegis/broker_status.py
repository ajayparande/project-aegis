"""Offline broker readiness: local availability only, never remote token validity."""

import json
from importlib import import_module

from aegis.broker_credentials import BrokerCredentialError, BrokerCredentials


def broker_status() -> dict[str, str]:
    """Return only fixed status labels; do not initialize clients or databases."""
    credentials = BrokerCredentials.from_env()
    try:
        adapter = import_module("aegis.providers.upstox_market_stream")
        upstox_available = callable(getattr(adapter, "UpstoxMarketStreamAdapter", None))
    except ImportError:
        upstox_available = False
    return {
        "upstox_access_token": "PRESENT" if credentials.upstox_access_token else "MISSING",
        "zerodha_api_key": "PRESENT" if credentials.zerodha_api_key else "MISSING",
        "zerodha_api_secret": "PRESENT" if credentials.zerodha_api_secret else "MISSING",
        "upstox_market_data_adapter": "AVAILABLE" if upstox_available else "UNAVAILABLE",
        "zerodha_market_data_adapter": "NOT_IMPLEMENTED",
        "aegis_mode": "RESEARCH_ONLY",
        "live_broker_orders": "DISABLED",
    }


def main() -> int:
    """Print sanitized local configuration status without prompts or network calls."""
    try:
        result = broker_status()
    except BrokerCredentialError as error:
        print(json.dumps({"error": error.code}))
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
