# Phase 7 ML Research Implementation Report

## Status

**Not accepted; implementation is present but required empirical evidence is unavailable.**

The implementation is a deterministic, in-memory research harness. The local SQLite
database currently has schema metadata only through version 2, no
`historical_candles` table, zero data imports, and zero instruments. The checked-in
`config/exchange_calendars.json` has no configured calendars. Consequently, this
environment cannot supply a permitted, quality-gated dataset or establish credible
after-cost out-of-sample strategy performance. No database migration, import, or data
fabrication was performed.

## Scope implemented

`src/aegis/ml_research.py` uses the optional scikit-learn research extra and existing
Phase 5 candle validation and Phase 6 indicator APIs. It does not add dependencies, write
to SQLite, persist a model, create a strategy, connect a broker, or place orders. It is
imported as `aegis.ml_research` rather than from the package root so core package imports
do not require the optional scikit-learn dependency.

The directional feature builder uses only completed, validated candles. It calculates
close versus SMA20, SMA20 versus SMA50, RSI14, MACD relative to close, ROC12, ATR14
relative to close, stochastic %K, and volume relative to its 20-bar average. The label is
whether the close at the configured later observation is above the decision close; a
flat or down outcome maps to class 0. Indicator warm-up rows are omitted, and no candles
are synthesized.

The model baseline is `StandardScaler` plus deterministic logistic regression. It is
compared on classification metrics with a training-majority benchmark and an SMA20/50
rule classifier. These are label-classification diagnostics, not position simulations
or strategy returns. Transaction costs and slippage are explicitly marked unevaluated.

## Requirement-to-code and test mapping

| Requirement | Implementation | Exact test/evidence | Status |
|---|---|---|---|
| Preserve non-ML references | Training majority and SMA20/50 rule classifiers in `run_baseline_experiment` | `test_model_run_is_deterministic_and_reports_only_classifier_diagnostics` checks each benchmark on all three partitions | Implemented as classification baselines; not cost-aware strategies |
| Decision-time features and future-only label | `build_directional_dataset`; `FeatureValue.available_at`; `ResearchObservation` | `test_dataset_builder_uses_completed_candles_and_decision_time_features` checks names, timestamps, label horizon, prefix consistency and input immutability | Implemented |
| Chronological train/validation/test with no target overlap | `chronological_split` purges rows whose outcome reaches the next partition | `test_chronological_split_purges_overlapping_outcomes` checks order, strict outcome boundaries, and purge counts | Implemented |
| Leakage and provenance safeguards | Observation-level feature availability check; source/calendar/quality status validation; report and dataset SHA-256 include declared provenance | `test_feature_timestamp_cannot_exceed_decision_time`; `test_research_provenance_fails_closed_for_unverified_data`; deterministic report test | Implemented for declared metadata; declaration authenticity is caller responsibility |
| Simple model and reproducibility | Fixed logistic pipeline and serialized model parameters/library version | `test_model_run_is_deterministic_and_reports_only_classifier_diagnostics` compares complete reports from repeat runs | Implemented; synthetic fixture only |
| Walk-forward and sensitivity | Expanding-window folds with purging; sensitivity evaluates validation rows only | `test_sensitivity_is_validation_only_and_walk_forward_is_chronological` | Implemented; synthetic fixture only |
| Transaction-cost-aware strategy comparison | No strategy simulation or transaction-cost model is implemented | `costs_evaluated: false` in report; no real data is available | **Unmet** |
| Untouched final holdout until frozen | Baseline API evaluates test after fitting the supplied configuration, but does not enforce a one-time frozen-model workflow | Test confirms chronological separation only; no test-time access lock is present | **Unmet as an enforced workflow** |
| Record model/data/features/parameters/results | JSON-safe report includes scikit-learn version, full declared source provenance, dataset fingerprint, features, model/split parameters, partition timestamps and metrics | `test_model_run_is_deterministic_and_reports_only_classifier_diagnostics` | Implemented in returned report; no persistence |
| Invalid and insufficient data | Phase 5 candle validation is reused; feature and label horizon validation is explicit | `test_dataset_builder_rejects_incomplete_or_insufficient_candles` | Implemented for incomplete candles and insufficient history; no missing bars are inferred |

## Tests and validation

The new synthetic-only Phase 7 test file is `tests/test_ml_research.py`:

- `test_dataset_builder_uses_completed_candles_and_decision_time_features`
- `test_research_provenance_fails_closed_for_unverified_data`
- `test_feature_timestamp_cannot_exceed_decision_time`
- `test_chronological_split_purges_overlapping_outcomes`
- `test_model_run_is_deterministic_and_reports_only_classifier_diagnostics`
- `test_sensitivity_is_validation_only_and_walk_forward_is_chronological`
- `test_dataset_builder_rejects_incomplete_or_insufficient_candles`

Executed validation:

| Command | Result |
|---|---|
| `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_ml_research.py` | 7 passed |
| `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider` | 409 passed in 44.59s |
| `.\.venv\Scripts\ruff.exe check .` | Passed: all checks passed |
| `.\.venv\Scripts\ruff.exe format --check .` | Passed: 54 files already formatted |
| `.\.venv\Scripts\mypy.exe src` | Passed: no issues in 12 source files |
| `git diff --check` | Exit 0; emitted existing LF-to-CRLF working-copy warnings for `README.md` and `tasks/PHASE_03_STRATEGY_LAB.md` |

No predictive-performance measurement is reported. The only evaluated data are synthetic
unit-test fixtures. These results do not establish market performance, profitability,
market-data rights, calendar completeness, or operational readiness.

## Reproducibility, causality, and limitations

Dataset hashing covers ordered feature and label rows, label horizon, and declared
provenance. Model configuration, scikit-learn version, split timestamps and result
metrics are returned in a deterministic JSON-safe report. Features are timestamped at
the completed decision candle; outcomes have a strictly later timestamp; overlapping
target rows are purged before the next partition. Prefix/truncation invariance and input
immutability are exercised by synthetic fixtures.

Provenance fields are explicit declarations supplied to the in-memory API. This code
does not independently authenticate source permissions, load the application's import
history, or re-run the exchange-calendar and market-quality analyzers. A caller must not
represent a declaration as independently verified. The empty checked-in calendar and
empty/unmigrated local database prevent meaningful real-data evaluation in this
environment.

The final test partition is separated chronologically and used for reporting after model
fit, but the API does not enforce a durable freeze or prevent repeated test inspection.
Sensitivity analysis itself reads training and validation only. A supervised,
one-time holdout process remains required before any empirical claim. No strategy-level
transaction costs, slippage, liquidity/capacity, turnover, drawdown or net returns are
calculated. No arbitrary cost assumptions were introduced in the absence of an approved
market dataset and instrument-specific assumptions.

## Safety and deferred scope

Execution remains `RESEARCH_ONLY`. No broker connection, credentials, orders, paper
trading, risk-control override, database/log mutation, or live execution path was added.
Phase 8 was not started. Phase 7 acceptance remains pending permitted and quality-gated
market data, explicit justified cost assumptions, cost-aware strategy benchmarks,
walk-forward/sensitivity results on real data, and a controlled untouched holdout
evaluation. No model output is evidence of profitability or authorization to trade.
