# Aegis — Local ML, Optional LLM and Empirical Evaluation Plan

**Prepared:** 2026-11-17. **Status:** PLANNED / PENDING APPROVAL implementation and evaluation proposal. LightGBM is the selected research candidate, not a trained or validated trading model. Thresholds and model promotion are NOT APPROVED by this document. No new code, dependency or model training is authorized.

## 1. Division of responsibilities — no duplicated trade authority

| Component | Owns | Does NOT own |
|---|---|---|
| Historical data and quality system | Point-in-time qualified candles, provenance, sessions, source rights | Trading permission or model promotion |
| Existing versioned SMA | Deterministic research benchmark; proposed initial pilot signals after authorization | Risk policy, broker orders, live model selection |
| **LightGBM** | Selected nonlinear local tabular research candidate; any future calibrated prediction requires separate validation | Validated trading model, autonomous order execution or risk-limit change |
| **Logistic Regression** | Mandatory low-complexity numerical baseline and calibration sanity check | Trading authority |
| **Claude / LLM (optional)** | Summarize reports, reason over bounded research evidence, generate testable hypotheses; optional future shadow proposer | Required inference in critical path, modifying model, trusting its own evidence, risk approval, orders |
| Deterministic signal policy | Turn approved predictions + position context into proposal BUY/SELL/HOLD under versioned rules | Final permission for simulated order |
| Phase 8 | Sole financial risk validation, simulated paper execution, cash/fill/ledger reconciliation | Training, choosing a model by best-looking recent profit |
| Decision Journal | Replayable record of all observed decisions, failures and later outcomes | New market evidence or bypass permission |

**Planned separation:** Claude/LLM is optional and excluded from the critical execution path. Do not run LLM and local ML synchronously in a serial order path. For a future separately authorized pilot, **SMA only** is proposed as active. No pilot is currently operating. Future ML shadow predictions should not call Claude. Any authorized training would be offline; a frozen artifact would be validated and versioned before use.

## 2. Why LightGBM was chosen, and its limitations

LightGBM is a practical starting candidate for structured tabular features on ordinary CPU hardware. It handles nonlinear interactions and usually provides efficient inference. This is a **systems engineering selection, not proof of predictive advantage in financial time series**. Market targets are low signal-to-noise, nonstationary, exposed to costs and prone to leakage. A simpler model may win; allow a NO-GO outcome. If LightGBM or its dependency adds unacceptable local operational complexity, keep Logistic Regression/SMA only until a documented review.

Do **not** add Random Forest, LSTM, deep Transformers, reinforcement learning or auto-training merely to expand the model list. Additional models are separate pre-registered hypotheses, not automatic promotion candidates.

## 3. Training data and experimental units

- **Data source:** user-permitted and properly entitled Kite historical candles (or separately approved comparable source), with immutable import/source IDs, approved calendar and quality. API access alone is not enough; source-use, coverage and actual timestamp quality must pass.
- **Universe:** freeze date-appropriate active NSE cash equity membership and instrument metadata; current constituents alone cause survivorship bias. If point-in-time universe history unavailable, disclose the limitation and narrow claims.
- **Adjustments:** define and version split/bonus/dividend and corporate-action policy; avoid mixing raw and adjusted price/volume in features or benchmark returns. Historical API may not deliver ideal corporate-action-adjusted series for every instrument.
- **Feature as-of:** only candles and public information available **before** the prediction point, with warm-up policy, missing data flags, data revision rules and deterministic serialization. Time features require exchange calendar semantics and session cutoffs.
- **Label as-of:** set horizon and economically meaningful target before training. Labels must not be available to inference and must be purged across train/test boundaries when outcome windows overlap.
- **Example target** (proposal, not approved until preregistered): probability of a future *tradable return exceeding modeled round-trip costs plus a pre-specified buffer* over N completed trading minutes. Define bid/ask/slippage and execution lag carefully. A probability of an up move is not itself expected profit or a BUY instruction.
- **Sampling:** consider class balance, market regime shifts, overlapping labels, correlation across instruments and multiple observation per day; avoid ordinary shuffled random split of adjacent candles.
- **Data-set freeze:** immutable manifests: source imports and rights, date range, instrument universe, calendar, all cleaning and normalization, parameters, library versions, seeds and exact code revision.

## 4. Train, validation, locked holdout and walk-forward

1. **Development:** feature engineering and architecture may be explored; log each attempt, parameter change and data slice, including unsuccessful runs.
2. **Validation:** chronological rolling/walk-forward folds, with purging/embargo at label overlaps and universe/cost parity, choose thresholds/policies; don't tune repeatedly on holdout.
3. **Locked holdout:** pre-register one final selection and release only after frozen code, features, model, costs, universe and evaluation settings. Record all access and invalidate 'untouched' claim on repeated tuning.
4. **Shadow forward evidence:** compare on identical candle IDs and timestamps, log opportunity coverage as well as matched hypothetical trade outcomes. Shadows never control Phase 8 until a separate promotion approval.
5. **Manual promotion:** technical acceptance and empirically justified performance pass separately. Rollback and drift controls required. Passing a classification metric is insufficient.

## 5. Evaluation metrics and controls

### Statistical and predictive

Precision/recall as appropriate, class prevalence, calibration (Brier/reliability), uncertainty intervals, drift, fraction of abstentions, consistency across regimes/instruments and failure under missing features.

### Trading strategy economics

Daily net after-cost returns with a declared calendar/holding policy; exposure, turnover, filled-notional, simulated fees, spread/slippage, performance relative to same-instrument buy-and-hold and an appropriately matched random baseline; drawdown, volatility, net Sharpe with assumptions, expectancy and profit concentration. Compare paired daily excess series under identical eligible opportunities. Record optimistic assumptions and missing bid/ask.

### Robustness and selection bias

Cost/slippage multipliers (proposed 1.5x and 2x, **not approved thresholds**), removal of top trades, parameter perturbations, regime/market turbulence windows, alternate horizon checks, multiple-testing correction/deflated selection awareness, block bootstrap where valid. Choose exact methods, confidence levels, sample counts and stopping rules *before* final holdout. Beware trading returns autocorrelation and same-period strategy comparisons.

### Critical failure gates

Reject a candidate on lookahead, missing source rights/quality, unstable timestamp alignment, unverifiable model artifact, exposed holdout, unrealistic cost/liquidity assumptions, inconsistent cash accounting, mismatched comparison universe or insufficient support for claims. Performance conclusions must remain 'undetermined' if data is inadequate.

## 6. Point-in-time inference contract

**Proposed schema:** `model_id`, `artifact_hash`, `feature_schema_hash`, `training_data_manifest`, `effective_from_session`, `instrument_id`, `as_of_candle_id`, `information_cutoff`, `feature_hash`, `prediction_horizon`, `probability_and_calibration_id`, `model_status`, `elapsed_monotonic_ms`, `prediction_id`. Hard validation against expected version, schema, horizon, calendar, feature quality, clock freshness and null rules. No arbitrary Python object pickles from untrusted sources. Persist artifact origin and trusted serialization mechanism.

The strategy proposes BUY/SELL/HOLD using a separate immutable **signal policy** with thresholds and position context; model outputs alone should not be treated as orders. Risk gate checks actual fresh evidence at execution. Missing/expired artifact, unhealthy feature or timeout yields `NO_DECISION`/BLOCKED for ML, with operator visibility; not a fabricated prediction or silent switching to SMA.

## 7. Runtime performance and independence

- Load model once per approved session, not once per candle; update features incrementally where safe and verify exact parity with full-window batch calculations.
- Report ingest, candle finalization, feature, inference, proposal, Phase 8 and SQLite latency separately; then end-to-end observed p50/p95/p99 by market session.
- Initial 1-minute strategy values correctness and freshness above unrealistic sub-250ms promises. A fast inference on stale quotes is unsafe.
- Shadow runs must be isolated: bounded CPU/time budgets, queue backpressure, no ability to block active Phase 8 or modify account state.
- Cost/budget caps for offline research and optional LLM service; no automatic paid provider calls.

## 8. LLM/Claude decision

**For first live-data paper pilot: disable.** No subscription needed. Avoid sending raw bulk historical candles in each request or believing inference API calls train Claude.

Potential later use **only if a bounded measurable purpose exists**:

1. Explain daily strategy performance from deterministic metrics and cited evidence.
2. Read authorized research summaries and propose falsifiable new features/hypotheses.
3. Independently propose BUY/SELL/HOLD as a **shadow research candidate**, never directly to Phase 8 without separate promotion review.

Privacy: no broker tokens, secrets, full account details or unapproved data to external LLM. Prompt injection/data poisoning should lead to rejection. Bounded tools enforce permission, resource/time limits and approved-calendar/source authority. Freeze model/provider IDs/prompt versions/tool implementation and normalized evidence refs for comparative evaluation; external nondeterminism limits replay. Reevaluate ROI against a local-only solution before enabling.

## 9. Planned decision journal as research substrate

The proposed, not-yet-implemented journal would record all opportunities, not just fills: active SMA, blocked proposals, NO_DECISION feed gaps and identical shadow predictions. A planned delayed evaluator would record 5/15/60-minute outcomes only if preregistered and observed, separately from realized paper P&L. Never automatically train on outcome fields adjacent to feature columns without temporal safeguards. Preserve negative results and all trial attempts to prevent cherry-picking.

## 10. Promotion ladder

`Research-only data` → `offline model with reproducible validation` → `independent locked-holdout assessment` → `shadow decisions (no orders)` → `supervised paper proposals after explicit approval` → `longer observed evidence` → **future, separate real-order assessment default NO-GO**.

Each arrow is a deliberate gate with data provenance, costs, safety, measured performance, robustness, explicit approvals and rollback. There is **no** self-promoting model loop.

## 11. Acceptance artifacts

Model manifest, frozen dataset/provenance and permission report, code/dependency hashes, attempts log, feature/label leakage tests, chrono CV and locked holdout proof, paired after-cost returns with benchmarks, independent reviewer verdict, shadow-run coverage and exact promotion/rollback approvals. Retain explicit OPEN status for not-yet-decided numerical thresholds.

## 12. Binding references to preserve

[AI research protocol](./AI_RESEARCH_PROTOCOL.md),
[approved architecture decisions](./ARCHITECTURE_DECISIONS.md),
[open decisions](./OPEN_DECISIONS.md),
[validation and acceptance](./VALIDATION_AND_ACCEPTANCE.md),
[Phase 7](../tasks/PHASE_07_ML_RESEARCH.md), current proposal contracts and
[Phase 8](../tasks/PHASE_08_PAPER_TRADING.md) remain controlling.
See also the [handoff](./PROJECT_HANDOFF_2026-11-18.md) and
[planned Decision Journal](../tasks/DECISION_JOURNAL_V1.md).
This plan does not amend approved restrictions or authorize a new operating mode;
`RESEARCH_ONLY` remains active.
