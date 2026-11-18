# Aegis Decision Journal v1 — event, outcome and replay contract

**Status:** PLANNED / PENDING APPROVAL; NOT IMPLEMENTED. Architectural specification proposal, not an approved schema or execution contract.
**Prepared:** 2026-11-17.
**Scope:** one-instrument synthetic and subsequently explicitly authorized live-data paper operation; supports shadow comparison, without authorizing trading or ML promotion. Phase 8 remains financial source of truth.

## 1. Non-negotiable invariants

1. Record **every expected decision opportunity**: BUY, SELL, HOLD, BLOCKED or `NO_DECISION`. Distinguish a signal HOLD from no data/no evaluation. Also record system/health/kill/restart and rejected-order events.
2. Immutable initial decision, exact information cutoff, input/evidence identifiers and execution outcome; never update a prior prediction with hindsight. Append later outcome assessments under a separate event identity and explicit calculation version.
3. No decision journal field is automatically trusted evidence or permission to execute. Phase 8 requalifies independently. The journal's `approved`/`eligible` descriptions cannot bypass calendar, source-use, feed or risk gates.
4. Every accepted **paper order/fill** must be auditable via the existing Phase 8 transactional ledger; journal links `paper_account_id`, `paper_order_id`, `fill_id` where allowed, never supplies a parallel financial balance.
5. Stable idempotency across retries and restarts. A unique decision key must incorporate runtime source identity, instrument, completed interval/event ID, proposer ID/version and decision attempt generation. Do not use wall-clock insertion time as identity.
6. Local SQLite with migrations tested only on fresh/disposable copies. Never access normal DB under documentation-only work or without separate explicit authorization.
7. No raw broker credentials/tokens, full private account identifiers, unapproved AI prompts/responses, sensitive user data, unrestricted filenames, arbitrary tool output or executable payloads in journal. Sanitize bounded reason codes and metadata.
8. Structured logging must be bounded and maintainable; avoid writing every raw WebSocket tick as a large JSON blob in the decision table. Raw market events use distinct authorized archival/retention rules if needed.

## 2. Logical schema (map to existing database style; no schema migration authorized yet)

### `decision_event` — append-only facts

Required fields (suggested names, exact schema must respect existing IDs and SQLite conventions):

| Field | Meaning / constraint |
|---|---|
| `decision_id` | Stable canonical ID, unique and deterministic; UUID/hash can be used without claiming cryptographic trust |
| `schema_version` | Explicit v1 schema |
| `run_id`, `session_date`, `runtime_event_id` | Operator/session/event provenance, unique within run |
| `instrument_id`, `exchange_segment`, `interval` | Exact registered active instrument, one-minute initial interval |
| `candle_id`, `candle_source_id`, `source_import_id` | Qualified evidence linkage; not unverified caller-text authority |
| `calendar_fingerprint`, `calendar_confidence` | Actual approved calendar resolution; `CONFIGURED` not `CONFIRMED` |
| `decision_time_utc`, `as_of_utc`, `information_cutoff_utc` | Timezone-aware and temporal ordering validated; `as_of <= decision` and evidence available by cutoff |
| `market_quote_id`, `quote_received_time_utc` | Observed execution-time quote reference when applicable |
| `proposer_id`, `proposer_version`, `strategy_id` | Deterministic SMA / shadow LightGBM / other named proposer |
| `model_artifact_hash`, `feature_set_version`, `parameter_manifest_hash` | Null for non-ML where inapplicable; exact nonsecret reference for model versioning |
| `signal` | BUY, SELL, HOLD or `NO_DECISION`; separate from authorization result |
| `validation_outcome` | Allowed, BLOCKED, NOT_EVALUATED, simulated accepted/rejected or other controlled enumeration |
| `reason_codes` | Bounded stable codes list, no secrets, no arbitrary model prose |
| `quality_report_id`, `source_use_status`, `feed_health_id`, `risk_snapshot_id` | Independent gate evidence references |
| `prediction_probability`, `calibration_id` | Optional, limited [0,1] only if semantically valid and calibrated; no fake probability for SMA |
| `proposed_qty`, `proposed_price`, `stop`, `target` | Optional Decimal/exact-paise/tick-safe requested values, not authoritative fills |
| `paper_account_id`, `paper_order_id`, `paper_fill_id` | Nullable links to authoritative Phase 8 records |
| `code_version`, `config_manifest_hash`, `cost_policy_version` | Reproducibility identities |
| `elapsed_stage_ms` | Separate monotonic durations for ingest/finalize/features/inference/risk/commit; elapsed only, not wall clock reconstruction |
| `created_at_utc`, `event_status` | Ingestion timestamp and terminal/pending state per documented transactional protocol |

Store source references or bounded canonical feature hashes with guarded source metadata; exact raw feature vectors may be retained in a separate restricted typed table when licensing/privacy and storage budget permit. A hash alone does not enable exact replay if original inputs disappear.

### `decision_outcome` — delayed, append-only results

`outcome_id`, `decision_id` FK, `horizon_definition_id`, `evaluation_version`, `as_of_end_utc`, source candle/quote refs, `coverage_status`, `censored_reason`, `observed_return`, `adverse_excursion`, `favorable_excursion`, `hypothetical_net_after_cost`, `actual_paper_pnl_ref`, `created_at_utc`, `supersedes_outcome_id` (optional versioned correction). No updates to original decision. Avoid naming a hypothetical price rise a realized trade profit. Fix whether horizon is *trading minutes* or *wall minutes* before a study; close/day/holiday truncation is censored, not fabricated.

### `runtime_health_event` — durable operational actions

`event_id`, `run_id`, `instrument_id`, `time_utc`, `kind`, `severity`, `reason_code`, `source_connection_generation`, `candle_or_order_ref`, `resolution_ref`, `operator_action` where authorized. Include auth expiration, reconnect, stream gaps, missed events, startup/restart reconciliation, clock skew, disk/DB errors, manual kill switch and abnormal exits.

### `experiment_manifest` — reproducibility

`experiment_id`, scope, frozen strategy + model + feature + parameters, input dataset quality/provenance, date cutoffs, calendar, instrument universe, fee policy, code version, approved experiment protocol, user authorization and prior attempts. Keep raw sensitive AI transcripts out unless retention policy explicitly approves them.

### `shadow_decision` (can be same `decision_event` with role field)

Shadow proposals share the **identical primary candle and as-of evidence ID** as active strategy, report `role=SHADOW`, and never receive permission to dispatch to Phase 8. If a shadow model is down, log shadow `NO_DECISION`, not retroactively fill prediction. Shadow P&L must be explicitly hypothetical and calculated under the same permitted fill/cost assumptions.

## 3. Transaction and failure semantics

- The lowest-risk design is to use existing Phase 8 DB transactions for actual simulated fills and ledger updates; persist a minimal durable decision/attempt linkage atomically **where existing Phase 8 architecture permits**. If journal and Phase 8 tables are in different transactions, use a persisted intent/outbox state and reconciliation protocol; **never claim cross-DB atomicity**.
- Before a new BUY, required audit intent and trusted evidence must be durable. If logging fails or queue is full, BLOCK new entries with an operator-visible reason. Do not accept unrecorded simulated orders.
- Protective SELLs must retain Phase 8's reviewed risk priority. If supplemental analytics journal fails but financial ledger is healthy, a valid risk-reducing SELL may be logged through the mandatory Phase 8 transaction and supplemental journal gap repaired from ledger + health event. If Phase 8's financial write itself fails, no fill. Reconciliation hard halt remains absolute.
- Exactly-once **effect** (one simulated order/fill) is achieved by DB unique constraints, stable idempotency keys and Phase 8 transactions, not by assuming a callback runs exactly once. Out-of-process streams are at least once or may drop data; record gaps.
- Bounded queue buffers, one SQLite writer policy or locking protocol, WAL strategy only if existing architecture supports it, busy-timeout and backpressure metrics. No infinite buffer, no silent drops.
- On crash between decision-intent and Phase 8 fill, replay intent references, query authoritative order status and either conclude already handled or fail closed; never fabricate a fill. On crash after fill before ancillary journal append, reconstruct allowable linkage from immutable ledger evidence with a recovery event.

## 4. Point-in-time evaluation and counterfactuals

- Original decision and its information cutoff must remain immutable; a subsequent candle correction may cause a **new evaluation version**, not mutate the old decision.
- Candidate horizons: 5, 15 and 60 completed *trading* minutes are illustrative; formal study defines horizon boundaries and no-lookahead rules upfront, including incomplete sessions and after-hours censoring.
- Outcome jobs trigger only after requisite evidence arrives and is qualified. Disallow outcomes computed on same-bar unknowable high/low as if executable.
- Paired shadow baselines use matching universe, calendar, time cutoff, tradable bid/ask assumptions, costs and sample filters; report opportunity coverage as well as average return.
- Do not directly retrain on forward paper journal rows without time-aware dataset construction, rights checks, feature/outcome separation, data-zone definitions and a pre-approved experiment.

## 5. Data minimization, retention and replay

- Keep compact decision events and input hashes/IDs; raw ticks and verbose debug logs must use explicit retention/rotation, storage budget and provider-licensing policy. No secrets.
- Code + model hashes + settings make version relationships traceable but do not fully reproduce an external feed or nondeterministic LLM response. The replay mechanism must require archived permitted evidence bytes and declared deterministic execution versions.
- Retention period, backup frequency, raw data rights, anonymization and access policy are OPEN until approved. Local-only storage preferred; no automatic cloud sync or export.
- Read-only dashboard queries must never trigger journal writes, recalculation that mutates original records, or live actions.

## 6. Required tests — implement before live activation

1. Every expected completed candle yields exactly one of evaluated decision/HOLD/BLOCKED/NO_DECISION, with unique stable ID; no silent gap.
2. BUY and SELL linkage to exact source/calendar, runtime event and Phase 8 order/fill; injected/caller-supplied passing quality cannot override trusted evidence.
3. HOLD with genuine evaluation differs from feed-outage NO_DECISION; ordinary risk BLOCKED differs from a model prediction.
4. Duplicate scheduler events/replayed messages do not duplicate journal or paper fills.
5. Invalid timestamps (naive, out of order, future evidence) rejected; optional prediction probability semantics and schema enforced.
6. Immutable original decisions; later outcomes appended with version/censoring, never revising old prediction or leaking future price into as-of query.
7. Disk-full/busy DB/queue overflow/journal exception → no unlogged new BUY; valid protective exit preserves ledger auditability; reconciliation halt blocks both.
8. Restart after each intentional fault injection point yields coherent journal references, one order/fill and no invented outcomes.
9. Redaction tests for API key/token/secret/AI prompts across log, JSON, SQLite and exception paths.
10. Read-only dashboard test detects stale vs fresh status and denies writes.
11. Shadow model timeout/crash leaves active SMA/risk unchanged and records shadow unavailability.
12. Aggregate query over journal reproduces decision count and categorized reason totals for a complete test session; no selection-only bias.
13. Backfill arriving after original decision cannot rewrite history or submit retroactive order.
14. Cross-session candle boundaries, special sessions/holidays and incomplete outcome horizon censoring.
15. Deterministic serialized schema and source fingerprints; immutable public API no mutable nested alias leaks.

## 7. Acceptance evidence

- Schema and migration/rollback review; actual isolation test commands and results; no normal DB access.
- Example synthetic session showing all event types, order linkage and delayed outcome computation.
- Restart and disk-failure report; sensitive-data redaction report.
- Read-only journal-to-ledger reconciliation report, with explicit unresolved-case behavior.
- Latency and backpressure counters with *measured* values, not target-only statements.
- Reviewer verifies the journal cannot be used as execution authorization and cannot bypass Phase 8.

## 8. Release order

Implement journal contract with synthetic data **before** real-data pilot; then run one isolated integration gate. Next add Kite read-only observation with separate explicit approval. ML fields are optional in v1, but the versioned schema and role identifiers must allow later shadow comparators without breaking Phase 8.

Each step above requires separate authorization; this specification grants none.
`RESEARCH_ONLY` remains active. Preserve the existing Phase 8 execution, risk and
transaction contracts; any proposed integration change requires approval before code.

## 9. Related documents

- [Current project state](../docs/PROJECT_STATE.md) and [approved decisions](../docs/ARCHITECTURE_DECISIONS.md).
- [Next-session handoff](../docs/PROJECT_HANDOFF_2026-11-18.md).
- [Planned live-data paper MVP](./PHASE_10_LIVE_DATA_PAPER_MVP.md).
- [ML/optional LLM evaluation plan](../docs/ML_LLM_AND_EVALUATION_PLAN.md).
