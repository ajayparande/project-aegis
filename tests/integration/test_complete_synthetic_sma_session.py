"""Complete offline SMA session: protection, gap causality, restart and audit."""

from __future__ import annotations

import json
import socket
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from aegis import decision_journal
from aegis.backtesting import default_cost_assumptions
from aegis.config import AppMode, Settings
from aegis.database import get_readonly_connection
from aegis.forward_paper_trading import ForwardPaperTradingEngine
from aegis.forward_runtime import ForwardRuntimeService
from aegis.paper_trading import get_paper_account_report, reconcile_paper_account
from aegis.strategy_lab import StrategyCandle, generate_sma_crossover_signals
from aegis.synthetic_sma_session import SMASessionConfig, run_existing_sma_runtime
from tests.test_forward_runtime import _provider, _setup


def _persisted(database: Path) -> dict[str, list[tuple[object, ...]]]:
    """Compare durable facts, excluding restart health notifications and mutable cursors."""
    with closing(get_readonly_connection(database)) as connection:
        return {
            table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY 1")]
            for table in (
                "paper_orders",
                "paper_fills",
                "paper_ledger_entries",
                "paper_positions",
                "paper_realized_pnl",
                "paper_execution_snapshot_uses",
                "forward_execution_authorizations",
                "forward_runtime_candles",
                "forward_scheduler_events",
                "decision_journal_events",
                "decision_journal_audits",
            )
        }


def test_complete_synthetic_sma_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run equivalent normal/crash sessions in fresh DBs with real nonzero execution costs."""

    def no_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("Synthetic session attempted network access")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setenv("AEGIS_MODE", "RESEARCH_ONLY")
    summaries = []
    original_route = ForwardPaperTradingEngine._route_runtime_proposal
    for crash in (False, True):
        root = tmp_path / str(crash) / "same-runtime"
        root.mkdir(parents=True)
        monkeypatch.setenv("AEGIS_DATABASE_PATH", str(root / "runtime.db"))
        monkeypatch.setenv("AEGIS_LOG_FILE", str(root / "session.log"))
        monkeypatch.setenv("AEGIS_CALENDAR_CONFIG_PATH", str(root / "runtime_calendar.json"))
        database, calendars, (instrument,), runtime = _setup(
            root, initial_cash=Decimal(10000), costs=default_cost_assumptions()
        )
        assert Settings.from_env().mode == AppMode.RESEARCH_ONLY
        # Reuse the deterministic provider's sequence/timing. Only synthetic prices
        # change; proposals and financial results are produced by existing engines.
        prices = (
            "100",
            "100",
            "100",
            "100",
            "99.90",
            "99.80",
            "100.10",
            "100.10",
            "100.20",
            "100.40",
            "99.90",
            "99.90",
            "99.90",
            "99.90",
            "99.90",
            "99.90",
        )
        quotes = tuple(
            replace(
                quote, price=Decimal(prices[minute]), quantity=2, event_id=f"sma-session-{minute}"
            )
            for minute, quote in enumerate(_provider((instrument,), count=len(prices)))
            if minute != 12
        )
        config = SMASessionConfig(
            buy_quantity=2,
            max_quotes=20,
            stop_loss=Decimal("99.50"),
            target_price=Decimal("100.30"),
        )
        if crash:
            # Reuse the existing F1 regression's supported post-commit boundary.
            def after_commit(self, *args, **kwargs):
                result = original_route(self, *args, **kwargs)
                if result.status == "FILLED":
                    raise RuntimeError("SMA_POST_COMMIT_CRASH")
                return result

            with monkeypatch.context() as patch:
                patch.setattr(ForwardPaperTradingEngine, "_route_runtime_proposal", after_commit)
                with pytest.raises(RuntimeError, match="SMA_POST_COMMIT_CRASH"):
                    run_existing_sma_runtime(runtime, quotes[:8], config)
            with closing(get_readonly_connection(database)) as connection:
                assert connection.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 1
                assert (
                    connection.execute("SELECT COUNT(*) FROM forward_trade_controls").fetchone()[0]
                    == 0
                )
                assert (
                    connection.execute(
                        "SELECT status FROM forward_scheduler_events WHERE json_extract(proposal_json,'$.action')='BUY'"
                    ).fetchone()[0]
                    == "PENDING"
                )
            assert (
                decision_journal.reconcile_journal(database, runtime.runtime_id)["status"]
                == "UNRESOLVED"
            )
        else:
            first = run_existing_sma_runtime(runtime, quotes[:8], config)
            assert first.quotes_accepted == 8
            assert first.account_reconciliation == first.journal_reconciliation == "RECONCILED"

        # Restart with an open position; committed BUY facts authenticate protection.
        runtime = ForwardRuntimeService.open(database, runtime.runtime_id, calendars)
        report = get_paper_account_report(database, runtime.account_id)
        assert Decimal(str(report["initial_cash"])) == Decimal(10000)
        assert Decimal(str(report["available_cash"])) == Decimal("9799.29")
        assert report["positions"][0]["quantity"] == 2
        with closing(get_readonly_connection(database)) as connection:
            buy = connection.execute(
                "SELECT * FROM forward_scheduler_events WHERE json_extract(proposal_json,'$.action')='BUY'"
            ).fetchone()
            assert buy["status"] == "FILLED" and buy["proposal_binding_state"] == "BOUND"
            canonical_buy = json.loads(buy["proposal_json"])
            assert canonical_buy["stop_loss"] == "99.5" and canonical_buy["target_price"] == "100.3"
            control = connection.execute("SELECT * FROM forward_trade_controls").fetchone()
            assert control["proposal_id"] == buy["proposal_id"]
            assert Decimal(control["stop_loss"]) == config.stop_loss
            assert Decimal(control["target_price"]) == config.target_price
            assert control["active"] == 1 and control["exit_event_id"] is None
            assert connection.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 1
        assert reconcile_paper_account(database, runtime.account_id)["status"] == "RECONCILED"
        assert decision_journal.reconcile_journal(database, runtime.runtime_id)["audit_complete"]

        # Observe a genuine HOLD with the protected position still open, then
        # reach the target on the following quote. The runtime generates the exit.
        held = run_existing_sma_runtime(runtime, quotes[8:9], config)
        assert held.protective_exits == 0
        assert held.decisions[-1].requested_action == held.decisions[-1].outcome_status == "HOLD"
        assert (
            get_paper_account_report(database, runtime.account_id)["positions"][0]["quantity"] == 2
        )
        second = run_existing_sma_runtime(runtime, quotes[9:12], config)
        assert second.protective_exits == 1
        assert second.account_reconciliation == second.journal_reconciliation == "RECONCILED"
        # Missing minute 12 is observed at minute 13. Its predecessor is finalized
        # late, never traded retrospectively. Later observed complete candles
        # can produce genuine HOLDs under the existing evidence contract.
        gap = run_existing_sma_runtime(runtime, quotes[12:], config)
        assert gap.quotes_accepted == 3 and gap.scheduled_events == 2
        assert all(
            d.requested_action == "HOLD" and d.outcome_status == "HOLD" and d.reason
            for d in gap.decisions
        )
        assert gap.account_reconciliation == gap.journal_reconciliation == "RECONCILED"

        with closing(get_readonly_connection(database)) as connection:
            candles = connection.execute(
                "SELECT * FROM forward_runtime_candles WHERE interval='1m' ORDER BY candle_timestamp"
            ).fetchall()
            times = [q.timestamp.replace("+00:00", "Z") for q in quotes]
            assert [c["candle_timestamp"] for c in candles] == times[:-1]
            assert [c["completed_at"] for c in candles] == times[1:]
            assert len(candles) == 14
            assert (
                connection.execute("SELECT COUNT(*) FROM forward_runtime_candles").fetchone()[0]
                == 16
            )
            assert connection.execute("SELECT COUNT(*) FROM historical_candles").fetchone()[0] == 16
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM forward_quotes WHERE accepted=1"
                ).fetchone()[0]
                == 15
            )
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM forward_runtime_candles WHERE interval IN ('5m','15m') AND candle_timestamp=?",
                    (times[10],),
                ).fetchone()[0]
                == 0
            )
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM forward_scheduler_events WHERE candle_timestamp=? AND interval='1m'",
                    (times[11],),
                ).fetchone()[0]
                == 0
            )
            missing = connection.execute(
                "SELECT payload_json FROM decision_journal_health WHERE kind='MISSING_OBSERVATION_INTERVAL'"
            ).fetchall()
            assert len(missing) == 1
            observation = json.loads(missing[0]["payload_json"])["details"]
            assert observation["missing_from"] == "2026-01-05T03:57:00Z"
            assert observation["missing_until_exclusive"] == times[12]
            assert observation["interval_boundary"] < times[12]
            missed = connection.execute(
                "SELECT decision_at FROM decision_journal_events WHERE decision_id=?",
                (observation["decision_id"],),
            ).fetchone()
            assert datetime.fromisoformat(missed[0]) == datetime.fromisoformat(
                times[12]
            ).astimezone(UTC)
            signals = generate_sma_crossover_signals(
                tuple(
                    StrategyCandle(
                        c["candle_timestamp"],
                        Decimal(c["open_decimal"]),
                        Decimal(c["high_decimal"]),
                        Decimal(c["low_decimal"]),
                        Decimal(c["close_decimal"]),
                        c["volume"],
                    )
                    for c in candles
                ),
                fast_window=2,
                slow_window=3,
            )
            assert [(s.action, s.timestamp) for s in signals] == [
                ("ENTER_LONG", times[6]),
                ("EXIT_LONG", times[10]),
            ]
            states = {
                r["status"]: r["total"]
                for r in connection.execute(
                    "SELECT status, COUNT(*) AS total FROM forward_scheduler_events GROUP BY status"
                )
            }
            assert states == {"FILLED": 2, "HOLD": 9, "REJECTED": 5}
            exit_event = connection.execute(
                "SELECT * FROM forward_scheduler_events WHERE proposal_id LIKE 'stop-target-%'"
            ).fetchone()
            trigger = json.loads(exit_event["proposal_json"])
            assert exit_event["status"] == "FILLED" and trigger["trigger"] == "TARGET"
            assert trigger["source_proposal_id"] == buy["proposal_id"]
            assert trigger["decision_timestamp"] == times[9]
            fills = connection.execute(
                """SELECT o.side, f.quantity, f.fill_price, f.fees_json FROM paper_fills f
                   JOIN paper_orders o USING(order_id) ORDER BY f.execution_timestamp"""
            ).fetchall()
            financial_fills = [
                (
                    f["side"],
                    f["quantity"],
                    Decimal(f["fill_price"]),
                    sum((Decimal(v) for v in json.loads(f["fees_json"]).values()), Decimal(0)),
                )
                for f in fills
            ]
            assert financial_fills == [
                ("BUY", 2, Decimal("100.20"), Decimal("0.31")),
                ("SELL", 2, Decimal("100.30"), Decimal("0.28")),
            ]
            assert connection.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 2
            assert connection.execute("SELECT COUNT(*) FROM paper_positions").fetchone()[0] == 0
            assert (
                connection.execute("SELECT active FROM forward_trade_controls").fetchone()[0] == 0
            )
            assert (
                connection.execute("SELECT status FROM forward_runtimes").fetchone()[0] == "RUNNING"
            )
            assert (
                connection.execute("SELECT COUNT(*) FROM paper_execution_snapshot_uses").fetchone()[
                    0
                ]
                == 2
            )
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM forward_execution_authorizations WHERE status='CONSUMED'"
                ).fetchone()[0]
                == 2
            )
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM decision_journal_audits WHERE stage='EXECUTION' AND fill_id IS NOT NULL"
                ).fetchone()[0]
                == 2
            )
            ledger = [
                Decimal(r[0]) for r in connection.execute("SELECT amount FROM paper_ledger_entries")
            ]
            assert sorted(ledger) == [Decimal("-200.71"), Decimal("200.32")]
            assert Decimal(10000) + sum(ledger) == Decimal("9999.61")

        report = get_paper_account_report(database, runtime.account_id)
        assert report["positions"] == []
        assert Decimal(str(report["available_cash"])) == Decimal("9999.61")
        assert Decimal(str(report["realized_net_pnl"])) == Decimal("-0.39")
        assert reconcile_paper_account(database, runtime.account_id)["status"] == "RECONCILED"
        journal = decision_journal.reconcile_journal(database, runtime.runtime_id)
        assert (
            journal["status"] == "RECONCILED" and journal["audit_complete"] and journal["complete"]
        )
        assert journal["requested_counts"] == {"BUY": 1, "SELL": 1, "HOLD": 9, "NO_DECISION": 8}
        assert journal["counts"] == {"FILLED": 2, "HOLD": 9, "NO_DECISION": 8}

        # Restarts and redelivery of the entire stream cannot replay financial effects,
        # invent gap candles or finalize a journal event a second time.
        before = _persisted(database)
        runtime = ForwardRuntimeService.open(database, runtime.runtime_id, calendars)
        replay = run_existing_sma_runtime(runtime, quotes, config)
        assert replay.quotes_accepted == replay.scheduled_events == replay.protective_exits == 0
        assert replay.decisions == ()
        assert replay.account_reconciliation == replay.journal_reconciliation == "RECONCILED"
        assert _persisted(database) == before
        summaries.append(
            (
                financial_fills,
                ledger,
                report["available_cash"],
                report["realized_net_pnl"],
                states,
                journal["counts"],
            )
        )
    assert summaries[0] == summaries[1]
