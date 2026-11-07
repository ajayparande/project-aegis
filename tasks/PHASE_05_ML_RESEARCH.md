# Phase 5 — ML Research

## Goal
Test whether simple ML adds value beyond transparent baselines.

## Tasks
- [ ] Establish non-ML baseline first.
- [ ] Define features using only information available at decision time.
- [ ] Create chronological train/validation/test splits.
- [ ] Add leakage tests and feature provenance.
- [ ] Train a simple baseline model.
- [ ] Compare against naive and rule-based benchmarks after costs.
- [ ] Add walk-forward and sensitivity analysis.
- [ ] Keep final holdout untouched until model/strategy is frozen.

## Acceptance criteria
- No random shuffling across time for time-series evaluation.
- No target leakage.
- Improvement is measured out of sample and after costs.
- If ML does not improve results robustly, retain the simpler baseline.
