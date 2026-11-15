# Synthetic workflow integration test

Run from the repository root with the existing virtual environment:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests\integration\test_full_synthetic_trading_workflow.py
```

The test creates a fresh SQLite database, CSV, calendar fixture, log path, and
corruption copy under pytest's temporary directory. It uses generated OHLCV
values only. The fixture calendar is marked `SYNTHETIC_TEST_ONLY`; its API
confidence value is needed to exercise the same downstream gate as existing
component tests and is not evidence that the calendar reflects NSE sessions.
The importer has no synthetic-only permission enum, so the test uses its
existing audited `VERIFIED_PERMITTED` declaration API for the CSV it generated
itself, with a rationale limited to that fixture. This is not a declaration
about any real data source.

The test connects the existing instrument registry, CSV importer, quality
analysis, Strategy Lab, backtester, paper ledger, reconciliation service, and
Streamlit AppTest. Signal selection and conversion into an explicit paper-order
request happen only inside the test; no production strategy-to-order router is
introduced. Execution snapshots use independently supplied synthetic quotes.
Strategy Lab has no persisted-run getter, so the test reads the run JSON with a
read-only SQLite connection after validating the returned signal.

The integration test covers a complete seven-candle SMA crossover round trip,
reproducible signal generation, backtest provenance, zero-cost paper BUY/SELL
ledger accounting, duplicate submission after reopening the database,
reconciliation, and fail-closed detection of a copied account's tampered P&L
projection. Focused tests continue to cover negative cases not repeated here,
including invalid OHLC/timestamps, unknown or inactive instruments, unverified
sources, missing calendar coverage, incomplete/stale evidence, cash/exposure
limits, daily-loss halts, and missing/cross-linked/mismatched ledger records.
See `tests/test_market_data.py`, `tests/test_market_quality.py`,
`tests/test_strategy_lab.py`, `tests/test_backtesting.py`, and
`tests/test_paper_trading.py` for those cases.

The dashboard portion verifies the isolated app starts in `RESEARCH_ONLY`,
shows a simulated paper report, and has no order submission control. Strategy
and backtest reports are retrieved from their service APIs; the current
dashboard does not provide report views for those two result types. AppTest is
not browser validation.

This synthetic harness verifies test fixture interoperability only. It does
not establish real source-use rights, trustworthy NSE/BSE calendar coverage,
real-market data quality, predictive performance, forward paper-trading
readiness, or live-trading authorization. Execution mode remains
`RESEARCH_ONLY`; no broker, external feed, credentials, or real orders are used.

## Synthetic streaming-to-paper workflow

`test_full_synthetic_streaming_workflow.py` separately starts with a fresh
temporary database and generates quote events through the synthetic provider.
It checks completed-candle evidence, an independent execution snapshot, Phase 8
paper fills and long-only rejection, event idempotency, restart reconciliation,
and the read-only runtime dashboard. Its calendar and quotes are deterministic
test fixtures, not operational market data or evidence of live readiness.
