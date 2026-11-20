"""M5-A -> M5-B1 observation-only demonstration, no execution path."""

import os
import subprocess
import sys

from aegis.portfolio_pretrade import _canonical, _PortfolioPretradeCoordinator
from tests.test_portfolio_pretrade import _build, _dump


def test_fresh_restarted_reordered_and_replayed_reports(tmp_path, monkeypatch):
    results = []
    for name, restart, reverse in (
        ("fresh", False, False),
        ("restarted", True, False),
        ("reordered", False, True),
    ):
        db, _, _, request = _build(tmp_path / name, monkeypatch, restart=restart, reverse=reverse)
        before = _dump(db)
        report = _PortfolioPretradeCoordinator(db).evaluate(request)
        assert (report.accepted_count, report.blocked_count, report.hold_count) == (2, 0, 1)
        assert _PortfolioPretradeCoordinator(db).evaluate(request) == report
        assert _dump(db) == before
        results.append(report)
    assert results[0].canonical_json() == results[1].canonical_json() == results[2].canonical_json()
    assert len({r.fingerprint for r in results}) == 1
    print(results[0].canonical_json())


def test_new_interpreter_revalidates_same_database(tmp_path, monkeypatch):
    db, _, _, request = _build(tmp_path, monkeypatch, restart=True)
    before = _dump(db)
    expected = _PortfolioPretradeCoordinator(db).evaluate(request).canonical_json()
    script = """
import json, socket, sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path
def denied(*args, **kwargs):
    raise AssertionError("No network")
socket.socket.connect = denied
from aegis.portfolio_pretrade import (_PortfolioPretradeRequest, _PortfolioPretradeCoordinator,
    _EvidenceReference, _InstrumentPretradeIntent)
from aegis.portfolio import SleeveCashAllocation
from aegis.paper_trading import RiskLimits
from aegis.backtesting import CostAssumptions
d = json.loads(sys.argv[2])
d['evaluated_at'] = datetime.fromisoformat(d['evaluated_at'])
d['available_cash'] = Decimal(d['available_cash'])
d['universe'] = tuple(d['universe'])
d['risk_limits'] = RiskLimits(**{k: Decimal(v) if isinstance(v, str) else v for k,v in d['risk_limits'].items()})
d['costs'] = CostAssumptions(**{k: Decimal(v) for k,v in d['costs'].items()})
d['sleeve_budgets'] = tuple(SleeveCashAllocation(s['exchange'], s['trading_symbol'], Decimal(s['cash'])) for s in d['sleeve_budgets'])
d['intents'] = tuple(_InstrumentPretradeIntent(i['instrument'], i['action'], i['requested_quantity'], _EvidenceReference(**i['evidence'])) for i in d['intents'])
print(_PortfolioPretradeCoordinator(Path(sys.argv[1])).evaluate(_PortfolioPretradeRequest(**d)).canonical_json())
"""
    child = subprocess.run(
        [sys.executable, "-c", script, str(db), _canonical(request)],
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        check=True,
    )
    assert child.stdout.strip() == expected
    assert _dump(db) == before
