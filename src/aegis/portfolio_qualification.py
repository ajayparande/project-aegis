"""Trusted all-or-none M2 portfolio qualification on one read-only snapshot."""

from __future__ import annotations

import inspect
import json
import threading
import weakref
from collections.abc import Mapping
from contextlib import closing
from dataclasses import dataclass, fields
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import FunctionType, MappingProxyType, ModuleType
from typing import Final, Literal

from aegis import backtesting, database, market_data, market_quality, portfolio, strategy_lab
from aegis.backtesting import CostAssumptions
from aegis.config import get_settings
from aegis.market_quality import DatasetQualityReport
from aegis.portfolio import (
    CapitalAllocation,
    PortfolioError,
    PortfolioPolicy,
    PortfolioRequest,
    SleeveRequest,
    allocate_equal_cash,
)
from aegis.strategy_lab import StrategyCandle, StrategySpec, _canonical_hash, _canonical_json

_BATCH_TOKEN: Final = object()
_BATCH_REGISTRY: dict[int, weakref.ReferenceType[_QualifiedBatch]] = {}
_BATCH_REGISTRY_LOCK = threading.RLock()


class PortfolioSnapshotChangedError(RuntimeError):
    """Raised when a database, calendar, or implementation source changes mid-qualification."""


@dataclass(frozen=True, slots=True)
class _BlockReason:
    sequence: int
    exchange: str
    trading_symbol: str
    category: str
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "sequence": self.sequence,
            "exchange": self.exchange,
            "trading_symbol": self.trading_symbol,
            "category": self.category,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class _UniverseMember:
    sequence: int
    exchange: str
    trading_symbol: str
    instrument_id: int
    segment: str
    instrument_type: str
    active: bool
    lot_size: int
    tick_size_decimal: str
    order_quantity: int
    strategy_spec_sha256: str
    instrument_snapshot_sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "sequence": self.sequence,
            "exchange": self.exchange,
            "trading_symbol": self.trading_symbol,
            "instrument_id": self.instrument_id,
            "segment": self.segment,
            "instrument_type": self.instrument_type,
            "active": self.active,
            "lot_size": self.lot_size,
            "tick_size_decimal": self.tick_size_decimal,
            "order_quantity": self.order_quantity,
            "strategy_spec_sha256": self.strategy_spec_sha256,
            "instrument_snapshot_sha256": self.instrument_snapshot_sha256,
        }


@dataclass(frozen=True, slots=True)
class _UniverseManifest:
    members: tuple[_UniverseMember, ...]
    manifest_sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "members": [member.to_dict() for member in self.members],
            "manifest_sha256": self.manifest_sha256,
        }


@dataclass(frozen=True, slots=True)
class _QualifiedSleeve:
    member: _UniverseMember
    strategy_spec: StrategySpec
    starting_cash: Decimal
    candles: tuple[StrategyCandle, ...]
    quality_report: DatasetQualityReport
    expected_by_timestamp: tuple[tuple[str, datetime], ...]
    source_imports: tuple[Mapping[str, object], ...]
    dataset_sha256: str
    source_file_sha256s: tuple[str, ...]
    candle_source_bindings_sha256: str
    effective_calendar_configuration_sha256: str
    expected_schedule_sha256: str


@dataclass(frozen=True, slots=True, weakref_slot=True)
class _QualifiedBatch:
    """Internal coordinator output valid while its exact value remains retained.

    Supported callers provide only a PortfolioRequest to the coordinator. Python
    module-private names are accessible to in-process code, so every batch is also
    checked for internal consistency before it can be used downstream. Registration
    does not consume a batch; repeated validation remains valid while callers retain it.
    """

    request: PortfolioRequest
    allocation: CapitalAllocation
    manifest: _UniverseManifest
    sleeves: tuple[_QualifiedSleeve, ...]
    provenance_json: str
    fingerprint_sha256: str
    _token: object

    def __post_init__(self) -> None:
        if self._token is not _BATCH_TOKEN:
            raise TypeError(
                "Qualified portfolio batches are created only by the trusted coordinator."
            )
        if (
            type(self.request) is not PortfolioRequest
            or type(self.allocation) is not CapitalAllocation
            or type(self.manifest) is not _UniverseManifest
            or type(self.sleeves) is not tuple
            or type(self.manifest.members) is not tuple
        ):
            raise TypeError("A qualified batch must contain immutable coordinator values.")
        if (
            len(self.sleeves) != len(self.manifest.members)
            or len(self.sleeves) != len(self.request.sleeves)
            or len(self.sleeves) != len(self.allocation.sleeves)
            or not self.sleeves
        ):
            raise ValueError("A qualified batch must contain every request member exactly once.")
        if self.allocation != allocate_equal_cash(self.request):
            raise ValueError("Qualified batch allocation does not match its request.")
        for index, (member, sleeve_request, allocation, sleeve) in enumerate(
            zip(
                self.manifest.members,
                self.request.sleeves,
                self.allocation.sleeves,
                self.sleeves,
                strict=True,
            )
        ):
            if (
                type(member) is not _UniverseMember
                or type(sleeve) is not _QualifiedSleeve
                or member.sequence != index
                or (member.exchange, member.trading_symbol)
                != (sleeve_request.exchange, sleeve_request.trading_symbol)
                or member.order_quantity != sleeve_request.order_quantity
                or member.strategy_spec_sha256 != _canonical_hash(sleeve_request.strategy.to_dict())
                or sleeve.member != member
                or sleeve.strategy_spec != sleeve_request.strategy
                or sleeve.starting_cash != allocation.cash
                or type(sleeve.candles) is not tuple
                or type(sleeve.expected_by_timestamp) is not tuple
                or type(sleeve.source_imports) is not tuple
                or not isinstance(sleeve.quality_report, DatasetQualityReport)
            ):
                raise ValueError("Qualified sleeve evidence does not match its frozen request.")
        expected_manifest_hash = _canonical_hash(
            [member.to_dict() for member in self.manifest.members]
        )
        if self.manifest.manifest_sha256 != expected_manifest_hash:
            raise ValueError("Qualified batch manifest fingerprint does not match its members.")
        try:
            provenance = json.loads(self.provenance_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("Qualified batch provenance must be valid canonical JSON.") from exc
        if not isinstance(provenance, dict) or _canonical_json(provenance) != self.provenance_json:
            raise ValueError("Qualified batch provenance must be a canonical JSON object.")
        if self.fingerprint_sha256 != _canonical_hash(provenance):
            raise ValueError("Qualified batch provenance fingerprint does not match its payload.")
        if (
            provenance.get("request") != self.request.to_dict()
            or provenance.get("allocation") != self.allocation.to_dict()
            or provenance.get("universe_manifest") != self.manifest.to_dict()
            or provenance.get("sleeves")
            != [_qualified_sleeve_provenance(sleeve) for sleeve in self.sleeves]
        ):
            raise ValueError("Qualified batch provenance does not match its frozen evidence.")
        _validate_required_implementation_fingerprints(provenance)
        _assert_no_float(provenance)

    def provenance_document(self) -> dict[str, object]:
        value = json.loads(self.provenance_json)
        if not isinstance(value, dict):
            raise TypeError("Qualified batch provenance must be a JSON object.")
        return value


def _validate_required_implementation_fingerprints(provenance: Mapping[str, object]) -> None:
    implementation_hashes = provenance.get("implementation_source_sha256")
    if not isinstance(implementation_hashes, dict):
        raise TypeError("Qualified batch provenance is missing its implementation fingerprint map.")
    expected = _implementation_fingerprints()
    if set(implementation_hashes) != set(expected):
        missing = sorted(set(expected) - set(implementation_hashes))
        unexpected = sorted(set(implementation_hashes) - set(expected))
        details = []
        if missing:
            details.append(f"missing={missing}")
        if unexpected:
            details.append(f"unexpected={unexpected}")
        raise ValueError(
            "Qualified batch provenance does not contain the complete required implementation fingerprint set: "
            + "; ".join(details)
        )
    for name, expected_hash in expected.items():
        actual_hash = implementation_hashes.get(name)
        if not _is_sha256(actual_hash) or actual_hash != expected_hash:
            raise ValueError(
                f"Qualified batch provenance mismatch for implementation fingerprint {name!r}."
            )


def _register_qualified_batch(batch: _QualifiedBatch) -> _QualifiedBatch:
    if type(batch) is not _QualifiedBatch:
        raise TypeError(
            "Qualified portfolio batches are registered only by the trusted coordinator."
        )
    batch_id = id(batch)

    def remove_expired(reference: weakref.ReferenceType[_QualifiedBatch]) -> None:
        with _BATCH_REGISTRY_LOCK:
            if _BATCH_REGISTRY.get(batch_id) is reference:
                del _BATCH_REGISTRY[batch_id]

    batch_reference = weakref.ref(batch, remove_expired)
    with _BATCH_REGISTRY_LOCK:
        _BATCH_REGISTRY[batch_id] = batch_reference
    return batch


def _validate_coordinator_batch(batch: _QualifiedBatch) -> _QualifiedBatch:
    if type(batch) is not _QualifiedBatch:
        raise TypeError("Qualified batch must be an internal coordinator value.")
    with _BATCH_REGISTRY_LOCK:
        batch_reference = _BATCH_REGISTRY.get(id(batch))
        if batch_reference is None or batch_reference() is not batch:
            raise ValueError("Qualified batch is not the exact coordinator-registered batch.")
    provenance = batch.provenance_document()
    _validate_required_implementation_fingerprints(provenance)
    return batch


@dataclass(frozen=True, slots=True)
class _PortfolioQualification:
    """Private coordinator status; its token is not an in-process security boundary."""

    status: Literal["QUALIFIED", "BLOCKED"]
    block_reasons: tuple[_BlockReason, ...]
    qualified_batch: _QualifiedBatch | None
    _token: object

    def __post_init__(self) -> None:
        if self._token is not _BATCH_TOKEN:
            raise TypeError(
                "Portfolio qualification outcomes are created only by the trusted coordinator."
            )
        if self.status not in ("QUALIFIED", "BLOCKED"):
            raise ValueError("Portfolio qualification status is invalid.")
        if type(self.block_reasons) is not tuple or any(
            type(reason) is not _BlockReason for reason in self.block_reasons
        ):
            raise TypeError("Portfolio block reasons must be immutable coordinator values.")
        if self.qualified_batch is not None and type(self.qualified_batch) is not _QualifiedBatch:
            raise TypeError("Qualified outcome must contain a validated internal batch.")
        if (self.status == "QUALIFIED") != (self.qualified_batch is not None):
            raise ValueError("Only a fully qualified outcome may contain a qualified batch.")
        if self.status == "QUALIFIED":
            if self.qualified_batch is None:
                raise ValueError("Only a fully qualified outcome may contain a qualified batch.")
            _validate_coordinator_batch(self.qualified_batch)
        if self.status == "QUALIFIED" and self.block_reasons:
            raise ValueError("A qualified outcome cannot contain block reasons.")
        if self.status == "BLOCKED" and not self.block_reasons:
            raise ValueError("A blocked outcome must contain at least one reason.")


def _immutable_request(request: PortfolioRequest) -> PortfolioRequest:
    if type(request) is not PortfolioRequest:
        raise PortfolioError("request must be an exact PortfolioRequest value.")
    if (
        any(type(sleeve) is not SleeveRequest for sleeve in request.sleeves)
        or type(request.policy) is not PortfolioPolicy
        or type(request.costs) is not CostAssumptions
    ):
        raise PortfolioError("Portfolio request members must use the exact immutable contracts.")
    try:
        sleeves = tuple(
            SleeveRequest(
                exchange=sleeve.exchange,
                trading_symbol=sleeve.trading_symbol,
                strategy=sleeve.strategy,
                order_quantity=sleeve.order_quantity,
            )
            for sleeve in request.sleeves
        )
        policy = PortfolioPolicy(
            **{
                policy_field.name: getattr(request.policy, policy_field.name)
                for policy_field in fields(PortfolioPolicy)
            }
        )
        costs = CostAssumptions(
            **{
                cost_field.name: getattr(request.costs, cost_field.name)
                for cost_field in fields(CostAssumptions)
            }
        )
        return PortfolioRequest(
            total_capital=request.total_capital,
            sleeves=sleeves,
            start_date=request.start_date,
            end_date=request.end_date,
            as_of=request.as_of,
            costs=costs,
            max_volume_participation=request.max_volume_participation,
            policy=policy,
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise PortfolioError(f"Portfolio request failed trusted revalidation: {exc}") from exc


def _implementation_fingerprints() -> dict[str, str]:
    sources: dict[str, object] = {
        "portfolio_qualification": Path(__file__).resolve(),
        "portfolio_contracts_and_allocation": Path(portfolio.__file__).resolve(),
        "backtesting_qualification": backtesting._qualify_one_instrument,
        "backtesting_instrument_resolution": backtesting._resolve_execution_instrument,
        "market_quality": market_quality.analyze_dataset,
        "calendar_loading": market_quality.load_approved_calendar_registry,
        "strategy_candles_and_source_use": strategy_lab._load_completed_candles,
        "source_use_validation": strategy_lab._validate_source_imports,
        "market_data_import": market_data.import_ohlcv_csv,
        "readonly_database_connection": database.get_readonly_connection,
        "settings_resolution": get_settings,
    }
    return {
        name: strategy_lab._sha256_file(_source_path(source))
        for name, source in sorted(sources.items())
    }


def _source_path(source: object) -> Path:
    if isinstance(source, Path):
        return source
    if isinstance(source, (FunctionType, ModuleType)):
        return Path(inspect.getfile(source)).resolve()
    raise TypeError(f"Cannot locate implementation source for {type(source).__name__}.")


def _registry_document(registry: market_quality.CalendarRegistry) -> dict[str, object]:
    def session_document(session: market_quality.SessionWindow) -> dict[str, str]:
        return {"opens": session.opens.isoformat(), "closes": session.closes.isoformat()}

    return {
        "configuration_sha256": registry.configuration_sha256,
        "staleness_policy": {
            "potential_after_missing": registry.staleness_policy.potential_after_missing,
            "stale_after_missing": registry.staleness_policy.stale_after_missing,
        },
        "calendars": [
            {
                "exchange": item.exchange,
                "segment": item.segment,
                "timezone": item.timezone_name,
                "confidence": item.confidence,
                "coverage_start": item.coverage_start.isoformat(),
                "coverage_end": item.coverage_end.isoformat(),
                "source": item.source,
                "weekdays": sorted(item.weekdays),
                "sessions": [session_document(session) for session in item.sessions],
                "holidays": [
                    {"date": day.isoformat(), "reason": reason}
                    for day, reason in sorted(item.holidays.items())
                ],
                "special_sessions": [
                    {
                        "date": day.isoformat(),
                        "reason": special.reason,
                        **(
                            {"closed": True}
                            if isinstance(special, market_quality.SpecialSessionClosure)
                            else {
                                "sessions": [
                                    session_document(session) for session in special.sessions
                                ]
                            }
                        ),
                    }
                    for day, special in sorted(item.special_sessions.items())
                ],
            }
            for item in registry.calendars
        ],
    }


def _manifest_for(
    request: PortfolioRequest,
    instruments: tuple[backtesting._ExecutionInstrument, ...],
) -> _UniverseManifest:
    members: list[_UniverseMember] = []
    for sequence, (sleeve, instrument) in enumerate(zip(request.sleeves, instruments, strict=True)):
        if (
            instrument.exchange != sleeve.exchange
            or instrument.trading_symbol != sleeve.trading_symbol
            or instrument.instrument_id is None
        ):
            raise RuntimeError("Resolved instrument identity does not match frozen request order.")
        strategy_hash = _canonical_hash(sleeve.strategy.to_dict())
        instrument_document = {
            "instrument_id": instrument.instrument_id,
            "exchange": instrument.exchange,
            "trading_symbol": instrument.trading_symbol,
            "segment": instrument.segment,
            "instrument_type": instrument.instrument_type,
            "active": instrument.is_active,
            "lot_size": instrument.lot_size,
            "tick_size_decimal": backtesting._decimal_text(instrument.tick_size_decimal),
            "tick_size_is_authoritative": instrument.tick_size_is_authoritative,
        }
        members.append(
            _UniverseMember(
                sequence=sequence,
                exchange=instrument.exchange,
                trading_symbol=instrument.trading_symbol,
                instrument_id=instrument.instrument_id,
                segment=instrument.segment,
                instrument_type=instrument.instrument_type,
                active=instrument.is_active,
                lot_size=instrument.lot_size,
                tick_size_decimal=backtesting._decimal_text(instrument.tick_size_decimal),
                order_quantity=sleeve.order_quantity,
                strategy_spec_sha256=strategy_hash,
                instrument_snapshot_sha256=_canonical_hash(instrument_document),
            )
        )
    payload = [member.to_dict() for member in members]
    return _UniverseManifest(tuple(members), _canonical_hash(payload))


def _report_document(report: DatasetQualityReport) -> dict[str, object]:
    return {
        report_field.name: backtesting._evidence_json_value(getattr(report, report_field.name))
        for report_field in fields(report)
    }


def _qualified_sleeve_provenance(sleeve: _QualifiedSleeve) -> dict[str, object]:
    return {
        "sequence": sleeve.member.sequence,
        "identity": f"{sleeve.member.exchange}:{sleeve.member.trading_symbol}",
        "strategy_spec_sha256": sleeve.member.strategy_spec_sha256,
        "instrument_snapshot_sha256": sleeve.member.instrument_snapshot_sha256,
        "quality_report": _report_document(sleeve.quality_report),
        "dataset_sha256": sleeve.dataset_sha256,
        "source_file_sha256s": list(sleeve.source_file_sha256s),
        "source_imports": [dict(item) for item in sleeve.source_imports],
        "candle_source_bindings_sha256": sleeve.candle_source_bindings_sha256,
        "effective_calendar_configuration_sha256": (sleeve.effective_calendar_configuration_sha256),
        "expected_schedule_sha256": sleeve.expected_schedule_sha256,
    }


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _freeze_source_imports(
    source_imports: tuple[dict[str, object], ...],
) -> tuple[Mapping[str, object], ...]:
    return tuple(
        MappingProxyType({key: value for key, value in sorted(item.items())})
        for item in source_imports
    )


def _blocked(
    reasons: list[_BlockReason],
) -> _PortfolioQualification:
    ordered = tuple(sorted(reasons, key=lambda item: (item.sequence, item.category, item.reason)))
    return _PortfolioQualification("BLOCKED", ordered, None, _BATCH_TOKEN)


def _qualify_portfolio_request(
    database_path: Path, request: PortfolioRequest
) -> _PortfolioQualification:
    """Qualify all request sleeves against one approved registry and SQLite snapshot.

    This private coordinator accepts no calendar, instrument, candle, quality, provenance,
    or qualification-token inputs. The only successful outcome contains all qualified
    sleeves; any expected eligibility failure discards every sleeve qualification.
    """

    if not isinstance(database_path, Path):
        raise PortfolioError("database_path must be a pathlib Path.")
    frozen_request = _immutable_request(request)
    allocation = allocate_equal_cash(frozen_request)
    settings = get_settings()
    implementation_before = _implementation_fingerprints()
    calendars = backtesting._load_admin_calendar_registry(settings.calendar_config_path)
    registry_before = _canonical_hash(_registry_document(calendars))
    if calendars.configuration_sha256 is None or calendars.configuration_path is None:
        raise RuntimeError("Approved calendar loader returned an unbound registry.")

    resolution_failures: list[_BlockReason] = []
    qualified_sleeves: list[_QualifiedSleeve] = []
    manifest: _UniverseManifest | None = None
    schema_version: int | None = None
    with closing(backtesting._readonly_connection(database_path)) as connection:
        if not connection.execute("PRAGMA query_only").fetchone()[0]:
            raise RuntimeError("Portfolio qualification connection is not query-only.")
        data_version_before = int(connection.execute("PRAGMA data_version").fetchone()[0])
        connection.execute("BEGIN")
        if not connection.in_transaction:
            raise RuntimeError("Portfolio qualification did not establish a read transaction.")
        connection.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()
        schema_row = connection.execute(
            "SELECT MAX(version) AS version FROM schema_metadata"
        ).fetchone()
        if (
            schema_row is None
            or type(schema_row["version"]) is not int
            or schema_row["version"] < 1
        ):
            raise RuntimeError("Database schema metadata is missing or invalid.")
        schema_version = schema_row["version"]
        instruments: list[backtesting._ExecutionInstrument] = []
        for sequence, sleeve in enumerate(frozen_request.sleeves):
            try:
                instruments.append(
                    backtesting._resolve_execution_instrument(
                        connection,
                        exchange=sleeve.exchange,
                        trading_symbol=sleeve.trading_symbol,
                        order_quantity=sleeve.order_quantity,
                    )
                )
            except backtesting._QualificationFailure as exc:
                resolution_failures.append(
                    _BlockReason(
                        sequence,
                        sleeve.exchange,
                        sleeve.trading_symbol,
                        exc.category,
                        exc.reason,
                    )
                )

        if not resolution_failures:
            manifest = _manifest_for(frozen_request, tuple(instruments))
            for sequence, (sleeve, instrument, allocation_item) in enumerate(
                zip(frozen_request.sleeves, instruments, allocation.sleeves, strict=True)
            ):
                config = backtesting.BacktestConfig(
                    starting_cash=allocation_item.cash,
                    order_quantity=sleeve.order_quantity,
                    max_volume_participation=frozen_request.max_volume_participation,
                    costs=frozen_request.costs,
                )
                try:
                    qualified = backtesting._qualify_one_instrument(
                        database_path,
                        exchange=sleeve.exchange,
                        trading_symbol=sleeve.trading_symbol,
                        interval=sleeve.strategy.interval,
                        start_date=frozen_request.start_date,
                        end_date=frozen_request.end_date,
                        config=config,
                        as_of=frozen_request.as_of,
                        calendars=calendars,
                        connection=connection,
                        qualification_token=backtesting._QUALIFICATION_TOKEN,
                        strategy_spec=sleeve.strategy,
                        instrument=instrument,
                    )
                except backtesting._QualificationFailure as exc:
                    resolution_failures.append(
                        _BlockReason(
                            sequence,
                            sleeve.exchange,
                            sleeve.trading_symbol,
                            exc.category,
                            exc.reason,
                        )
                    )
                    continue
                member = manifest.members[sequence]
                qualified_sleeves.append(
                    _QualifiedSleeve(
                        member=member,
                        strategy_spec=qualified.strategy_spec,
                        starting_cash=allocation_item.cash,
                        candles=qualified.candles,
                        quality_report=qualified.eligibility.quality_report,
                        expected_by_timestamp=tuple(qualified.expected_by_timestamp.items()),
                        source_imports=_freeze_source_imports(qualified.eligibility.source_imports),
                        dataset_sha256=qualified.dataset_sha256,
                        source_file_sha256s=qualified.source_file_sha256s,
                        candle_source_bindings_sha256=qualified.candle_source_bindings_sha256,
                        effective_calendar_configuration_sha256=(
                            qualified.effective_calendar_configuration_sha256
                        ),
                        expected_schedule_sha256=qualified.expected_schedule_sha256,
                    )
                )

        connection.commit()
        data_version_after = int(connection.execute("PRAGMA data_version").fetchone()[0])
        if data_version_after != data_version_before:
            raise PortfolioSnapshotChangedError(
                "The SQLite database changed during portfolio qualification."
            )

    calendar_after = backtesting._reload_calendar_registry(calendars)
    registry_after = _canonical_hash(_registry_document(calendar_after))
    if registry_after != registry_before:
        raise PortfolioSnapshotChangedError("Calendar registry changed during qualification.")
    implementation_after = _implementation_fingerprints()
    if implementation_after != implementation_before:
        raise PortfolioSnapshotChangedError(
            "Qualification implementation changed during qualification."
        )

    if resolution_failures:
        return _blocked(resolution_failures)
    if manifest is None or len(qualified_sleeves) != len(frozen_request.sleeves):
        raise RuntimeError("Portfolio qualification did not produce an all-sleeve manifest.")

    sleeve_provenance = [_qualified_sleeve_provenance(sleeve) for sleeve in qualified_sleeves]
    provenance: dict[str, object] = {
        "schema_version": 1,
        "request": frozen_request.to_dict(),
        "allocation": allocation.to_dict(),
        "universe_manifest": manifest.to_dict(),
        "database": {
            "schema_version": schema_version,
            "snapshot_policy": "ONE_READ_ONLY_SQLITE_TRANSACTION",
        },
        "calendar_registry": {
            "configuration_sha256": calendars.configuration_sha256,
            "registry_sha256": registry_before,
            "calendar_path": str(calendars.configuration_path),
        },
        "implementation_source_sha256": implementation_before,
        "sleeves": sleeve_provenance,
    }
    _assert_no_float(provenance)
    provenance_json = _canonical_json(provenance)
    batch = _QualifiedBatch(
        request=frozen_request,
        allocation=allocation,
        manifest=manifest,
        sleeves=tuple(qualified_sleeves),
        provenance_json=provenance_json,
        fingerprint_sha256=_canonical_hash(provenance),
        _token=_BATCH_TOKEN,
    )
    batch = _register_qualified_batch(batch)
    return _PortfolioQualification("QUALIFIED", (), batch, _BATCH_TOKEN)


def _assert_no_float(value: object) -> None:
    if isinstance(value, float):
        raise TypeError("Portfolio qualification fingerprints cannot contain binary floats.")
    if isinstance(value, Mapping):
        for key, item in value.items():
            _assert_no_float(key)
            _assert_no_float(item)
    elif isinstance(value, (tuple, list)):
        for item in value:
            _assert_no_float(item)
