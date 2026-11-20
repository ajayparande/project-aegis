"""Bounded, explicitly approved market-data connectivity; offline by default.

This wrapper never exposes order APIs. Live quotes remain OBSERVE_ONLY. Historical
execution is separately labelled and delegated to existing qualification/execution.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from uuid import uuid4

from aegis.backtesting import zero_cost_assumptions
from aegis.broker_credentials import BrokerCredentialError, BrokerCredentials
from aegis.config import Settings
from aegis.database import get_readonly_connection, initialize_database
from aegis.forward_runtime import DeterministicSyntheticQuoteProvider, ForwardRuntimeService
from aegis.instruments import InstrumentInput, register_instrument
from aegis.market_quality import load_approved_calendar_registry, load_calendar_registry
from aegis.paper_trading import RiskLimits, create_paper_account, get_paper_account_report
from aegis.providers.collection_store import _atomic_new_file, _has_reparse_ancestor
from aegis.providers.upstox_http import UpstoxHistoricalHTTPClient
from aegis.providers.upstox_market_stream import (
    MarketObservation,
    ObservationResult,
    StreamControl,
    StreamMode,
    StreamStopped,
    UpstoxMarketStreamAdapter,
    UpstoxMarketStreamClient,
    UpstoxStreamError,
    feed_message_class,
)
from aegis.upstox_research_session import collect_and_attempt_simulation

_KEY = re.compile(r"NSE_EQ\|IN[A-Z0-9]{10}\Z")
_DEMO_KEY = "NSE_EQ|IN0000000001"
_START = datetime(2026, 1, 5, 3, 45, tzinfo=UTC)


@dataclass(frozen=True)
class ObservationConfig:
    """Conservative immutable limits; approval and secrets are invocation arguments."""

    root: Path
    mode: StreamMode = StreamMode.OFFLINE_TEST
    max_seconds: int = 60
    max_observations: int = 300
    max_reconnects: int = 3
    max_frames: int = 10_000
    connectivity_only: bool = True
    historical_from: date | None = None
    historical_to: date | None = None
    data_use_reviewed: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", StreamMode(self.mode))
        if self.mode == StreamMode.PAPER_RESEARCH:
            raise ValueError("PAPER_RESEARCH remains disabled.")
        StreamControl(self.max_seconds, self.max_observations)
        if type(self.max_reconnects) is not int or not 0 <= self.max_reconnects <= 3:
            raise ValueError("Reconnect bound must be between zero and three.")
        if type(self.max_frames) is not int or not 1 <= self.max_frames <= 10_000:
            raise ValueError("Frame bound must be between one and 10000.")
        if not isinstance(self.root, Path) or type(self.connectivity_only) is not bool:
            raise ValueError("An explicit new output root and valid observation mode are required.")
        if type(self.data_use_reviewed) is not bool:
            raise ValueError("data_use_reviewed must be a boolean.")
        if (self.historical_from is None) != (self.historical_to is None):
            raise ValueError("Historical dates must be supplied together.")
        if (
            self.historical_from is not None
            and self.historical_to is not None
            and not 0 <= (self.historical_to - self.historical_from).days < 7
        ):
            raise ValueError("Historical sample must span one to seven calendar days.")


class _Latency:
    """Fixed-size microsecond histogram; quantiles are explicit bucket upper bounds."""

    bounds = (100, 500, 1000, 5000, 10000, 50000, 100000, 500000, 1000000, 5000000, 60000000)

    def __init__(self) -> None:
        self.buckets = [0] * (len(self.bounds) + 1)
        self.count = self.total = self.maximum = 0

    def add(self, value: timedelta) -> None:
        micros = max(0, value // timedelta(microseconds=1))
        self.count += 1
        self.total += micros
        self.maximum = max(self.maximum, micros)
        index = next((n for n, b in enumerate(self.bounds) if micros <= b), len(self.bounds))
        self.buckets[index] += 1

    def report(self) -> dict[str, Any]:
        quantiles = {}
        for label, numerator in (("p50", 50), ("p95", 95), ("p99", 99)):
            target = (self.count * numerator + 99) // 100
            cumulative = 0
            upper = None
            for index, count in enumerate(self.buckets):
                cumulative += count
                if target and cumulative >= target:
                    upper = self.bounds[index] if index < len(self.bounds) else None
                    break
            quantiles[label + "_bucket_upper_us"] = upper
        return {
            "count": self.count,
            "mean_us": self.total // self.count if self.count else None,
            "max_us": self.maximum if self.count else None,
            **quantiles,
        }


def _zero_execution(database: Path, runtime: ForwardRuntimeService) -> bool:
    with closing(get_readonly_connection(database)) as connection:
        connection.execute("BEGIN")
        for table in (
            "paper_orders",
            "paper_fills",
            "paper_marks",
            "paper_positions",
            "paper_ledger_entries",
            "paper_realized_pnl",
            "forward_scheduler_events",
            "forward_execution_authorizations",
            "paper_execution_snapshot_uses",
            "forward_trade_controls",
        ):
            if connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]:
                return False
        if connection.execute(
            "SELECT COUNT(*) FROM decision_journal_events WHERE state IN ('BUY','SELL','HOLD')"
        ).fetchone()[0]:
            return False
        config = connection.execute("SELECT config_json FROM forward_runtimes").fetchone()
        if not config or json.loads(config[0]).get("observation_only") is not True:
            return False
    report = get_paper_account_report(database, runtime.account_id)
    reconciliation = report["reconciliation"]
    return (
        runtime.observation_only
        and isinstance(reconciliation, dict)
        and reconciliation.get("status") == "RECONCILED"
        and (
            Decimal(str(report["available_cash"]))
            == Decimal(str(report["initial_cash"]))
            == Decimal(10000)
        )
    )


def _demo_frames() -> Iterable[tuple[datetime, bytes]]:
    """Translate the existing synthetic provider into official binary fixture messages."""
    message_class = feed_message_class()
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    status = message_class(type=2, currentTs=(_START - epoch) // timedelta(milliseconds=1))
    status.marketInfo.segmentStatus["NSE_EQ"] = 2
    yield _START, status.SerializeToString()
    provider = DeterministicSyntheticQuoteProvider(
        (1,), start_at=_START, base_prices={1: Decimal(100)}
    )
    for index, quote in enumerate(provider.quotes(8)):
        at = datetime.fromisoformat(quote.timestamp)
        if index == 3:
            continue  # Deliberate missing minute, never fabricated by the runtime.
        message = message_class(
            type=0 if index == 0 else 1, currentTs=(at - epoch) // timedelta(milliseconds=1)
        )
        feed = message.feeds[_DEMO_KEY].ltpc
        feed.ltp, feed.ltt, feed.ltq = float(quote.price), message.currentTs, quote.quantity
        yield at + timedelta(milliseconds=5), message.SerializeToString()


def run_observation(
    config: ObservationConfig,
    *,
    instrument_key: str | None = None,
    instrument: InstrumentInput | None = None,
    operator_authorized: bool = False,
    credential: Callable[[], str] | None = None,
    client_factory: Callable[..., UpstoxMarketStreamClient] = UpstoxMarketStreamClient,
    historical_factory: Callable[..., UpstoxHistoricalHTTPClient] = UpstoxHistoricalHTTPClient,
    wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    monotonic: Callable[[], float] = time.monotonic,
    offline_frames: Iterable[tuple[datetime, bytes]] | None = None,
) -> dict[str, Any]:
    """Run one isolated session. Never read credentials before per-invocation approval."""
    session_id = uuid4().hex
    started = wall_clock()
    report: dict[str, Any] = {
        "session_id": session_id,
        "mode": config.mode.value,
        "application_mode": "RESEARCH_ONLY",
        "started_at": started.isoformat(),
        "protocol": "UPSTOX_V3",
        "instrument_count": 0,
        "status": "CREATED",
        "lifecycle": ["CREATED"],
        "reason_codes": [],
        "database_classification": "NOT_CREATED",
        "zero_execution": "NOT_EVALUATED",
        "verification_scope": "OFFLINE_SIMULATION"
        if config.mode == StreamMode.OFFLINE_TEST
        else "REAL_TRANSPORT",
        "authorization": "NOT_ATTEMPTED",
        "websocket_handshake": "NOT_ATTEMPTED",
        "market_status": "NOT_DECODED",
        "snapshot": "NOT_DECODED",
        "historical": {"status": "NOT_REQUESTED"},
        "trading_readiness": "NOT_AUTHORIZED",
        "data_quality_eligibility": "UNVERIFIED",
        "calendar_confidence": "UNAVAILABLE",
        "limitations": [
            "Observed data is not authorized for paper execution.",
            "Historical replay requires independent calendar and source-use eligibility.",
            "LTPC quantities are sampled, not authoritative exchange volume.",
        ],
    }
    counters: Counter[str] = Counter()
    rejected: Counter[str] = Counter()
    latency, processing = _Latency(), _Latency()
    runtime = None
    owned = False
    database = config.root / "observation.db"
    token = ""

    def transition(state: str) -> None:
        report["status"] = state
        if report["lifecycle"][-1] != state and len(report["lifecycle"]) < 64:
            report["lifecycle"].append(state)

    def checkpoint() -> None:
        if runtime is not None and not _zero_execution(database, runtime):
            report["zero_execution"] = "FAILED"
            raise StreamStopped("SAFETY_VIOLATION")

    def event(code: str) -> None:
        counters[code] += 1
        if code == "CONNECTION_ATTEMPT":
            transition("CONNECTING")
        elif code == "SUBSCRIBED":
            transition("SYNCHRONIZING")
        elif code == "SYNCHRONIZED":
            transition("OBSERVING")
        elif code == "MARKET_STATUS_DECODED":
            report["market_status"] = "DECODED"
        elif code == "SNAPSHOT_DECODED":
            report["snapshot"] = "DECODED"
        elif code == "AUTHORIZATION_PASSED":
            report["authorization"] = "SUCCESS"
        elif code == "CONNECTED":
            report["websocket_handshake"] = "SUCCESS"

    def observation(result: ObservationResult) -> None:
        outcome = result.runtime_result
        if outcome.duplicate:
            counters["DUPLICATE"] += 1
        elif outcome.accepted:
            counters["ACCEPTED"] += 1
            counters["MISSING_MINUTE_GAP"] += int(result.gap_detected)
        else:
            code = "OUT_OF_ORDER" if "Out-of-order" in str(outcome.reason) else "RUNTIME_VALIDATION"
            rejected[code] += 1
        latency.add(result.observation.received_at - result.observation.event_at)
        processing.add(result.observation.processing_started_at - result.observation.received_at)

    def diagnostic(observed: MarketObservation) -> None:
        if config.mode == StreamMode.OBSERVE_ONLY and config.connectivity_only:
            latency.add(observed.received_at - observed.event_at)
            processing.add(observed.processing_started_at - observed.received_at)

    try:
        root = config.root.absolute()
        protected = Path(tempfile.gettempdir()) / "aegis-p10p5-continuous"
        if (
            ".." in root.parts
            or root.exists()
            or _has_reparse_ancestor(root)
            or root.is_relative_to(protected)
        ):
            report["reason_codes"].append("EXISTING_OR_UNSAFE_OUTPUT_ROOT")
            transition("BLOCKED")
            return report
        root.mkdir(parents=True, exist_ok=False)
        owned = True
        Settings.from_env()  # Application mode can never become a trading mode.
        if config.mode == StreamMode.OBSERVE_ONLY:
            if operator_authorized is not True:
                report["reason_codes"].append("PER_INVOCATION_APPROVAL_REQUIRED")
                transition("BLOCKED_FOR_AUTHORIZATION")
                return report
            token = credential() if credential else ""
            if (
                not isinstance(token, str)
                or not token
                or len(token) > 8192
                or any(ord(c) < 33 or ord(c) > 126 for c in token)
            ):
                report["reason_codes"].append("OPERATOR_CREDENTIAL_UNAVAILABLE")
                transition("BLOCKED_FOR_AUTHORIZATION")
                return report
            if instrument is None or instrument_key is None:
                report["reason_codes"].append("EXPLICIT_INSTRUMENT_MAPPING_REQUIRED")
                transition("BLOCKED")
                return report
            calendars = load_approved_calendar_registry()
        else:
            from aegis.synthetic_paper_pilot import _create_test_calendar

            fixture_calendar = root / "synthetic_calendar.json"
            _create_test_calendar(fixture_calendar)
            calendars = load_calendar_registry(fixture_calendar)
            instrument_key = instrument_key or _DEMO_KEY
            instrument = instrument or InstrumentInput(
                "NSE",
                "SYNTH",
                "Offline observation fixture",
                "EQUITY",
                segment="NSE",
                isin=_DEMO_KEY.split("|")[1],
                tick_size=Decimal("0.05"),
            )
        if (
            _KEY.fullmatch(instrument_key) is None
            or instrument.exchange != "NSE"
            or instrument.segment not in ("NSE", "NSE_EQ")
            or instrument.instrument_type != "EQUITY"
            or instrument.isin != instrument_key.split("|")[1]
            or not instrument.is_active
        ):
            report["reason_codes"].append("INVALID_EXACT_INSTRUMENT_MAPPING")
            transition("BLOCKED")
            return report
        calendar = calendars.resolve(instrument.exchange, instrument.segment)
        if calendar is None:
            report["reason_codes"].append("CALENDAR_UNAVAILABLE")
            transition("BLOCKED")
            return report
        report["calendar_confidence"] = (
            calendar.confidence if config.mode == StreamMode.OBSERVE_ONLY else "CONFIRMED_TEST_ONLY"
        )
        if (
            config.mode == StreamMode.OBSERVE_ONLY
            and not config.connectivity_only
            and calendar.confidence != "CONFIRMED"
        ):
            report["reason_codes"].append("CALENDAR_NOT_CONFIRMED")
            transition("BLOCKED")
            return report
        # Connectivity-only can inspect a closed market or provisional calendar,
        # but it cannot persist quotes or authorize data-quality eligibility.
        initialize_database(database)
        registered = register_instrument(database, instrument)
        account = create_paper_account(
            database,
            name="isolated-observation",
            initial_cash=Decimal(10000),
            risk_limits=RiskLimits(
                Decimal(2000), 10, Decimal(3000), Decimal(8000), Decimal(200), 60
            ),
            costs=zero_cost_assumptions(),
            created_at=(_START if config.mode == StreamMode.OFFLINE_TEST else started).isoformat(),
        )
        runtime = ForwardRuntimeService.create(
            database,
            "observe-" + session_id,
            account,
            (registered.id,),
            calendars,
            observation_only=True,
            created_at=(_START if config.mode == StreamMode.OFFLINE_TEST else started).isoformat(),
        )
        report.update(database_classification="NEW_ISOLATED_SESSION", instrument_count=1)
        checkpoint()
        transition("PREFLIGHT_PASSED")
        simulated_time = _START
        adapter = UpstoxMarketStreamAdapter(
            runtime,
            {instrument_key: registered.id},
            mode=config.mode,
            clock=(lambda: simulated_time)
            if config.mode == StreamMode.OFFLINE_TEST
            else wall_clock,
            ingest_observations=config.mode == StreamMode.OFFLINE_TEST
            or not config.connectivity_only,
        )
        control = StreamControl(
            config.max_seconds,
            config.max_observations,
            monotonic,
            checkpoint,
            event,
            observation,
            decoded=diagnostic,
        )
        if config.mode == StreamMode.OFFLINE_TEST:
            control.start()
            transition("CONNECTING")
            adapter.connected()
            event("SUBSCRIBED")
            frames = offline_frames if offline_frames is not None else _demo_frames()
            try:
                for index, (received, frame) in enumerate(frames):
                    if index >= config.max_frames:
                        raise StreamStopped("FRAME_LIMIT")
                    simulated_time = received + timedelta(milliseconds=1)
                    control.check()
                    adapter.receive_frame(frame, received_at=received, control=control)
                    if adapter.synchronized and counters["SYNCHRONIZED"] == 0:
                        event("SYNCHRONIZED")
                control.stop_reason = "OFFLINE_INPUT_COMPLETE"
            except StreamStopped as stopped:
                control.stop_reason = stopped.reason
            finally:
                adapter.disconnected()
        else:
            client = client_factory(network_enabled=True, max_reconnects=config.max_reconnects)
            if client_factory is not UpstoxMarketStreamClient:
                report["verification_scope"] = "INJECTED_TRANSPORT"
            try:
                client.run(
                    adapter,
                    access_token=token,
                    operator_authorized=True,
                    max_frames=config.max_frames,
                    control=control,
                )
            except UpstoxStreamError:
                report["reason_codes"].append("STREAM_AUTHORIZATION_OR_TRANSPORT_FAILED")
                report["authorization"] = (
                    "FAILED" if not counters["AUTHORIZATION_PASSED"] else "SUCCESS"
                )
                transition("FAILED")
        transition("STOPPING") if report["status"] != "FAILED" else None
        checkpoint()
        report["zero_execution"] = "PASSED"
        report["disconnect_reason"] = control.stop_reason or "TRANSPORT_FAILURE"
        if control.stop_reason == "SAFETY_VIOLATION":
            report["zero_execution"] = "FAILED"
            transition("FAILED")
        elif report["status"] != "FAILED":
            transition("COMPLETED")
        if config.historical_from is not None and config.historical_to is not None:
            if config.mode != StreamMode.OBSERVE_ONLY or not config.data_use_reviewed:
                report["historical"] = {"status": "BLOCKED_FOR_DATA_USE_REVIEW"}
            else:
                research = root / "research.db"
                initialize_database(research)
                register_instrument(research, instrument)
                try:
                    report["historical"] = collect_and_attempt_simulation(
                        research,
                        root / "historical",
                        client=historical_factory(token, max_attempts=1),
                        instrument_key=instrument_key,
                        symbol=instrument.trading_symbol,
                        from_date=config.historical_from,
                        to_date=config.historical_to,
                        calendar_path=Settings.from_env().calendar_config_path,
                        operator_authorized=True,
                        data_use_reviewed=True,
                        as_of=wall_clock(),
                    )
                except Exception:  # noqa: BLE001 -- never expose provider bodies, headers or credentials
                    report["historical"] = {
                        "status": "FAILED_AUTHORIZATION_ENTITLEMENT_OR_DATA_VALIDATION"
                    }
    except BrokerCredentialError as error:
        report["reason_codes"].append(error.code)
        transition("BLOCKED_FOR_AUTHORIZATION")
    except KeyboardInterrupt:
        report["disconnect_reason"] = "OPERATOR_INTERRUPT"
        transition("INTERRUPTED")
    except StreamStopped as stopped:
        report["reason_codes"].append(stopped.reason)
        transition("FAILED" if stopped.reason == "SAFETY_VIOLATION" else "INTERRUPTED")
    except Exception:  # noqa: BLE001 -- sanitized report is the only external error surface
        report["reason_codes"].append("PREFLIGHT_OR_SESSION_VALIDATION_FAILED")
        transition("FAILED")
    finally:
        token = ""
        if runtime is not None:
            try:
                report["zero_execution"] = (
                    "PASSED" if _zero_execution(database, runtime) else "FAILED"
                )
            except Exception:  # noqa: BLE001 -- database failure must not create a false safety success
                report["zero_execution"] = "FAILED"
            if report["zero_execution"] == "FAILED":
                transition("FAILED")
        report.update(
            ended_at=wall_clock().isoformat(),
            counters=dict(counters),
            rejected_by_reason=dict(rejected),
            provider_latency=latency.report(),
            processing_latency=processing.report(),
        )
        report["offline_simulation_success"] = (
            config.mode == StreamMode.OFFLINE_TEST and report["status"] == "COMPLETED"
        )
        real = report["verification_scope"] == "REAL_TRANSPORT"
        report["real_handshake_verified"] = real and report["websocket_handshake"] == "SUCCESS"
        report["real_feed_observation_verified"] = real and counters["LTPC_UPDATE_DECODED"] > 0
        if owned:
            _atomic_new_file(
                config.root / "report.json",
                (json.dumps(report, sort_keys=True, indent=2) + "\n").encode(),
            )
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bounded Upstox observation; no broker orders.")
    parser.add_argument("--mode", choices=("OFFLINE_TEST", "OBSERVE_ONLY"), default="OFFLINE_TEST")
    parser.add_argument(
        "--root", type=Path, required=True, help="New, nonexistent isolated output directory"
    )
    parser.add_argument("--instrument-config", type=Path)
    parser.add_argument("--max-seconds", type=int, default=60)
    parser.add_argument("--max-observations", type=int, default=300)
    parser.add_argument("--max-reconnects", type=int, default=3)
    parser.add_argument(
        "--approve-market-data-only",
        "--approve-observe-only",
        dest="approve_observe_only",
        action="store_true",
        help="Approve bounded market data for this invocation only; never orders or paper execution",
    )
    parser.add_argument(
        "--confirm", action="store_true", help="Ask for local interactive observation approval"
    )
    parser.add_argument(
        "--token-env",
        help="Explicit operator-selected environment variable; never an approval gate",
    )
    parser.add_argument(
        "--ingest-observations", action="store_true", help="Requires CONFIRMED approved calendar"
    )
    parser.add_argument("--historical-from", type=date.fromisoformat)
    parser.add_argument("--historical-to", type=date.fromisoformat)
    parser.add_argument("--data-use-reviewed", action="store_true")
    args = parser.parse_args(argv)
    approved = args.approve_observe_only
    if args.mode == "OBSERVE_ONLY" and args.confirm and not approved and sys.stdin.isatty():
        approved = (
            input(
                "Type OBSERVE_ONLY to authorize this bounded market-data and requested historical session: "
            ).strip()
            == "OBSERVE_ONLY"
        )

    def credential() -> str:
        if args.token_env:
            return os.environ.get(args.token_env, "")
        return BrokerCredentials.from_env().upstox_access_token or ""

    instrument = None
    key = None
    try:
        if args.instrument_config and (approved or args.mode == "OFFLINE_TEST"):
            if args.instrument_config.stat().st_size > 8192:
                raise ValueError("Oversized instrument configuration")
            document = json.loads(args.instrument_config.read_text(encoding="utf-8"))
            if not isinstance(document, dict):
                raise ValueError("Instrument configuration must be an object")
            key = document.pop("instrument_key")
            if "tick_size" in document:
                if not isinstance(document["tick_size"], str):
                    raise ValueError("tick_size must be exact decimal text")
                document["tick_size"] = Decimal(document["tick_size"])
            instrument = InstrumentInput(**document)
        config = ObservationConfig(
            args.root,
            StreamMode(args.mode),
            args.max_seconds,
            args.max_observations,
            args.max_reconnects,
            connectivity_only=not args.ingest_observations,
            historical_from=args.historical_from,
            historical_to=args.historical_to,
            data_use_reviewed=args.data_use_reviewed,
        )
    except (ValueError, TypeError, OSError, KeyError, InvalidOperation):
        print(json.dumps({"status": "BLOCKED", "reason_codes": ["INVALID_LOCAL_CONFIGURATION"]}))
        return 2
    previous = signal.getsignal(signal.SIGTERM)

    def terminated(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, terminated)
    try:
        report = run_observation(
            config,
            instrument_key=key,
            instrument=instrument,
            operator_authorized=approved,
            credential=credential,
        )
    finally:
        signal.signal(signal.SIGTERM, previous)
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0 if report["status"] == "COMPLETED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
