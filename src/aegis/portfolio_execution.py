"""Private M2-C composition of qualified portfolio sleeves through the M1B engine."""

from __future__ import annotations

import json
import os
import re
import threading
import weakref
from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields
from datetime import UTC, date, datetime
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
from pathlib import Path
from types import MappingProxyType
from typing import Final, Literal
from zoneinfo import ZoneInfo

from aegis import backtesting, market_quality, portfolio_qualification, strategy_lab
from aegis.config import get_settings
from aegis.market_quality import CalendarRegistry, ExchangeCalendar
from aegis.portfolio import PortfolioRequest
from aegis.portfolio_qualification import (
    _PortfolioQualification,
    _QualifiedBatch,
    _QualifiedSleeve,
    _UniverseMember,
)

_SHA256_RE: Final = re.compile(r"[0-9a-fA-F]{64}\Z")
_SOURCE_IMPORT_FIELDS: Final = frozenset(
    {
        "import_id",
        "file_sha256",
        "instrument_id",
        "exchange",
        "trading_symbol",
        "interval",
        "import_status",
        "source_use_status",
        "declaration_id",
        "declaration_import_id",
        "declaration_previous_status",
        "declaration_new_status",
        "declared_by",
        "rationale",
        "declared_at",
    }
)
_FEE_FIELDS: Final = frozenset(
    {
        "brokerage",
        "exchange_charges",
        "regulatory_charges",
        "transaction_tax",
        "stamp_duty",
        "gst",
    }
)
_COST_FIELDS: Final = frozenset(field.name for field in fields(backtesting.CostAssumptions))
_ORDER_BASE_FIELDS: Final = frozenset(
    {
        "order_id",
        "order_type",
        "side",
        "signal",
        "signal_candle_timestamp",
        "submitted_at",
    }
)
_ORDER_EXECUTION_FIELDS: Final = frozenset(
    {
        "execution_bar_timestamp",
        "execution_time",
        "reference_open",
        "quantity",
        "prior_bar_volume",
        "volume_capacity",
    }
)
_ORDER_PRICE_FIELDS: Final = frozenset(
    {
        "fill_price",
        "fees",
        "total_fees",
        "spread_cost",
        "slippage_cost",
        "tick_rounding_cost",
    }
)
_FILL_FIELDS: Final = frozenset(
    {
        "order_id",
        "execution_bar_timestamp",
        "execution_time",
        "side",
        "quantity",
        "fill_price",
        "fees",
        "total_fees",
        "cash_before",
        "cash_after",
        "position_quantity_before",
        "position_quantity_after",
    }
)
_TRADE_FIELDS: Final = frozenset(
    {
        "trade_id",
        "entry_order_id",
        "exit_order_id",
        "entry_time",
        "exit_time",
        "quantity",
        "entry_price",
        "exit_price",
        "gross_pnl",
        "entry_fees",
        "exit_fees",
        "net_pnl",
    }
)
_EQUITY_FIELDS: Final = frozenset(
    {"timestamp", "local_date", "cash", "position_quantity", "mark_price", "equity"}
)
_OPEN_POSITION_FIELDS: Final = frozenset(
    {
        "quantity",
        "entry_price",
        "entry_time",
        "entry_fees",
        "mark_price",
        "unrealized_gross_pnl",
        "unrealized_net_pnl",
        "estimated_liquidation_costs",
        "net_liquidation_value",
    }
)
_SUMMARY_DECIMAL_FIELDS: Final = frozenset(
    {
        "starting_cash",
        "ending_cash",
        "ending_equity",
        "net_liquidation_value",
        "absolute_pnl",
        "net_liquidation_pnl",
        "total_return",
        "net_liquidation_return",
        "realized_net_pnl",
        "unrealized_gross_pnl",
        "unrealized_net_pnl",
        "estimated_liquidation_costs",
        "total_fees",
        "filled_turnover",
        "maximum_drawdown",
        "gross_profit",
        "gross_loss",
        "net_profit_from_winning_trades",
        "net_loss_from_losing_trades",
    }
)
_SUMMARY_COUNT_FIELDS: Final = frozenset(
    {"open_position_quantity", "closed_trade_count", "winning_trades", "losing_trades"}
)
_SUMMARY_NULLABLE_DECIMAL_FIELDS: Final = frozenset(
    {
        "win_rate",
        "profit_factor",
        "expectancy_per_closed_trade",
        "annualized_daily_volatility",
        "annualized_sharpe_zero_risk_free",
    }
)
_SUMMARY_FIELDS: Final = (
    _SUMMARY_DECIMAL_FIELDS
    | _SUMMARY_COUNT_FIELDS
    | _SUMMARY_NULLABLE_DECIMAL_FIELDS
    | {"open_position", "daily_returns"}
)
_DIAGNOSTIC_FIELDS: Final = frozenset(
    {
        "instrument",
        "interval",
        "execution_configuration",
        "signal_provenance",
        "strategy_implementation_sha256",
        "signals_sha256",
        "dataset_sha256",
        "candle_source_bindings_sha256",
        "source_file_sha256s",
        "source_imports_sha256",
        "calendar_configuration_sha256",
        "effective_calendar_configuration_sha256",
        "expected_schedule_sha256",
        "quality_report_sha256",
        "data_quality",
        "source_imports",
    }
)
_INSTRUMENT_FIELDS: Final = frozenset(
    {
        "instrument_id",
        "exchange",
        "trading_symbol",
        "segment",
        "instrument_type",
        "lot_size",
        "tick_size_decimal",
    }
)
_DATA_QUALITY_FIELDS: Final = frozenset(
    {
        "status",
        "calendar_status",
        "calendar_confidence",
        "staleness_status",
        "completeness_percentage",
        "expected_candles",
        "actual_candles",
        "as_of",
    }
)
_BENCHMARK_COMPLETED_FIELDS: Final = frozenset(
    {
        "status",
        "method",
        "quantity",
        "entry_price",
        "exit_price",
        "fees",
        "ending_cash",
        "total_return",
        "equity_curve",
    }
)


class _PortfolioExecutionIntegrityError(RuntimeError):
    """Raised when a registered M2-B batch or M1B result fails M2-C validation."""


@dataclass(frozen=True, slots=True)
class _ExecutionPlan:
    member: _UniverseMember
    qualified_sleeve: _QualifiedSleeve
    request: backtesting._ExecutionRequest
    calendar: ExchangeCalendar
    signals_sha256: str
    candles_sha256: str
    source_imports_sha256: str


@dataclass(frozen=True, slots=True)
class _PortfolioSleeveExecution:
    """Detached M1B snapshot; its hashes bind integrity, not authenticity."""

    sequence: int
    exchange: str
    trading_symbol: str
    result: backtesting.ExecutionResult
    result_sha256: str
    provenance_json: str
    provenance_sha256: str

    def __post_init__(self) -> None:
        if type(self.sequence) is not int or self.sequence < 0:
            raise TypeError("Sleeve execution sequence must be a non-negative integer.")
        if type(self.exchange) is not str or type(self.trading_symbol) is not str:
            raise TypeError("Sleeve execution identity must be text.")
        if type(self.result) is not backtesting.ExecutionResult:
            raise TypeError("Sleeve execution must contain a detached M1B ExecutionResult.")
        _require_hash(self.result_sha256, "result_sha256")
        _require_hash(self.provenance_sha256, "provenance_sha256")
        try:
            provenance = json.loads(self.provenance_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise _PortfolioExecutionIntegrityError(
                "Sleeve execution provenance must be canonical JSON."
            ) from exc
        if (
            type(provenance) is not dict
            or strategy_lab._canonical_json(provenance) != self.provenance_json
            or strategy_lab._canonical_hash(provenance) != self.provenance_sha256
        ):
            raise _PortfolioExecutionIntegrityError(
                "Sleeve execution provenance fingerprint does not match its document."
            )
        if _execution_result_sha256(self.result) != self.result_sha256:
            raise _PortfolioExecutionIntegrityError(
                "Sleeve execution result fingerprint does not match its snapshot."
            )

    def to_dict(self) -> dict[str, object]:
        """Return a detached serialization without exposing stored mutable containers."""

        provenance = json.loads(self.provenance_json)
        if type(provenance) is not dict:
            raise _PortfolioExecutionIntegrityError("Sleeve provenance must be an object.")
        return {
            "sequence": self.sequence,
            "exchange": self.exchange,
            "trading_symbol": self.trading_symbol,
            "result_sha256": self.result_sha256,
            "provenance_sha256": self.provenance_sha256,
            "provenance": provenance,
            "execution_result": _execution_result_document(self.result),
        }


@dataclass(frozen=True, slots=True, weakref_slot=True)
class _PortfolioSleeveExecutionBatch:
    """Complete immutable M2-C result; it intentionally contains no portfolio totals."""

    batch_fingerprint_sha256: str
    request_sha256: str
    manifest_sha256: str
    calendar_configuration_sha256: str
    calendar_registry_sha256: str
    implementation_fingerprints: tuple[tuple[str, str], ...]
    sleeves: tuple[_PortfolioSleeveExecution, ...]

    def __post_init__(self) -> None:
        for label in (
            "batch_fingerprint_sha256",
            "request_sha256",
            "manifest_sha256",
            "calendar_configuration_sha256",
            "calendar_registry_sha256",
        ):
            _require_hash(getattr(self, label), label)
        if (
            type(self.implementation_fingerprints) is not tuple
            or not self.implementation_fingerprints
        ):
            raise TypeError("Implementation fingerprints must be a non-empty immutable tuple.")
        for name, fingerprint in self.implementation_fingerprints:
            if type(name) is not str or not name:
                raise TypeError("Implementation fingerprint names must be non-empty text.")
            _require_hash(fingerprint, f"implementation_fingerprints[{name}]")
        if type(self.sleeves) is not tuple or not self.sleeves:
            raise TypeError("A published M2-C batch must contain every sleeve.")
        if any(type(item) is not _PortfolioSleeveExecution for item in self.sleeves):
            raise TypeError("M2-C batches may contain only immutable sleeve executions.")
        if tuple(item.sequence for item in self.sleeves) != tuple(range(len(self.sleeves))):
            raise _PortfolioExecutionIntegrityError(
                "Sleeve execution order does not match the frozen manifest."
            )

    def to_dict(self) -> dict[str, object]:
        """Return an immutable-result serialization with fresh mutable containers."""

        return {
            "batch_fingerprint_sha256": self.batch_fingerprint_sha256,
            "request_sha256": self.request_sha256,
            "manifest_sha256": self.manifest_sha256,
            "calendar_configuration_sha256": self.calendar_configuration_sha256,
            "calendar_registry_sha256": self.calendar_registry_sha256,
            "implementation_fingerprints": dict(self.implementation_fingerprints),
            "sleeves": [sleeve.to_dict() for sleeve in self.sleeves],
        }


@dataclass(frozen=True, slots=True)
class _PublishedExecutionEvidence:
    """Process-local handoff for pure accounting, retained only while the batch lives."""

    reference: weakref.ReferenceType[_PortfolioSleeveExecutionBatch]
    source_batch_sha256: str
    qualification_provenance_json: str
    sleeves: tuple[_PortfolioSleeveExecution, ...]
    results: tuple[backtesting.ExecutionResult, ...]
    implementation_fingerprints: tuple[tuple[str, str], ...]


_PUBLISHED_EXECUTION_BATCHES: dict[int, _PublishedExecutionEvidence] = {}
_PUBLISHED_EXECUTION_LOCK = threading.RLock()


def _read_execution_batch_evidence(batch: _PortfolioSleeveExecutionBatch) -> tuple[str, str]:
    """Validate the exact published M2-C object without I/O or repeating execution.

    Like M2-B registration, this is a supported-call boundary, not a sandbox against
    arbitrary Python in this process. Hashes record the evidence validated at M2-C
    publication; accounting does not re-read source files or current market data.
    """

    if type(batch) is not _PortfolioSleeveExecutionBatch:
        raise _PortfolioExecutionIntegrityError(
            "Accounting requires an exact M2-C execution batch."
        )
    with _PUBLISHED_EXECUTION_LOCK:
        evidence = _PUBLISHED_EXECUTION_BATCHES.get(id(batch))
        if evidence is None or evidence.reference() is not batch:
            raise _PortfolioExecutionIntegrityError(
                "Accounting requires the exact coordinator-published M2-C batch."
            )
    batch.__post_init__()
    if batch.implementation_fingerprints != evidence.implementation_fingerprints or any(
        type(pair) is not tuple or len(pair) != 2 for pair in batch.implementation_fingerprints
    ):
        raise _PortfolioExecutionIntegrityError(
            "Published implementation fingerprint entries changed."
        )
    if len(batch.sleeves) != len(evidence.sleeves) or any(
        actual is not original or actual.result is not result
        for actual, original, result in zip(
            batch.sleeves, evidence.sleeves, evidence.results, strict=True
        )
    ):
        raise _PortfolioExecutionIntegrityError("Published M2-C sleeve evidence was replaced.")
    for sleeve in batch.sleeves:
        sleeve.__post_init__()
        for result_field in fields(backtesting.ExecutionResult):
            _require_immutable_execution_evidence(getattr(sleeve.result, result_field.name))
    if strategy_lab._canonical_hash(batch.to_dict()) != evidence.source_batch_sha256:
        raise _PortfolioExecutionIntegrityError("Published M2-C batch fingerprint changed.")
    return evidence.source_batch_sha256, evidence.qualification_provenance_json


def _require_immutable_execution_evidence(value: object) -> None:
    """Reject mutable replacements even when their serialized values are unchanged."""

    if type(value) is MappingProxyType:
        for item in value.values():
            _require_immutable_execution_evidence(item)
    elif type(value) is tuple:
        for item in value:
            _require_immutable_execution_evidence(item)
    elif type(value) not in (str, int, bool, type(None)):
        raise _PortfolioExecutionIntegrityError(
            "Published execution evidence must remain immutable."
        )


def _qualify_and_execute_portfolio_request(
    database_path: Path, request: PortfolioRequest
) -> _PortfolioQualification | _PortfolioSleeveExecutionBatch:
    """Qualify through M2-B and privately execute its exact registered batch through M1B."""

    qualification = portfolio_qualification._qualify_portfolio_request(database_path, request)
    if type(qualification) is not _PortfolioQualification:
        raise _PortfolioExecutionIntegrityError(
            "M2-B returned an unsupported portfolio qualification outcome."
        )
    if qualification.status == "BLOCKED":
        return qualification
    if qualification.status != "QUALIFIED" or qualification.qualified_batch is None:
        raise _PortfolioExecutionIntegrityError(
            "M2-B returned an invalid qualified portfolio outcome."
        )
    batch = qualification.qualified_batch
    _validate_registered_batch(batch)
    return _execute_registered_batch(batch)


def _execute_registered_batch(batch: _QualifiedBatch) -> _PortfolioSleeveExecutionBatch:
    """Execute every member of the exact registered batch before publishing any result."""

    registered_batch = _validate_registered_batch(batch)
    provenance = registered_batch.provenance_document()
    approved_calendar_path = _capture_approved_calendar_path(provenance)
    m2b_fingerprints = _implementation_fingerprints_from_batch(provenance)
    m2c_fingerprint = _portfolio_execution_fingerprint()
    registry = _load_bound_calendar_registry(provenance, approved_calendar_path)
    plans = _prepare_execution_plans(
        registered_batch,
        provenance,
        registry,
        approved_calendar_path,
        m2c_fingerprint,
    )

    if _portfolio_execution_fingerprint() != m2c_fingerprint:
        raise _PortfolioExecutionIntegrityError(
            "M2-C implementation changed while execution inputs were prepared."
        )

    completed: list[_PortfolioSleeveExecution] = []
    for plan in plans:
        raw_result = backtesting._execute_qualified_request(
            plan.request,
            qualification_token=backtesting._QUALIFICATION_TOKEN,
        )
        detached_result, result_sha256 = _snapshot_execution_result(raw_result, plan)
        result_provenance = _sleeve_execution_provenance(
            registered_batch,
            provenance,
            registry,
            plan,
            result_sha256,
            m2b_fingerprints,
            m2c_fingerprint,
        )
        provenance_json = strategy_lab._canonical_json(result_provenance)
        completed.append(
            _PortfolioSleeveExecution(
                sequence=plan.member.sequence,
                exchange=plan.member.exchange,
                trading_symbol=plan.member.trading_symbol,
                result=detached_result,
                result_sha256=result_sha256,
                provenance_json=provenance_json,
                provenance_sha256=strategy_lab._canonical_hash(result_provenance),
            )
        )

    _validate_registered_batch(registered_batch)
    final_provenance = registered_batch.provenance_document()
    if _implementation_fingerprints_from_batch(final_provenance) != m2b_fingerprints:
        raise _PortfolioExecutionIntegrityError(
            "M2-B implementation provenance changed before M2-C publication."
        )
    _verify_calendar_unchanged(registered_batch, registry, provenance, approved_calendar_path)
    final_m2c_fingerprint = _portfolio_execution_fingerprint()
    if final_m2c_fingerprint != m2c_fingerprint:
        raise _PortfolioExecutionIntegrityError(
            "M2-C implementation changed before portfolio result publication."
        )

    request_sha256 = strategy_lab._canonical_hash(registered_batch.request.to_dict())
    published = _PortfolioSleeveExecutionBatch(
        batch_fingerprint_sha256=registered_batch.fingerprint_sha256,
        request_sha256=request_sha256,
        manifest_sha256=registered_batch.manifest.manifest_sha256,
        calendar_configuration_sha256=registry.configuration_sha256 or "",
        calendar_registry_sha256=_require_hash(
            _require_mapping(provenance.get("calendar_registry"), "batch.calendar_registry").get(
                "registry_sha256"
            ),
            "batch.calendar_registry.registry_sha256",
        ),
        implementation_fingerprints=tuple(
            sorted((*m2b_fingerprints.items(), ("portfolio_execution", m2c_fingerprint)))
        ),
        sleeves=tuple(completed),
    )
    batch_id = id(published)

    def remove_expired(reference: weakref.ReferenceType[_PortfolioSleeveExecutionBatch]) -> None:
        with _PUBLISHED_EXECUTION_LOCK:
            entry = _PUBLISHED_EXECUTION_BATCHES.get(batch_id)
            if entry is not None and entry.reference is reference:
                del _PUBLISHED_EXECUTION_BATCHES[batch_id]

    evidence = _PublishedExecutionEvidence(
        reference=weakref.ref(published, remove_expired),
        source_batch_sha256=strategy_lab._canonical_hash(published.to_dict()),
        qualification_provenance_json=registered_batch.provenance_json,
        sleeves=published.sleeves,
        results=tuple(sleeve.result for sleeve in published.sleeves),
        implementation_fingerprints=published.implementation_fingerprints,
    )
    with _PUBLISHED_EXECUTION_LOCK:
        _PUBLISHED_EXECUTION_BATCHES[batch_id] = evidence
    return published


def _prepare_execution_plans(
    batch: _QualifiedBatch,
    provenance: Mapping[str, object],
    registry: CalendarRegistry,
    approved_calendar_path: Path,
    expected_m2c_fingerprint: str,
) -> tuple[_ExecutionPlan, ...]:
    request = batch.request
    validated_sleeves = tuple(
        (
            member,
            sleeve,
            _validate_sleeve_calendar(batch, provenance, registry, member, sleeve),
        )
        for member, sleeve in zip(batch.manifest.members, batch.sleeves, strict=True)
    )
    _validate_registered_batch(batch)
    if _portfolio_execution_fingerprint() != expected_m2c_fingerprint:
        raise _PortfolioExecutionIntegrityError(
            "M2-C implementation changed before signal generation."
        )
    _verify_calendar_unchanged(batch, registry, provenance, approved_calendar_path)
    plans: list[_ExecutionPlan] = []
    for member, sleeve, calendar in validated_sleeves:
        imports = _adapt_source_imports(sleeve.source_imports, member)
        strategy_hash = strategy_lab._canonical_hash(sleeve.strategy_spec.to_dict())
        if strategy_hash != member.strategy_spec_sha256:
            raise _PortfolioExecutionIntegrityError(
                "Qualified strategy does not match its frozen manifest fingerprint."
            )
        candle_document = _candle_document(sleeve.candles)
        candles_sha256 = strategy_lab._canonical_hash(candle_document)
        signal_values = backtesting.generate_sma_crossover_signals(
            sleeve.candles,
            fast_window=sleeve.strategy_spec.fast_window,
            slow_window=sleeve.strategy_spec.slow_window,
        )
        signal_sha256 = _signals_sha256(signal_values)
        config = backtesting.BacktestConfig(
            starting_cash=sleeve.starting_cash,
            order_quantity=member.order_quantity,
            max_volume_participation=request.max_volume_participation,
            costs=request.costs,
        )
        instrument = backtesting._ExecutionInstrument(
            exchange=member.exchange,
            trading_symbol=member.trading_symbol,
            segment=member.segment,
            instrument_type=member.instrument_type,
            is_active=member.active,
            lot_size=member.lot_size,
            tick_size_decimal=Decimal(member.tick_size_decimal),
            tick_size_is_authoritative=True,
            instrument_id=member.instrument_id,
        )
        signal_provenance = backtesting._SignalProvenance(
            strategy_key=sleeve.strategy_spec.strategy_key,
            name=sleeve.strategy_spec.name,
            version=sleeve.strategy_spec.version,
            parameters=(
                ("fast_window", sleeve.strategy_spec.fast_window),
                ("slow_window", sleeve.strategy_spec.slow_window),
            ),
            strategy_spec_sha256=strategy_hash,
        )
        expected_by_timestamp = dict(sleeve.expected_by_timestamp)
        m1b_request = backtesting._ExecutionRequest(
            candles=sleeve.candles,
            signals=signal_values,
            signal_provenance=signal_provenance,
            instrument=instrument,
            interval=sleeve.strategy_spec.interval,
            config=config,
            calendar=calendar,
            expected_by_timestamp=MappingProxyType(expected_by_timestamp),
            eligibility=backtesting._ExecutionEligibility(
                quality_report=sleeve.quality_report,
                source_imports=imports,
            ),
            dataset_sha256=sleeve.dataset_sha256,
            candle_source_bindings_sha256=sleeve.candle_source_bindings_sha256,
            source_file_sha256s=sleeve.source_file_sha256s,
            calendar_configuration_sha256=registry.configuration_sha256,
            effective_calendar_configuration_sha256=(
                sleeve.effective_calendar_configuration_sha256
            ),
            expected_schedule_sha256=sleeve.expected_schedule_sha256,
        )
        plans.append(
            _ExecutionPlan(
                member=member,
                qualified_sleeve=sleeve,
                request=m1b_request,
                calendar=calendar,
                signals_sha256=signal_sha256,
                candles_sha256=candles_sha256,
                source_imports_sha256=strategy_lab._canonical_hash(list(imports)),
            )
        )
    return tuple(plans)


def _validate_registered_batch(batch: _QualifiedBatch) -> _QualifiedBatch:
    registered = portfolio_qualification._validate_coordinator_batch(batch)
    provenance = registered.provenance_document()
    if (
        strategy_lab._canonical_json(provenance) != registered.provenance_json
        or strategy_lab._canonical_hash(provenance) != registered.fingerprint_sha256
        or provenance.get("request") != registered.request.to_dict()
        or provenance.get("allocation") != registered.allocation.to_dict()
        or provenance.get("universe_manifest") != registered.manifest.to_dict()
        or provenance.get("sleeves")
        != [
            portfolio_qualification._qualified_sleeve_provenance(sleeve)
            for sleeve in registered.sleeves
        ]
    ):
        raise _PortfolioExecutionIntegrityError(
            "Exact registered batch contents no longer match their provenance fingerprint."
        )
    _validate_result_tree(provenance, "batch.provenance")
    return registered


def _capture_approved_calendar_path(provenance: Mapping[str, object]) -> Path:
    settings_path = get_settings().calendar_config_path
    if ".." in settings_path.parts:
        raise _PortfolioExecutionIntegrityError(
            "Application calendar configuration path traversal is not permitted."
        )
    try:
        approved_path = settings_path.resolve(strict=True)
    except OSError as exc:
        raise _PortfolioExecutionIntegrityError(
            "The application-approved calendar configuration is unavailable."
        ) from exc
    if os.path.normcase(os.path.abspath(os.fspath(approved_path))) != os.path.normcase(
        os.path.abspath(os.fspath(settings_path))
    ):
        raise _PortfolioExecutionIntegrityError(
            "The application-approved calendar configuration must not escape through a symlink."
        )
    if not approved_path.is_file():
        raise _PortfolioExecutionIntegrityError(
            "The application-approved calendar configuration is not a file."
        )

    calendar_provenance = provenance.get("calendar_registry")
    calendar_data = _require_mapping(calendar_provenance, "batch.calendar_registry")
    _require_keys(
        calendar_data,
        {"configuration_sha256", "registry_sha256", "calendar_path"},
        "batch.calendar_registry",
    )
    recorded_path = calendar_data["calendar_path"]
    if type(recorded_path) is not str:
        raise _PortfolioExecutionIntegrityError("Batch calendar path must be text.")
    try:
        provenance_path = Path(recorded_path).resolve(strict=True)
    except OSError as exc:
        raise _PortfolioExecutionIntegrityError(
            "The qualified batch calendar path is unavailable."
        ) from exc
    if provenance_path != approved_path:
        raise _PortfolioExecutionIntegrityError(
            "Qualified batch calendar path is not the application-approved calendar."
        )
    return approved_path


def _load_bound_calendar_registry(
    provenance: Mapping[str, object], approved_path: Path
) -> CalendarRegistry:
    calendar_data = _require_mapping(provenance.get("calendar_registry"), "batch.calendar_registry")
    try:
        registry = market_quality.load_calendar_registry(approved_path)
    except market_quality.CalendarConfigError as exc:
        raise backtesting.BacktestError(f"Calendar configuration is invalid: {exc}") from exc
    registry_hash = strategy_lab._canonical_hash(
        portfolio_qualification._registry_document(registry)
    )
    if (
        registry.configuration_path != approved_path
        or not registry.calendars
        or registry.configuration_sha256 is None
        or not _is_sha256(registry.configuration_sha256)
        or registry.configuration_sha256 != calendar_data["configuration_sha256"]
        or registry_hash != calendar_data["registry_sha256"]
    ):
        raise _PortfolioExecutionIntegrityError(
            "Approved calendar bytes or registry content differ from M2-B provenance."
        )
    return registry


def _verify_calendar_unchanged(
    batch: _QualifiedBatch,
    original_registry: CalendarRegistry,
    provenance: Mapping[str, object],
    approved_path: Path,
) -> None:
    calendar_data = _require_mapping(provenance.get("calendar_registry"), "batch.calendar_registry")
    try:
        reloaded = market_quality.load_calendar_registry(approved_path)
    except market_quality.CalendarConfigError as exc:
        raise backtesting.BacktestError(str(exc)) from exc
    if (
        original_registry.configuration_path != approved_path
        or reloaded.configuration_path != approved_path
    ):
        raise _PortfolioExecutionIntegrityError("Calendar provenance is missing its path binding.")
    if (
        reloaded.configuration_sha256 != original_registry.configuration_sha256
        or reloaded.configuration_sha256 != calendar_data["configuration_sha256"]
    ):
        raise backtesting.BacktestError("Calendar configuration changed after it was loaded.")
    registry_hash = strategy_lab._canonical_hash(
        portfolio_qualification._registry_document(reloaded)
    )
    if registry_hash != calendar_data["registry_sha256"]:
        raise _PortfolioExecutionIntegrityError(
            "Canonical calendar registry differs from M2-B provenance."
        )
    for member, sleeve in zip(batch.manifest.members, batch.sleeves, strict=True):
        _validate_sleeve_calendar(batch, provenance, reloaded, member, sleeve)


def _validate_sleeve_calendar(
    batch: _QualifiedBatch,
    provenance: Mapping[str, object],
    registry: CalendarRegistry,
    member: _UniverseMember,
    sleeve: _QualifiedSleeve,
) -> ExchangeCalendar:
    if sleeve.member != member:
        raise _PortfolioExecutionIntegrityError("Qualified sleeve identity changed.")
    request_sleeve = batch.request.sleeves[member.sequence]
    report = sleeve.quality_report
    if (
        report.exchange.strip().upper() != member.exchange
        or report.trading_symbol.strip().upper() != member.trading_symbol
        or report.segment != member.segment
        or report.interval != request_sleeve.strategy.interval
        or report.requested_start != batch.request.start_date
        or report.requested_end != batch.request.end_date
        or report.as_of != batch.request.as_of
        or report.calendar_status != "FULL"
        or report.calendar_confidence != "CONFIRMED"
    ):
        raise _PortfolioExecutionIntegrityError(
            "Quality report identity or confirmed calendar status differs from the batch."
        )
    calendar = registry.resolve(member.exchange, member.segment)
    if calendar is None or calendar.segment not in (member.segment, "*"):
        raise _PortfolioExecutionIntegrityError(
            "Approved calendar no longer resolves the frozen instrument segment."
        )
    if (
        report.calendar_source != calendar.source
        or report.calendar_coverage_start != calendar.coverage_start
        or report.calendar_coverage_end != calendar.coverage_end
        or report.calendar_confidence != calendar.confidence
    ):
        raise _PortfolioExecutionIntegrityError(
            "Calendar mapping no longer matches the qualified quality report."
        )
    effective_hash = strategy_lab._effective_calendar_hash(calendar, registry)
    if effective_hash != sleeve.effective_calendar_configuration_sha256:
        raise _PortfolioExecutionIntegrityError(
            "Effective calendar configuration differs from qualified sleeve provenance."
        )
    scheduled, completed, _, uncovered = market_quality.calculate_expected_candles(
        calendar,
        request_sleeve.strategy.interval,
        batch.request.start_date,
        batch.request.end_date,
        as_of=batch.request.as_of,
    )
    if uncovered or len(scheduled) != len(completed):
        raise _PortfolioExecutionIntegrityError(
            "The approved calendar does not provide a complete confirmed execution schedule."
        )
    expected = {item.timestamp: item.completes_at for item in completed}
    original_pairs = sleeve.expected_by_timestamp
    if type(original_pairs) is not tuple:
        raise _PortfolioExecutionIntegrityError("Expected schedule must be immutable.")
    if any(type(item) is not tuple or len(item) != 2 for item in original_pairs):
        raise _PortfolioExecutionIntegrityError("Expected schedule contains malformed entries.")
    recorded = dict(original_pairs)
    if len(recorded) != len(original_pairs) or recorded != expected:
        raise _PortfolioExecutionIntegrityError(
            "Expected candle completion mapping differs from the approved calendar."
        )
    schedule_document = {
        timestamp: completion.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        for timestamp, completion in expected.items()
    }
    if strategy_lab._canonical_hash(schedule_document) != sleeve.expected_schedule_sha256:
        raise _PortfolioExecutionIntegrityError(
            "Expected schedule fingerprint differs from qualified sleeve provenance."
        )
    if set(expected) != {candle.timestamp for candle in sleeve.candles}:
        raise _PortfolioExecutionIntegrityError(
            "Qualified candles do not match every expected calendar timestamp."
        )
    _validate_batch_calendar_provenance(batch, provenance, registry)
    return calendar


def _validate_batch_calendar_provenance(
    batch: _QualifiedBatch,
    provenance: Mapping[str, object],
    registry: CalendarRegistry,
) -> None:
    calendar_data = _require_mapping(provenance.get("calendar_registry"), "batch.calendar_registry")
    if (
        registry.configuration_path is None
        or registry.configuration_path
        != Path(
            _require_text(calendar_data["calendar_path"], "batch.calendar_registry.calendar_path")
        )
        or registry.configuration_sha256 != calendar_data["configuration_sha256"]
        or strategy_lab._canonical_hash(portfolio_qualification._registry_document(registry))
        != calendar_data["registry_sha256"]
    ):
        raise _PortfolioExecutionIntegrityError(
            "Calendar registry does not match the exact M2-B batch provenance."
        )
    if batch.provenance_document().get("calendar_registry") != calendar_data:
        raise _PortfolioExecutionIntegrityError("Calendar provenance changed during validation.")


def _adapt_source_imports(
    source_imports: tuple[Mapping[str, object], ...], member: _UniverseMember
) -> tuple[dict[str, object], ...]:
    if type(source_imports) is not tuple or not source_imports:
        raise _PortfolioExecutionIntegrityError(
            "Qualified source imports must be a non-empty immutable tuple."
        )
    detached: list[dict[str, object]] = []
    for index, source_import in enumerate(source_imports):
        value = _require_mapping(source_import, f"source_imports[{index}]")
        _require_keys(value, _SOURCE_IMPORT_FIELDS, f"source_imports[{index}]")
        item = dict(value)
        _validate_source_import_record(item, member, index)
        detached.append(item)
    copied = tuple(detached)
    backtesting._validate_source_imports(copied)
    if tuple(dict(item) for item in source_imports) != copied:
        raise _PortfolioExecutionIntegrityError(
            "M1B source-import validation changed M2-B evidence."
        )
    return copied


def _validate_source_import_record(
    source_import: Mapping[str, object], member: _UniverseMember, index: int
) -> None:
    for name in (
        "import_id",
        "instrument_id",
        "declaration_id",
        "declaration_import_id",
    ):
        _require_int(source_import[name], f"source_imports[{index}].{name}", minimum=1)
    _require_hash(source_import["file_sha256"], f"source_imports[{index}].file_sha256")
    text_values: dict[str, str] = {}
    for name in _SOURCE_IMPORT_FIELDS - {
        "import_id",
        "instrument_id",
        "declaration_id",
        "declaration_import_id",
        "file_sha256",
    }:
        text_values[name] = _require_text(source_import[name], f"source_imports[{index}].{name}")
    if (
        text_values["exchange"].strip().upper() != member.exchange
        or text_values["trading_symbol"].strip().upper() != member.trading_symbol
        or text_values["interval"] != "1d"
        or text_values["source_use_status"] != "VERIFIED_PERMITTED"
        or text_values["declaration_new_status"] != "VERIFIED_PERMITTED"
        or text_values["import_status"] not in {"COMPLETED", "COMPLETED_WITH_ERRORS"}
        or text_values["declaration_previous_status"]
        not in {"VERIFIED_PERMITTED", "UNVERIFIED", "PROHIBITED"}
        or source_import["declaration_import_id"] != source_import["import_id"]
    ):
        raise _PortfolioExecutionIntegrityError(
            f"source_imports[{index}] does not match its qualified instrument and declaration."
        )


def _candle_document(candles: tuple[strategy_lab.StrategyCandle, ...]) -> list[dict[str, object]]:
    if type(candles) is not tuple or not candles:
        raise _PortfolioExecutionIntegrityError("Qualified candles must be a non-empty tuple.")
    document: list[dict[str, object]] = []
    previous_timestamp: datetime | None = None
    for index, candle in enumerate(candles):
        if type(candle) is not strategy_lab.StrategyCandle:
            raise _PortfolioExecutionIntegrityError("Qualified candle has an invalid type.")
        _require_datetime(candle.timestamp, f"candles[{index}].timestamp")
        parsed_timestamp = _parse_datetime(candle.timestamp, f"candles[{index}].timestamp")
        if previous_timestamp is not None and parsed_timestamp <= previous_timestamp:
            raise _PortfolioExecutionIntegrityError("Qualified candle timestamps are not ordered.")
        previous_timestamp = parsed_timestamp
        prices = {}
        for name in ("open", "high", "low", "close"):
            price = getattr(candle, name)
            if type(price) is not Decimal or not price.is_finite() or price <= 0:
                raise _PortfolioExecutionIntegrityError(
                    f"candles[{index}].{name} must be a finite positive Decimal."
                )
            prices[name] = backtesting._decimal_text(price)
        if type(candle.volume) is not int or candle.volume < 0:
            raise _PortfolioExecutionIntegrityError(
                f"candles[{index}].volume must be a non-negative integer."
            )
        if candle.high < max(candle.open, candle.low, candle.close) or candle.low > min(
            candle.open, candle.high, candle.close
        ):
            raise _PortfolioExecutionIntegrityError(f"candles[{index}] has invalid OHLC values.")
        document.append({"timestamp": candle.timestamp, **prices, "volume": candle.volume})
    return document


def _signals_sha256(signals: tuple[strategy_lab.StrategySignal, ...]) -> str:
    document: list[dict[str, str]] = []
    for index, signal in enumerate(signals):
        if type(signal) is not strategy_lab.StrategySignal:
            raise _PortfolioExecutionIntegrityError("Generated signal has an invalid type.")
        for name, value in (
            ("close", signal.close),
            ("fast_sma", signal.fast_sma),
            ("slow_sma", signal.slow_sma),
        ):
            if type(value) is not Decimal or not value.is_finite():
                raise _PortfolioExecutionIntegrityError(
                    f"signals[{index}].{name} must be a finite Decimal."
                )
        if signal.action not in {"ENTER_LONG", "EXIT_LONG"}:
            raise _PortfolioExecutionIntegrityError("Generated signal action is unsupported.")
        _require_datetime(signal.timestamp, f"signals[{index}].timestamp")
        document.append(
            {
                "timestamp": signal.timestamp,
                "action": signal.action,
                "close": backtesting._decimal_text(signal.close),
                "fast_sma": backtesting._decimal_text(signal.fast_sma),
                "slow_sma": backtesting._decimal_text(signal.slow_sma),
            }
        )
    return strategy_lab._canonical_hash(document)


def _implementation_fingerprints_from_batch(
    provenance: Mapping[str, object],
) -> dict[str, str]:
    values = _require_mapping(
        provenance.get("implementation_source_sha256"),
        "batch.implementation_source_sha256",
    )
    if not values:
        raise _PortfolioExecutionIntegrityError("Batch has no implementation fingerprints.")
    result: dict[str, str] = {}
    for name, fingerprint in values.items():
        if type(name) is not str or not name:
            raise _PortfolioExecutionIntegrityError("Implementation name must be non-empty text.")
        result[name] = _require_hash(fingerprint, f"implementation_source_sha256.{name}")
    return result


def _portfolio_execution_fingerprint() -> str:
    return strategy_lab._sha256_file(Path(__file__).resolve())


def _sleeve_execution_provenance(
    batch: _QualifiedBatch,
    batch_provenance: Mapping[str, object],
    registry: CalendarRegistry,
    plan: _ExecutionPlan,
    result_sha256: str,
    m2b_fingerprints: Mapping[str, str],
    m2c_fingerprint: str,
) -> dict[str, object]:
    request_document = batch.request.to_dict()
    calendar_document = _require_mapping(
        batch_provenance.get("calendar_registry"), "batch.calendar_registry"
    )
    report_hash = strategy_lab._canonical_hash(
        backtesting._evidence_json_value(asdict(plan.qualified_sleeve.quality_report))
    )
    return {
        "schema_version": 1,
        "batch_fingerprint_sha256": batch.fingerprint_sha256,
        "request_sha256": strategy_lab._canonical_hash(request_document),
        "allocation_sha256": strategy_lab._canonical_hash(batch.allocation.to_dict()),
        "manifest_sha256": batch.manifest.manifest_sha256,
        "manifest_member": plan.member.to_dict(),
        "strategy_spec": plan.qualified_sleeve.strategy_spec.to_dict(),
        "strategy_spec_sha256": plan.member.strategy_spec_sha256,
        "candles_sha256": plan.candles_sha256,
        "signals_sha256": plan.signals_sha256,
        "dataset_sha256": plan.qualified_sleeve.dataset_sha256,
        "candle_source_bindings_sha256": plan.qualified_sleeve.candle_source_bindings_sha256,
        "source_file_sha256s": list(plan.qualified_sleeve.source_file_sha256s),
        "source_imports_sha256": plan.source_imports_sha256,
        "quality_report_sha256": report_hash,
        "calendar": {
            "path": str(registry.configuration_path),
            "configuration_sha256": registry.configuration_sha256,
            "registry_sha256": calendar_document["registry_sha256"],
            "effective_calendar_configuration_sha256": (
                plan.qualified_sleeve.effective_calendar_configuration_sha256
            ),
            "expected_schedule_sha256": plan.qualified_sleeve.expected_schedule_sha256,
        },
        "execution_configuration": plan.request.config.to_dict(),
        "execution_configuration_sha256": strategy_lab._canonical_hash(
            plan.request.config.to_dict()
        ),
        "implementation_source_sha256": {
            **dict(m2b_fingerprints),
            "portfolio_execution": m2c_fingerprint,
        },
        "execution_result_sha256": result_sha256,
    }


def _snapshot_execution_result(
    result: backtesting.ExecutionResult, plan: _ExecutionPlan
) -> tuple[backtesting.ExecutionResult, str]:
    """Strictly validate, detach, revalidate and integrity-hash one M1B result."""

    _validate_execution_result(result, plan)
    detached = backtesting.ExecutionResult(
        orders=_copy_result_records(result.orders),
        fills=_copy_result_records(result.fills),
        rejected_or_unfilled_orders=_copy_result_records(result.rejected_or_unfilled_orders),
        trades=_copy_result_records(result.trades),
        equity_curve=_copy_result_records(result.equity_curve),
        open_position=(
            _copy_result_mapping(result.open_position) if result.open_position is not None else None
        ),
        summary=_copy_result_mapping(result.summary),
        diagnostics=_copy_result_mapping(result.diagnostics),
        buy_and_hold_benchmark=_copy_result_mapping(result.buy_and_hold_benchmark),
    )
    _validate_execution_result(detached, plan)
    return detached, _execution_result_sha256(detached)


def _validate_execution_result(result: backtesting.ExecutionResult, plan: _ExecutionPlan) -> None:
    if type(result) is not backtesting.ExecutionResult:
        raise _PortfolioExecutionIntegrityError("M1B returned a substituted result type.")
    for field in fields(backtesting.ExecutionResult):
        _validate_result_tree(getattr(result, field.name), field.name)
    orders = _require_record_tuple(result.orders, "orders")
    fills = _require_record_tuple(result.fills, "fills")
    rejections = _require_record_tuple(
        result.rejected_or_unfilled_orders, "rejected_or_unfilled_orders"
    )
    trades = _require_record_tuple(result.trades, "trades")
    equity_curve = _require_record_tuple(result.equity_curve, "equity_curve")
    if result.open_position is not None and not isinstance(result.open_position, Mapping):
        raise _PortfolioExecutionIntegrityError("open_position must be a mapping or None.")
    summary = _require_mapping(result.summary, "summary")
    diagnostics = _require_mapping(result.diagnostics, "diagnostics")
    benchmark = _require_mapping(result.buy_and_hold_benchmark, "buy_and_hold_benchmark")

    _require_keys(summary, _SUMMARY_FIELDS, "summary")
    _require_keys(diagnostics, _DIAGNOSTIC_FIELDS, "diagnostics")
    with localcontext() as context:
        context.prec = 50
        context.rounding = ROUND_HALF_UP
        _validate_orders(orders, plan)
        _validate_equity_curve(equity_curve, orders, plan)
        _validate_fills(fills, orders, equity_curve, plan)
        _validate_trades(trades, orders, fills)
        _validate_rejections(rejections, orders)
        _validate_open_position(result.open_position, summary, equity_curve, orders, plan)
        _validate_summary(summary, result.open_position, trades, fills, equity_curve, plan)
        _validate_diagnostics(diagnostics, plan)
        _validate_benchmark(benchmark, plan)


def _validate_orders(orders: tuple[Mapping[str, object], ...], plan: _ExecutionPlan) -> None:
    if len(orders) != len(plan.request.signals):
        raise _PortfolioExecutionIntegrityError("M1B orders do not match generated signals.")
    schedule = dict(plan.request.expected_by_timestamp)
    candles = plan.request.candles
    position_quantity = 0
    cash = plan.request.config.starting_cash
    open_entry: tuple[int, Decimal, Decimal, str] | None = None
    signal_indexes = {candle.timestamp: index for index, candle in enumerate(candles)}
    for index, (order_value, signal) in enumerate(zip(orders, plan.request.signals, strict=True)):
        order = _require_mapping(order_value, f"orders[{index}]")
        base = set(_ORDER_BASE_FIELDS)
        if type(order.get("order_id")) is not int or order["order_id"] != index + 1:
            raise _PortfolioExecutionIntegrityError("Order IDs must be sequential exact integers.")
        _require_text(order.get("order_type"), f"orders[{index}].order_type")
        if order["order_type"] != "MARKET":
            raise _PortfolioExecutionIntegrityError("M1B order_type must be MARKET.")
        if (
            order.get("signal") != signal.action
            or order.get("signal_candle_timestamp") != signal.timestamp
        ):
            raise _PortfolioExecutionIntegrityError("Order does not match its generated signal.")
        expected_side: Literal["BUY", "SELL"] = "BUY" if signal.action == "ENTER_LONG" else "SELL"
        if order.get("side") != expected_side:
            raise _PortfolioExecutionIntegrityError("Order side does not match its signal.")
        _require_datetime(
            order.get("signal_candle_timestamp"), f"orders[{index}].signal_candle_timestamp"
        )
        expected_submission = schedule.get(signal.timestamp)
        if (
            expected_submission is None
            or order.get("submitted_at") != expected_submission.isoformat()
        ):
            raise _PortfolioExecutionIntegrityError(
                "Order submission time differs from the schedule."
            )
        _require_datetime(order.get("submitted_at"), f"orders[{index}].submitted_at")
        status = _require_text(order.get("status"), f"orders[{index}].status")
        if status not in {"FILLED", "REJECTED", "UNFILLED_NO_NEXT_BAR"}:
            raise _PortfolioExecutionIntegrityError("Order has an unsupported status.")
        has_execution = "execution_bar_timestamp" in order
        has_prices = bool(_ORDER_PRICE_FIELDS & set(order))
        if has_execution:
            base.update(_ORDER_EXECUTION_FIELDS)
            if has_prices:
                base.update(_ORDER_PRICE_FIELDS)
            base.update({"status", "reason"})
            _require_keys(order, base, f"orders[{index}]")
            position_quantity, cash, open_entry = _validate_pending_order(
                order,
                signal,
                index,
                position_quantity,
                cash,
                open_entry,
                signal_indexes,
                plan,
            )
        else:
            base.update({"quantity", "status", "reason"})
            _require_keys(order, base, f"orders[{index}]")
            quantity = _require_int(order["quantity"], f"orders[{index}].quantity", minimum=0)
            if status == "FILLED":
                raise _PortfolioExecutionIntegrityError("A filled order lacks execution evidence.")
            reason = _require_text(order["reason"], f"orders[{index}].reason")
            if status == "UNFILLED_NO_NEXT_BAR":
                if reason != "No next eligible candle exists in the registered date range.":
                    raise _PortfolioExecutionIntegrityError("No-next-bar reason is not M1B output.")
                if signal_indexes[signal.timestamp] != len(candles) - 1:
                    raise _PortfolioExecutionIntegrityError(
                        "No-next-bar order was not submitted on the final candle."
                    )
                expected_quantity = (
                    plan.request.config.order_quantity
                    if expected_side == "BUY"
                    else position_quantity
                )
                if quantity != expected_quantity or (
                    expected_side == "SELL" and not position_quantity
                ):
                    raise _PortfolioExecutionIntegrityError(
                        "No-next-bar quantity does not match the M1B position."
                    )
            else:
                expected_reason = (
                    "A long position is already open."
                    if expected_side == "BUY" and position_quantity
                    else "No open long position exists."
                    if expected_side == "SELL" and not position_quantity
                    else None
                )
                if expected_reason is None or reason != expected_reason:
                    raise _PortfolioExecutionIntegrityError(
                        "Immediate rejection reason does not match M1B position state."
                    )
                expected_quantity = (
                    plan.request.config.order_quantity if expected_side == "BUY" else 0
                )
                if quantity != expected_quantity:
                    raise _PortfolioExecutionIntegrityError(
                        "Immediate rejection quantity does not match M1B behavior."
                    )
    if (
        len(orders)
        and any(order.get("status") == "UNFILLED_NO_NEXT_BAR" for order in orders)
        and orders[-1].get("status") != "UNFILLED_NO_NEXT_BAR"
    ):
        raise _PortfolioExecutionIntegrityError("No-next-bar order must be the final order.")


def _validate_pending_order(
    order: Mapping[str, object],
    signal: strategy_lab.StrategySignal,
    order_index: int,
    position_quantity: int,
    cash: Decimal,
    open_entry: tuple[int, Decimal, Decimal, str] | None,
    signal_indexes: Mapping[str, int],
    plan: _ExecutionPlan,
) -> tuple[int, Decimal, tuple[int, Decimal, Decimal, str] | None]:
    side: Literal["BUY", "SELL"] = "BUY" if signal.action == "ENTER_LONG" else "SELL"
    if (side == "BUY" and position_quantity != 0) or (side == "SELL" and position_quantity == 0):
        raise _PortfolioExecutionIntegrityError(
            "M1B cannot queue an order inconsistent with its long-only position."
        )
    quantity = _require_int(order["quantity"], f"orders[{order_index}].quantity", minimum=0)
    expected_quantity = plan.request.config.order_quantity if side == "BUY" else position_quantity
    if quantity != expected_quantity or quantity < 1:
        raise _PortfolioExecutionIntegrityError("Pending order quantity differs from M1B sizing.")
    signal_index = signal_indexes[signal.timestamp]
    if signal_index + 1 >= len(plan.request.candles):
        raise _PortfolioExecutionIntegrityError("A final-bar signal cannot have an execution bar.")
    execution_candle = plan.request.candles[signal_index + 1]
    prior_candle = plan.request.candles[signal_index]
    if (
        order["execution_bar_timestamp"] != execution_candle.timestamp
        or order["execution_time"]
        != backtesting._execution_time(execution_candle, plan.request.interval, plan.calendar)
        or order["reference_open"] != backtesting._decimal_text(execution_candle.open)
        or order["prior_bar_volume"] != prior_candle.volume
    ):
        raise _PortfolioExecutionIntegrityError(
            "Pending order execution evidence differs from qualified candles."
        )
    _require_datetime(
        order["execution_bar_timestamp"], f"orders[{order_index}].execution_bar_timestamp"
    )
    _require_datetime(order["execution_time"], f"orders[{order_index}].execution_time")
    reference_open = _require_decimal(
        order["reference_open"], f"orders[{order_index}].reference_open", positive=True
    )
    if reference_open != execution_candle.open:
        raise _PortfolioExecutionIntegrityError("Order reference open differs from its candle.")
    prior_volume = _require_int(
        order["prior_bar_volume"], f"orders[{order_index}].prior_bar_volume", minimum=0
    )
    capacity = _require_int(
        order["volume_capacity"], f"orders[{order_index}].volume_capacity", minimum=0
    )
    expected_capacity = int(
        (Decimal(prior_volume) * plan.request.config.max_volume_participation).to_integral_value(
            rounding=ROUND_FLOOR
        )
    )
    if capacity != expected_capacity:
        raise _PortfolioExecutionIntegrityError("Order volume capacity differs from M1B rules.")
    status = order["status"]
    priced = bool(_ORDER_PRICE_FIELDS & set(order))
    if quantity > capacity:
        if status != "REJECTED" or priced:
            raise _PortfolioExecutionIntegrityError(
                "Volume-rejected M1B orders must not contain calculated fill costs."
            )
        reason = _require_text(order["reason"], f"orders[{order_index}].reason")
        if "prior completed bar's volume" not in reason:
            raise _PortfolioExecutionIntegrityError("Volume rejection reason is invalid.")
        return position_quantity, cash, open_entry

    if not priced:
        raise _PortfolioExecutionIntegrityError(
            "An order within volume capacity is missing M1B price and fee evidence."
        )
    fill_price, spread_per_unit, slippage_per_unit, tick_per_unit = backtesting._fill_price(
        execution_candle.open,
        side,
        plan.request.instrument.tick_size_decimal,
        plan.request.config.costs,
    )
    fees = backtesting._fees(fill_price, quantity, side, plan.request.config.costs)
    fee_total = backtesting._fee_total(fees)
    expected_pricing = {
        "fill_price": backtesting._decimal_text(fill_price),
        "fees": {key: backtesting._decimal_text(value) for key, value in fees.items()},
        "total_fees": backtesting._decimal_text(fee_total),
        "spread_cost": backtesting._decimal_text(spread_per_unit * quantity),
        "slippage_cost": backtesting._decimal_text(slippage_per_unit * quantity),
        "tick_rounding_cost": backtesting._decimal_text(tick_per_unit * quantity),
    }
    for key, expected in expected_pricing.items():
        actual = order[key]
        if key == "fees":
            _validate_fees(actual, f"orders[{order_index}].fees")
        elif key != "fill_price":
            _require_decimal(actual, f"orders[{order_index}].{key}", nonnegative=True)
        else:
            _require_decimal(actual, f"orders[{order_index}].{key}", positive=True)
        if actual != expected:
            raise _PortfolioExecutionIntegrityError(
                f"Order {key} differs from M1B fill and cost calculations."
            )
    insufficient_cash = side == "BUY" and cash < fill_price * quantity + fee_total
    if insufficient_cash:
        if status != "REJECTED" or order["reason"] != "Insufficient cash for fill and charges.":
            raise _PortfolioExecutionIntegrityError("Unaffordable M1B order has invalid rejection.")
        return position_quantity, cash, open_entry
    if status != "FILLED" or order["reason"] is not None:
        raise _PortfolioExecutionIntegrityError("Affordable M1B order must be filled.")
    if side == "BUY":
        cash = backtesting._money(cash - fill_price * quantity - fee_total)
        position_quantity = quantity
        open_entry = (
            order_index + 1,
            fill_price,
            fee_total,
            str(order["execution_time"]),
        )
    else:
        cash = backtesting._money(cash + fill_price * quantity - fee_total)
        position_quantity = 0
        open_entry = None
    return position_quantity, cash, open_entry


def _validate_equity_curve(
    equity_curve: tuple[Mapping[str, object], ...],
    orders: tuple[Mapping[str, object], ...],
    plan: _ExecutionPlan,
) -> None:
    if len(equity_curve) != len(plan.request.candles):
        raise _PortfolioExecutionIntegrityError("Equity curve must mark every qualified candle.")
    cash = plan.request.config.starting_cash
    position = 0
    filled_by_timestamp: dict[str, Mapping[str, object]] = {}
    for order_value in orders:
        order = _require_mapping(order_value, "orders")
        if order.get("status") == "FILLED":
            timestamp = order.get("execution_bar_timestamp")
            if type(timestamp) is not str or timestamp in filled_by_timestamp:
                raise _PortfolioExecutionIntegrityError(
                    "M1B has an invalid or duplicate filled execution timestamp."
                )
            filled_by_timestamp[timestamp] = order
    for index, (point_value, candle) in enumerate(
        zip(equity_curve, plan.request.candles, strict=True)
    ):
        fill_order = filled_by_timestamp.get(candle.timestamp)
        if fill_order is not None:
            quantity = _require_int(fill_order["quantity"], "filled order quantity", minimum=1)
            fill_price = _require_decimal(
                fill_order["fill_price"], "filled order fill_price", positive=True
            )
            fee_total = _require_decimal(
                fill_order["total_fees"], "filled order total_fees", nonnegative=True
            )
            turnover = fill_price * quantity
            if fill_order["side"] == "BUY":
                if position != 0:
                    raise _PortfolioExecutionIntegrityError("M1B opened a second long position.")
                cash = backtesting._money(cash - turnover - fee_total)
                position = quantity
            else:
                if position != quantity:
                    raise _PortfolioExecutionIntegrityError(
                        "M1B exit quantity differs from its open position."
                    )
                cash = backtesting._money(cash + turnover - fee_total)
                position = 0
        point = _require_mapping(point_value, f"equity_curve[{index}]")
        _require_keys(point, _EQUITY_FIELDS, f"equity_curve[{index}]")
        if point["timestamp"] != candle.timestamp:
            raise _PortfolioExecutionIntegrityError(
                "Equity mark timestamp differs from its candle."
            )
        _require_datetime(point["timestamp"], f"equity_curve[{index}].timestamp")
        _require_date(point["local_date"], f"equity_curve[{index}].local_date")
        local_date = (
            datetime.fromisoformat(candle.timestamp)
            .astimezone(ZoneInfo(plan.calendar.timezone_name))
            .date()
            .isoformat()
        )
        if point["local_date"] != local_date:
            raise _PortfolioExecutionIntegrityError("Equity local date differs from calendar time.")
        cash_value = _require_decimal(point["cash"], f"equity_curve[{index}].cash")
        quantity_text = point["position_quantity"]
        quantity = _require_integer_text(
            quantity_text, f"equity_curve[{index}].position_quantity", minimum=0
        )
        mark = _require_decimal(
            point["mark_price"], f"equity_curve[{index}].mark_price", positive=True
        )
        equity = _require_decimal(point["equity"], f"equity_curve[{index}].equity")
        if (
            mark != candle.close
            or equity != cash_value + mark * quantity
            or cash_value != cash
            or quantity != position
        ):
            raise _PortfolioExecutionIntegrityError("Equity mark does not reconcile to M1B values.")
        if index == 0 and cash_value != plan.request.config.starting_cash:
            raise _PortfolioExecutionIntegrityError("Initial equity cash differs from M1B config.")


def _validate_fills(
    fills: tuple[Mapping[str, object], ...],
    orders: tuple[Mapping[str, object], ...],
    equity_curve: tuple[Mapping[str, object], ...],
    plan: _ExecutionPlan,
) -> None:
    fill_orders = [order for order in orders if order.get("status") == "FILLED"]
    if len(fills) != len(fill_orders):
        raise _PortfolioExecutionIntegrityError("M1B fills do not match filled orders.")
    equity_by_timestamp = {point["timestamp"]: point for point in equity_curve}
    candle_indexes = {candle.timestamp: index for index, candle in enumerate(plan.request.candles)}
    for index, (fill_value, order) in enumerate(zip(fills, fill_orders, strict=True)):
        fill = _require_mapping(fill_value, f"fills[{index}]")
        _require_keys(fill, _FILL_FIELDS, f"fills[{index}]")
        for key in (
            "order_id",
            "execution_bar_timestamp",
            "execution_time",
            "side",
            "quantity",
            "fill_price",
            "fees",
            "total_fees",
        ):
            if fill[key] != order[key]:
                raise _PortfolioExecutionIntegrityError(
                    f"fills[{index}].{key} does not match its filled order."
                )
        _require_int(fill["order_id"], f"fills[{index}].order_id", minimum=1)
        _require_int(fill["quantity"], f"fills[{index}].quantity", minimum=1)
        _validate_fees(fill["fees"], f"fills[{index}].fees")
        for name in ("fill_price", "total_fees", "cash_before", "cash_after"):
            _require_decimal(
                fill[name],
                f"fills[{index}].{name}",
                nonnegative=name == "total_fees",
                positive=name == "fill_price",
            )
        before_position = _require_integer_text(
            fill["position_quantity_before"], f"fills[{index}].position_quantity_before", minimum=0
        )
        after_position = _require_integer_text(
            fill["position_quantity_after"], f"fills[{index}].position_quantity_after", minimum=0
        )
        execution_index = candle_indexes.get(str(fill["execution_bar_timestamp"]))
        if execution_index is None or execution_index < 1:
            raise _PortfolioExecutionIntegrityError("Fill does not point to an eligible candle.")
        before_point = equity_by_timestamp[plan.request.candles[execution_index - 1].timestamp]
        after_point = equity_by_timestamp[plan.request.candles[execution_index].timestamp]
        if (
            fill["cash_before"] != before_point["cash"]
            or fill["cash_after"] != after_point["cash"]
            or fill["position_quantity_before"] != before_point["position_quantity"]
            or fill["position_quantity_after"] != after_point["position_quantity"]
            or before_position != int(str(before_point["position_quantity"]))
            or after_position != int(str(after_point["position_quantity"]))
        ):
            raise _PortfolioExecutionIntegrityError(
                "Fill cash or position transition is inconsistent."
            )


def _validate_trades(
    trades: tuple[Mapping[str, object], ...],
    orders: tuple[Mapping[str, object], ...],
    fills: tuple[Mapping[str, object], ...],
) -> None:
    filled_by_order = {fill["order_id"]: fill for fill in fills}
    expected_trades: list[dict[str, object]] = []
    entry: Mapping[str, object] | None = None
    for order in orders:
        if order.get("status") != "FILLED":
            continue
        fill = filled_by_order[order["order_id"]]
        if order["side"] == "BUY":
            if entry is not None:
                raise _PortfolioExecutionIntegrityError("M1B cannot open multiple long positions.")
            entry = fill
        else:
            if entry is None:
                raise _PortfolioExecutionIntegrityError("M1B cannot close a missing long position.")
            entry_fees = _require_decimal(entry["total_fees"], "entry fill total_fees")
            exit_fees = _require_decimal(fill["total_fees"], "exit fill total_fees")
            entry_price = _require_decimal(entry["fill_price"], "entry fill fill_price")
            exit_price = _require_decimal(fill["fill_price"], "exit fill fill_price")
            quantity = _require_int(fill["quantity"], "exit fill quantity", minimum=1)
            if (
                fill["quantity"] != entry["quantity"]
                or fill["position_quantity_before"] != str(quantity)
                or fill["position_quantity_after"] != "0"
            ):
                raise _PortfolioExecutionIntegrityError(
                    "M1B trade quantity or position is invalid."
                )
            gross = (exit_price - entry_price) * quantity
            expected_trades.append(
                {
                    "trade_id": len(expected_trades) + 1,
                    "entry_order_id": entry["order_id"],
                    "exit_order_id": fill["order_id"],
                    "entry_time": entry["execution_time"],
                    "exit_time": fill["execution_time"],
                    "quantity": quantity,
                    "entry_price": backtesting._decimal_text(entry_price),
                    "exit_price": backtesting._decimal_text(exit_price),
                    "gross_pnl": backtesting._decimal_text(gross),
                    "entry_fees": backtesting._decimal_text(entry_fees),
                    "exit_fees": backtesting._decimal_text(exit_fees),
                    "net_pnl": backtesting._decimal_text(gross - entry_fees - exit_fees),
                }
            )
            entry = None
    if len(trades) != len(expected_trades):
        raise _PortfolioExecutionIntegrityError("M1B trades do not match filled entry/exit pairs.")
    for index, (trade_value, expected) in enumerate(zip(trades, expected_trades, strict=True)):
        trade = _require_mapping(trade_value, f"trades[{index}]")
        _require_keys(trade, _TRADE_FIELDS, f"trades[{index}]")
        for key in ("trade_id", "entry_order_id", "exit_order_id", "quantity"):
            _require_int(trade[key], f"trades[{index}].{key}", minimum=1)
        for key in ("entry_time", "exit_time"):
            _require_datetime(trade[key], f"trades[{index}].{key}")
        for key in _TRADE_FIELDS - {
            "trade_id",
            "entry_order_id",
            "exit_order_id",
            "entry_time",
            "exit_time",
            "quantity",
        }:
            _require_decimal(trade[key], f"trades[{index}].{key}")
        if dict(trade) != expected:
            raise _PortfolioExecutionIntegrityError("Trade values do not reconcile to M1B fills.")


def _validate_rejections(
    rejections: tuple[Mapping[str, object], ...],
    orders: tuple[Mapping[str, object], ...],
) -> None:
    expected = [
        {
            "order_id": order["order_id"],
            "status": order["status"],
            "reason": order["reason"],
        }
        for order in orders
        if order["status"] != "FILLED"
    ]
    if len(rejections) != len(expected):
        raise _PortfolioExecutionIntegrityError("Rejected/unfilled projection is incomplete.")
    for index, (rejection_value, expected_item) in enumerate(
        zip(rejections, expected, strict=True)
    ):
        rejection = _require_mapping(rejection_value, f"rejected_or_unfilled_orders[{index}]")
        _require_keys(
            rejection,
            {"order_id", "status", "reason"},
            f"rejected_or_unfilled_orders[{index}]",
        )
        _require_int(
            rejection["order_id"], f"rejected_or_unfilled_orders[{index}].order_id", minimum=1
        )
        if (
            type(rejection["status"]) is not str
            or rejection["status"] not in {"REJECTED", "UNFILLED_NO_NEXT_BAR"}
            or type(rejection["reason"]) is not str
            or dict(rejection) != expected_item
        ):
            raise _PortfolioExecutionIntegrityError(
                "Rejected/unfilled projection differs from M1B order records."
            )


def _validate_open_position(
    open_position_value: object,
    summary: Mapping[str, object],
    equity_curve: tuple[Mapping[str, object], ...],
    orders: tuple[Mapping[str, object], ...],
    plan: _ExecutionPlan,
) -> None:
    if not equity_curve:
        raise _PortfolioExecutionIntegrityError("M1B returned an empty equity curve.")
    final_point = equity_curve[-1]
    final_quantity = _require_integer_text(
        final_point["position_quantity"], "equity_curve[-1].position_quantity", minimum=0
    )
    filled_buys = [
        order for order in orders if order["status"] == "FILLED" and order["side"] == "BUY"
    ]
    filled_sells = [
        order for order in orders if order["status"] == "FILLED" and order["side"] == "SELL"
    ]
    if open_position_value is None:
        if final_quantity != 0 or len(filled_buys) != len(filled_sells):
            raise _PortfolioExecutionIntegrityError(
                "M1B omitted an open position that remains after the final fill."
            )
        open_position: Mapping[str, object] | None = None
    else:
        open_position = _require_mapping(open_position_value, "open_position")
        _require_keys(open_position, _OPEN_POSITION_FIELDS, "open_position")
        quantity = _require_int(open_position["quantity"], "open_position.quantity", minimum=1)
        for name in _OPEN_POSITION_FIELDS - {"quantity", "entry_time"}:
            _require_decimal(open_position[name], f"open_position.{name}")
        _require_datetime(open_position["entry_time"], "open_position.entry_time")
        if not filled_buys or len(filled_buys) != len(filled_sells) + 1:
            raise _PortfolioExecutionIntegrityError("Open position has no matching filled entry.")
        entry = filled_buys[-1]
        if (
            quantity != final_quantity
            or open_position["mark_price"] != final_point["mark_price"]
            or open_position["entry_price"] != entry["fill_price"]
            or open_position["entry_time"] != entry["execution_time"]
            or open_position["entry_fees"] != entry["total_fees"]
        ):
            raise _PortfolioExecutionIntegrityError(
                "Open position does not match final equity and its M1B entry."
            )
        mark = _require_decimal(final_point["mark_price"], "equity_curve[-1].mark_price")
        entry_price = _require_decimal(entry["fill_price"], "open position entry price")
        entry_fees = _require_decimal(entry["total_fees"], "open position entry fees")
        gross = (mark - entry_price) * quantity
        exit_fill, _, _, _ = backtesting._fill_price(
            mark,
            "SELL",
            plan.request.instrument.tick_size_decimal,
            plan.request.config.costs,
        )
        exit_fees = backtesting._fee_total(
            backtesting._fees(
                exit_fill,
                quantity,
                "SELL",
                plan.request.config.costs,
            )
        )
        liquidation_costs = backtesting._money((mark - exit_fill) * quantity + exit_fees)
        ending_equity = _require_decimal(final_point["equity"], "equity_curve[-1].equity")
        expected = {
            "quantity": quantity,
            "entry_price": backtesting._decimal_text(entry_price),
            "entry_time": entry["execution_time"],
            "entry_fees": backtesting._decimal_text(entry_fees),
            "mark_price": backtesting._decimal_text(mark),
            "unrealized_gross_pnl": backtesting._decimal_text(gross),
            "unrealized_net_pnl": backtesting._decimal_text(gross - entry_fees - liquidation_costs),
            "estimated_liquidation_costs": backtesting._decimal_text(liquidation_costs),
            "net_liquidation_value": backtesting._decimal_text(ending_equity - liquidation_costs),
        }
        if dict(open_position) != expected:
            raise _PortfolioExecutionIntegrityError(
                "Open position does not reconcile to the M1B liquidation estimate."
            )
    if summary["open_position"] != open_position_value:
        raise _PortfolioExecutionIntegrityError(
            "Summary open position differs from the top-level M1B position."
        )


def _validate_summary(
    summary: Mapping[str, object],
    open_position: object,
    trades: tuple[Mapping[str, object], ...],
    fills: tuple[Mapping[str, object], ...],
    equity_curve: tuple[Mapping[str, object], ...],
    plan: _ExecutionPlan,
) -> None:
    for name in _SUMMARY_DECIMAL_FIELDS:
        _require_decimal(summary[name], f"summary.{name}")
    for name in _SUMMARY_COUNT_FIELDS:
        _require_int(summary[name], f"summary.{name}", minimum=0)
    for name in _SUMMARY_NULLABLE_DECIMAL_FIELDS:
        if summary[name] is not None:
            _require_decimal(summary[name], f"summary.{name}")
    if type(summary["daily_returns"]) is not tuple:
        raise _PortfolioExecutionIntegrityError("summary.daily_returns must be an immutable tuple.")
    expected_daily = backtesting._daily_metrics(
        [_copy_result_string_record(point, "equity_curve") for point in equity_curve],
        plan.request.config.starting_cash,
        [_copy_result_mapping(trade) for trade in trades],
    )
    with localcontext() as context:
        context.prec = 50
        context.rounding = ROUND_HALF_UP
        starting_cash = plan.request.config.starting_cash
        final_point = _require_mapping(equity_curve[-1], "equity_curve[-1]")
        ending_cash = _require_decimal(final_point["cash"], "equity_curve[-1].cash")
        ending_equity = _require_decimal(final_point["equity"], "equity_curve[-1].equity")
        estimated_liquidation_costs = (
            _require_decimal(open_position["estimated_liquidation_costs"], "open position costs")
            if isinstance(open_position, Mapping)
            else Decimal(0)
        )
        net_liquidation_value = ending_equity - estimated_liquidation_costs
        realized_net_pnl = sum(
            (_require_decimal(trade["net_pnl"], "trade.net_pnl") for trade in trades),
            Decimal(0),
        )
        total_fees = sum(
            (_require_decimal(fill["total_fees"], "fill.total_fees") for fill in fills),
            Decimal(0),
        )
        filled_turnover = sum(
            (
                _require_decimal(fill["fill_price"], "fill.fill_price")
                * _require_int(fill["quantity"], "fill.quantity", minimum=1)
                for fill in fills
            ),
            Decimal(0),
        )
        peak = starting_cash
        maximum_drawdown = Decimal(0)
        for point_value in equity_curve:
            point = _require_mapping(point_value, "equity_curve")
            equity = _require_decimal(point["equity"], "equity_curve.equity")
            peak = max(peak, equity)
            if peak:
                maximum_drawdown = max(maximum_drawdown, (peak - equity) / peak)
        base = {
            "starting_cash": backtesting._decimal_text(starting_cash),
            "ending_cash": backtesting._decimal_text(ending_cash),
            "ending_equity": backtesting._decimal_text(ending_equity),
            "net_liquidation_value": backtesting._decimal_text(net_liquidation_value),
            "absolute_pnl": backtesting._decimal_text(ending_equity - starting_cash),
            "net_liquidation_pnl": backtesting._decimal_text(net_liquidation_value - starting_cash),
            "total_return": backtesting._decimal_text(ending_equity / starting_cash - Decimal(1)),
            "net_liquidation_return": backtesting._decimal_text(
                net_liquidation_value / starting_cash - Decimal(1)
            ),
            "realized_net_pnl": backtesting._decimal_text(realized_net_pnl),
            "unrealized_gross_pnl": (
                open_position["unrealized_gross_pnl"] if isinstance(open_position, Mapping) else "0"
            ),
            "unrealized_net_pnl": (
                open_position["unrealized_net_pnl"] if isinstance(open_position, Mapping) else "0"
            ),
            "estimated_liquidation_costs": backtesting._decimal_text(estimated_liquidation_costs),
            "total_fees": backtesting._decimal_text(total_fees),
            "filled_turnover": backtesting._decimal_text(filled_turnover),
            "open_position_quantity": (
                open_position["quantity"] if isinstance(open_position, Mapping) else 0
            ),
            "maximum_drawdown": backtesting._decimal_text(maximum_drawdown),
            "open_position": _copy_result_value(open_position),
            **expected_daily,
        }
        if set(base) != _SUMMARY_FIELDS or _copy_result_value(summary) != base:
            raise _PortfolioExecutionIntegrityError(
                "M1B summary values do not reconcile to its orders, fills, trades and marks."
            )


def _validate_diagnostics(diagnostics: Mapping[str, object], plan: _ExecutionPlan) -> None:
    request = plan.request
    instrument = _require_mapping(diagnostics["instrument"], "diagnostics.instrument")
    _require_keys(instrument, _INSTRUMENT_FIELDS, "diagnostics.instrument")
    expected_instrument = {
        "instrument_id": request.instrument.instrument_id,
        "exchange": request.instrument.exchange.strip().upper(),
        "trading_symbol": request.instrument.trading_symbol.strip().upper(),
        "segment": request.instrument.segment.strip().upper(),
        "instrument_type": request.instrument.instrument_type.strip().upper(),
        "lot_size": request.instrument.lot_size,
        "tick_size_decimal": backtesting._decimal_text(request.instrument.tick_size_decimal),
    }
    for name in ("instrument_id", "lot_size"):
        _require_int(instrument[name], f"diagnostics.instrument.{name}", minimum=1)
    _require_decimal(
        instrument["tick_size_decimal"], "diagnostics.instrument.tick_size_decimal", positive=True
    )
    if dict(instrument) != expected_instrument:
        raise _PortfolioExecutionIntegrityError("M1B diagnostic instrument was substituted.")
    if diagnostics["interval"] != request.interval:
        raise _PortfolioExecutionIntegrityError("M1B diagnostic interval was substituted.")
    configuration = _require_mapping(
        diagnostics["execution_configuration"], "diagnostics.execution_configuration"
    )
    _require_keys(
        configuration,
        {"starting_cash", "order_quantity", "max_volume_participation", "costs"},
        "diagnostics.execution_configuration",
    )
    _require_decimal(
        configuration["starting_cash"], "execution_configuration.starting_cash", positive=True
    )
    _require_int(
        configuration["order_quantity"], "execution_configuration.order_quantity", minimum=1
    )
    _require_decimal(
        configuration["max_volume_participation"],
        "execution_configuration.max_volume_participation",
        positive=True,
    )
    costs = _require_mapping(configuration["costs"], "execution_configuration.costs")
    _require_keys(costs, _COST_FIELDS, "execution_configuration.costs")
    for key in _COST_FIELDS:
        _require_decimal(costs[key], f"execution_configuration.costs.{key}", nonnegative=True)
    if dict(configuration) != request.config.to_dict():
        raise _PortfolioExecutionIntegrityError(
            "M1B diagnostic execution configuration differs from its internal request."
        )

    signal_provenance = _require_mapping(
        diagnostics["signal_provenance"], "diagnostics.signal_provenance"
    )
    _require_keys(
        signal_provenance,
        {"status", "strategy_key", "name", "version", "parameters", "strategy_spec_sha256"},
        "diagnostics.signal_provenance",
    )
    parameters = _require_mapping(
        signal_provenance["parameters"], "diagnostics.signal_provenance.parameters"
    )
    _require_keys(parameters, {"fast_window", "slow_window"}, "signal_provenance.parameters")
    _require_int(parameters["fast_window"], "signal_provenance.parameters.fast_window", minimum=2)
    _require_int(parameters["slow_window"], "signal_provenance.parameters.slow_window", minimum=3)
    expected_signal_provenance = request.signal_provenance.to_dict()
    if dict(signal_provenance) != expected_signal_provenance:
        raise _PortfolioExecutionIntegrityError("M1B strategy/signal provenance was substituted.")
    expected_hashes = {
        "strategy_implementation_sha256": strategy_lab._sha256_file(
            Path(backtesting.__file__).with_name("strategy_lab.py")
        ),
        "signals_sha256": plan.signals_sha256,
        "dataset_sha256": request.dataset_sha256,
        "candle_source_bindings_sha256": request.candle_source_bindings_sha256,
        "source_imports_sha256": plan.source_imports_sha256,
        "calendar_configuration_sha256": request.calendar_configuration_sha256,
        "effective_calendar_configuration_sha256": (
            request.effective_calendar_configuration_sha256
        ),
        "expected_schedule_sha256": request.expected_schedule_sha256,
        "quality_report_sha256": strategy_lab._canonical_hash(
            backtesting._evidence_json_value(asdict(request.eligibility.quality_report))
        ),
    }
    for name, expected in expected_hashes.items():
        _require_hash(diagnostics[name], f"diagnostics.{name}")
        if diagnostics[name] != expected:
            raise _PortfolioExecutionIntegrityError(
                f"M1B diagnostic {name} differs from qualified provenance."
            )
    source_hashes = _require_tuple(
        diagnostics["source_file_sha256s"], "diagnostics.source_file_sha256s"
    )
    for index, fingerprint in enumerate(source_hashes):
        _require_hash(fingerprint, f"diagnostics.source_file_sha256s[{index}]")
    if source_hashes != request.source_file_sha256s:
        raise _PortfolioExecutionIntegrityError("M1B source-file hashes differ from M2-B.")
    _require_hash(
        diagnostics["calendar_configuration_sha256"],
        "diagnostics.calendar_configuration_sha256",
    )
    quality = _require_mapping(diagnostics["data_quality"], "diagnostics.data_quality")
    _require_keys(quality, _DATA_QUALITY_FIELDS, "diagnostics.data_quality")
    report = request.eligibility.quality_report
    expected_quality = {
        "status": report.overall_status,
        "calendar_status": report.calendar_status,
        "calendar_confidence": report.calendar_confidence,
        "staleness_status": report.staleness_status,
        "completeness_percentage": (
            backtesting._decimal_text(report.completeness_percentage)
            if report.completeness_percentage is not None
            else None
        ),
        "expected_candles": report.expected_candles,
        "actual_candles": report.actual_candles,
        "as_of": report.as_of.astimezone(UTC).isoformat(timespec="seconds"),
    }
    for key in ("status", "calendar_status", "calendar_confidence", "staleness_status"):
        _require_text(quality[key], f"diagnostics.data_quality.{key}")
    if quality["completeness_percentage"] is not None:
        _require_decimal(quality["completeness_percentage"], "data_quality.completeness_percentage")
    if quality["expected_candles"] is not None:
        _require_int(quality["expected_candles"], "data_quality.expected_candles", minimum=0)
    _require_int(quality["actual_candles"], "data_quality.actual_candles", minimum=0)
    _require_datetime(quality["as_of"], "diagnostics.data_quality.as_of")
    if dict(quality) != expected_quality:
        raise _PortfolioExecutionIntegrityError("M1B data-quality diagnostics were substituted.")

    imports_value = _require_record_tuple(
        diagnostics["source_imports"], "diagnostics.source_imports"
    )
    imports: list[dict[str, object]] = []
    for index, item in enumerate(imports_value):
        _require_keys(item, _SOURCE_IMPORT_FIELDS, f"diagnostics.source_imports[{index}]")
        _validate_source_import_record(item, plan.member, index)
        imports.append(dict(item))
    if tuple(imports) != request.eligibility.source_imports:
        raise _PortfolioExecutionIntegrityError("M1B source-import diagnostics were substituted.")


def _validate_benchmark(benchmark: Mapping[str, object], plan: _ExecutionPlan) -> None:
    status = benchmark.get("status")
    if type(status) is not str:
        raise _PortfolioExecutionIntegrityError("M1B benchmark status must be text.")
    if status == "UNAVAILABLE":
        _require_keys(benchmark, {"status", "reason"}, "buy_and_hold_benchmark")
        _require_text(benchmark["reason"], "buy_and_hold_benchmark.reason")
    elif status == "COMPLETED":
        _require_keys(benchmark, _BENCHMARK_COMPLETED_FIELDS, "buy_and_hold_benchmark")
        _require_text(benchmark["method"], "buy_and_hold_benchmark.method")
        _require_int(benchmark["quantity"], "buy_and_hold_benchmark.quantity", minimum=1)
        for key in ("entry_price", "exit_price"):
            _require_decimal(benchmark[key], f"buy_and_hold_benchmark.{key}", positive=True)
        fees = _require_mapping(benchmark["fees"], "buy_and_hold_benchmark.fees")
        _require_keys(fees, {"entry", "exit", "total"}, "buy_and_hold_benchmark.fees")
        _validate_fees(fees["entry"], "buy_and_hold_benchmark.fees.entry")
        _validate_fees(fees["exit"], "buy_and_hold_benchmark.fees.exit")
        _require_decimal(fees["total"], "buy_and_hold_benchmark.fees.total", nonnegative=True)
        _require_decimal(benchmark["ending_cash"], "buy_and_hold_benchmark.ending_cash")
        _require_decimal(benchmark["total_return"], "buy_and_hold_benchmark.total_return")
        curve = _require_tuple(benchmark["equity_curve"], "buy_and_hold_benchmark.equity_curve")
        if len(curve) != len(plan.request.candles):
            raise _PortfolioExecutionIntegrityError("Benchmark curve omits qualified candles.")
        for index, point_value in enumerate(curve):
            point = _require_mapping(point_value, f"benchmark.equity_curve[{index}]")
            _require_keys(point, {"timestamp", "equity"}, f"benchmark.equity_curve[{index}]")
            _require_datetime(point["timestamp"], f"benchmark.equity_curve[{index}].timestamp")
            _require_decimal(point["equity"], f"benchmark.equity_curve[{index}].equity")
            if point["timestamp"] != plan.request.candles[index].timestamp:
                raise _PortfolioExecutionIntegrityError("Benchmark timestamp differs from candles.")
    else:
        raise _PortfolioExecutionIntegrityError("M1B benchmark status is unsupported.")
    expected = backtesting._buy_and_hold_benchmark(
        list(plan.request.candles),
        config=plan.request.config,
        tick_size=plan.request.instrument.tick_size_decimal,
    )
    if _copy_result_value(benchmark) != expected:
        raise _PortfolioExecutionIntegrityError("Benchmark differs from the M1B output builder.")


def _validate_fees(value: object, label: str) -> None:
    fees = _require_mapping(value, label)
    _require_keys(fees, _FEE_FIELDS, label)
    for key in _FEE_FIELDS:
        _require_decimal(fees[key], f"{label}.{key}", nonnegative=True)


def _execution_result_document(
    result: backtesting.ExecutionResult,
) -> dict[str, object]:
    if type(result) is not backtesting.ExecutionResult:
        raise _PortfolioExecutionIntegrityError(
            "Only M1B ExecutionResult values can be serialized."
        )
    return {
        field.name: _copy_result_value(getattr(result, field.name))
        for field in fields(backtesting.ExecutionResult)
    }


def _execution_result_sha256(result: backtesting.ExecutionResult) -> str:
    document = _execution_result_document(result)
    _validate_result_tree(document, "execution_result")
    return strategy_lab._canonical_hash(document)


def _copy_result_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _copy_result_value(item) for key, item in value.items()}
    if type(value) is tuple:
        return [_copy_result_value(item) for item in value]
    if type(value) in (str, int, bool) or value is None:
        return value
    raise _PortfolioExecutionIntegrityError(
        f"Unsupported M1B result snapshot value: {type(value).__name__}."
    )


def _require_record_tuple(value: object, label: str) -> tuple[Mapping[str, object], ...]:
    items = _require_tuple(value, label)
    return tuple(_require_mapping(item, f"{label}[{index}]") for index, item in enumerate(items))


def _copy_result_mapping(value: Mapping[str, object]) -> dict[str, object]:
    return {key: _copy_result_value(item) for key, item in value.items()}


def _copy_result_records(
    values: tuple[Mapping[str, object], ...],
) -> tuple[Mapping[str, object], ...]:
    return tuple(_copy_result_mapping(value) for value in values)


def _copy_result_string_record(value: Mapping[str, object], label: str) -> dict[str, str]:
    copied: dict[str, str] = {}
    for key, item in value.items():
        if type(item) is not str:
            raise _PortfolioExecutionIntegrityError(
                f"{label}.{key} must be text for the M1B daily-metrics builder."
            )
        copied[key] = item
    return copied


def _validate_result_tree(value: object, label: str) -> None:
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise _PortfolioExecutionIntegrityError(f"{label} mapping keys must be strings.")
        for key, item in value.items():
            _validate_result_tree(item, f"{label}.{key}")
    elif isinstance(value, (tuple, list)):
        for index, item in enumerate(value):
            _validate_result_tree(item, f"{label}[{index}]")
    elif type(value) in (str, int, bool) or value is None:
        return
    else:
        raise _PortfolioExecutionIntegrityError(
            f"{label} contains unsupported {type(value).__name__} data."
        )


def _require_mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(type(key) is not str for key in value):
        raise _PortfolioExecutionIntegrityError(f"{label} must be a string-keyed mapping.")
    return value


def _require_keys(
    value: Mapping[str, object], expected: set[str] | frozenset[str], label: str
) -> None:
    actual = set(value)
    if actual != set(expected):
        missing = sorted(set(expected) - actual)
        unexpected = sorted(actual - set(expected))
        raise _PortfolioExecutionIntegrityError(
            f"{label} has an invalid schema; missing={missing}, unexpected={unexpected}."
        )


def _require_tuple(value: object, label: str) -> tuple[object, ...]:
    if type(value) is not tuple:
        raise _PortfolioExecutionIntegrityError(f"{label} must be an immutable tuple.")
    return value


def _require_text(value: object, label: str) -> str:
    if type(value) is not str or not value.strip():
        raise _PortfolioExecutionIntegrityError(f"{label} must be non-empty text.")
    return value


def _require_int(value: object, label: str, *, minimum: int | None = None) -> int:
    if type(value) is not int or minimum is not None and value < minimum:
        raise _PortfolioExecutionIntegrityError(f"{label} must be an exact integer.")
    return value


def _require_decimal(
    value: object,
    label: str,
    *,
    positive: bool = False,
    nonnegative: bool = False,
) -> Decimal:
    if type(value) is not str or not value or len(value) > 4096:
        raise _PortfolioExecutionIntegrityError(f"{label} must be canonical decimal text.")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise _PortfolioExecutionIntegrityError(f"{label} must be canonical decimal text.") from exc
    if (
        not result.is_finite()
        or backtesting._decimal_text(result) != value
        or positive
        and result <= 0
        or nonnegative
        and result < 0
    ):
        raise _PortfolioExecutionIntegrityError(
            f"{label} must be finite canonical decimal text with valid sign."
        )
    return result


def _require_integer_text(value: object, label: str, *, minimum: int) -> int:
    decimal_value = _require_decimal(value, label, nonnegative=minimum >= 0)
    if decimal_value != decimal_value.to_integral_value():
        raise _PortfolioExecutionIntegrityError(f"{label} must be integer text.")
    parsed = int(decimal_value)
    if parsed < minimum:
        raise _PortfolioExecutionIntegrityError(f"{label} is below its minimum.")
    return parsed


def _require_hash(value: object, label: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise _PortfolioExecutionIntegrityError(f"{label} must be a 64-character SHA-256.")
    return value


def _is_sha256(value: object) -> bool:
    return type(value) is str and _SHA256_RE.fullmatch(value) is not None


def _require_datetime(value: object, label: str) -> None:
    _parse_datetime(value, label)


def _parse_datetime(value: object, label: str) -> datetime:
    if type(value) is not str or not value:
        raise _PortfolioExecutionIntegrityError(f"{label} must be an ISO timestamp.")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise _PortfolioExecutionIntegrityError(f"{label} must be an ISO timestamp.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise _PortfolioExecutionIntegrityError(f"{label} must include a timezone.")
    return parsed


def _require_date(value: object, label: str) -> None:
    if type(value) is not str:
        raise _PortfolioExecutionIntegrityError(f"{label} must be an ISO date.")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise _PortfolioExecutionIntegrityError(f"{label} must be an ISO date.") from exc
    if parsed.isoformat() != value:
        raise _PortfolioExecutionIntegrityError(f"{label} must use canonical ISO date text.")
