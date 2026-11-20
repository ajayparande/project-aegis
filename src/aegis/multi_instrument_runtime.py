"""Private bounded synthetic coordination over the authenticated forward runtime."""

import json
from contextlib import closing
from pathlib import Path

from aegis.database import get_readonly_connection
from aegis.forward_runtime import ForwardRuntimeError, ForwardRuntimeService, Quote, QuoteResult
from aegis.instruments import get_instrument
from aegis.market_quality import CalendarRegistry


class _MultiInstrumentCoordinator:
    """Resolve a fixed registry set; delegate all ordering, aggregation and recovery."""

    def __init__(self, runtime: ForwardRuntimeService, symbols: tuple[str, ...]) -> None:
        if type(runtime) is not ForwardRuntimeService:
            raise ForwardRuntimeError("A persisted forward runtime service is required.")
        if not symbols or len(set(symbols)) != len(symbols):
            raise ForwardRuntimeError("Instrument symbols must be unique and nonempty.")
        self._validate_capacity(runtime.max_candidates, len(runtime.instrument_ids))
        resolved = [get_instrument(runtime.database_path, "NSE", symbol) for symbol in symbols]
        if any(
            item is None or not item.is_active or item.instrument_type != "EQUITY"
            for item in resolved
        ):
            raise ForwardRuntimeError("Active NSE cash-equity registry identities are required.")
        ids = tuple(sorted(item.id for item in resolved if item is not None))
        if ids != runtime.instrument_ids or not runtime.scheduling_only:
            raise ForwardRuntimeError("Fixed instrument set or synthetic scheduling mode mismatch.")
        self._runtime = runtime
        self.symbols = symbols

    @classmethod
    def open(
        cls, database: Path, runtime_id: str, calendars: CalendarRegistry, symbols: tuple[str, ...]
    ) -> "_MultiInstrumentCoordinator":
        """Authenticate persisted cursors and recover scheduler evidence without routing."""
        # Runtime.open performs recovery writes; reject capped configurations first.
        with closing(get_readonly_connection(database)) as connection:
            row = connection.execute(
                "SELECT config_json FROM forward_runtimes WHERE runtime_id = ?", (runtime_id,)
            ).fetchone()
        if row is None:
            raise ForwardRuntimeError("Forward runtime does not exist.")
        config = json.loads(row["config_json"])
        cls._validate_capacity(config["max_candidates"], len(config["instrument_ids"]))
        return cls(ForwardRuntimeService.open(database, runtime_id, calendars), symbols)

    @staticmethod
    def _validate_capacity(max_candidates: int, instrument_count: int) -> None:
        if max_candidates < instrument_count:
            raise ForwardRuntimeError(
                "M5-A requires max_candidates >= number of configured instruments."
            )

    def process(self, quotes: tuple[Quote, ...]) -> tuple[QuoteResult, ...]:
        """Process at most 1000 observations in supplied causal delivery order.

        Callers interleave by timestamp and stable instrument identity. Never sort
        away late deliveries: the existing per-instrument validator must reject them.
        """
        if len(quotes) > 1000:
            raise ForwardRuntimeError("Synthetic batch exceeds 1000 observations.")
        if any(
            q.instrument_id not in self._runtime.instrument_ids
            or q.observation_evidence is not None
            for q in quotes
        ):
            raise ForwardRuntimeError("Only configured synthetic observations are allowed.")
        return tuple(self._runtime.process_quote(quote) for quote in quotes)
