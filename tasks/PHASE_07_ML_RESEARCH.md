# Phase 7 — ML Research (Optional Evidence-Gated Track)

## Goal
Test whether simple ML adds robust value beyond transparent non-ML baselines. ML is optional and must not be added merely for complexity.

## Tasks
- [x] Establish and preserve non-ML baselines first (training majority and SMA20/50 rule).
- [x] Define features using only information available at decision time.
- [x] Create chronological train/validation/test splits with overlapping target windows purged.
- [x] Add leakage checks and feature/data provenance.
- [x] Train a deterministic logistic-regression baseline behind explicit data provenance gates.
- [ ] Compare strategy-level performance against naive and rule-based benchmarks after realistic costs.
- [x] Add expanding-window walk-forward and validation-only regularization sensitivity analysis.
- [ ] Enforce a frozen-model holdout workflow before final test evaluation.
- [x] Return model/library version, dataset fingerprint, source provenance, features, parameters, split timestamps, and results as a deterministic report.

## Acceptance criteria
- No random shuffling across time for time-series evaluation.
- No target leakage or use of unavailable future information.
- Improvement is measured out of sample and after costs.
- If ML does not improve results robustly, retain the simpler baseline.
- ML models cannot bypass deterministic risk controls or independently activate execution.

Phase 7 may consume validated, accepted, provenance-preserving research outputs from
Phase 5 and Phase 6 where appropriate. Those phases do not establish that ML is useful
or remove the evidence gates above.

## Current implementation and acceptance status

The in-memory research harness is implemented in `src/aegis/ml_research.py`. It builds
causal features from completed candles using Phase 6 indicators, rejects non-permitted or
quality/calendar-unready data, uses chronological purged partitions, and reports only
classifier diagnostics. `tests/test_ml_research.py` uses explicitly synthetic fixtures to
verify these code paths. The harness does not write to SQLite or persist fitted models.

Phase 7 acceptance remains pending. The local database currently has no historical-candle
table or imports, and the checked-in exchange calendar is empty. Therefore there is no
permitted, quality-gated dataset with which to evaluate strategy returns after realistic
costs, perform a credible untouched out-of-sample comparison, or establish robust ML value.
Synthetic results are implementation tests only, not evidence of predictive performance.
No market-data permissions or calendar completeness are inferred. The remaining unchecked
criteria above require an appropriately verified dataset and explicit cost assumptions;
this limitation does not authorize paper or live trading.
