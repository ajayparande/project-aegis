# Phase 5 — ML Research (Optional Evidence-Gated Track)

## Goal
Test whether simple ML adds robust value beyond transparent non-ML baselines. ML is optional and must not be added merely for complexity.

## Tasks
- [ ] Establish and preserve non-ML baselines first.
- [ ] Define features using only information available at decision time.
- [ ] Create chronological train/validation/test splits.
- [ ] Add leakage tests and feature provenance.
- [ ] Train a simple baseline model only where justified.
- [ ] Compare against naive and rule-based benchmarks after realistic costs.
- [ ] Add walk-forward and sensitivity analysis.
- [ ] Keep the final holdout untouched until the model/strategy is frozen.
- [ ] Record model version, training data, features, parameters, and evaluation results.

## Acceptance criteria
- No random shuffling across time for time-series evaluation.
- No target leakage or use of unavailable future information.
- Improvement is measured out of sample and after costs.
- If ML does not improve results robustly, retain the simpler baseline.
- ML models cannot bypass deterministic risk controls or independently activate execution.
