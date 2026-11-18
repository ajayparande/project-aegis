# Project Aegis — 360° Architecture and Delivery Review

**Prepared:** 2026-11-17 (Asia/Kolkata)
**Type:** PLANNED / PENDING APPROVAL architectural recommendation and risk assessment. **Not an approved requirements baseline or operational authorization.**
**Basis:** User-reported Checkpoint 1/2 and M1B/M2 progress; prior project design and research protocol snapshots (the document archive examined is dated 2026-11-15 and can be superseded by newer repository content); official Kite Connect documentation checked 2026-11-17. **The live Windows repository, current source and normal database were not inspected by this reviewer.**

The Decision Journal and live-data paper MVP described below are **planned, not
implemented**. Provider capabilities, pricing, token lifetimes and timing figures are
dated review reports, not facts reverified by this documentation integration. Existing
[approved decisions](./ARCHITECTURE_DECISIONS.md) and
[current status](./PROJECT_STATE.md) remain authoritative.

## 0. Proposed direction — pending approval

Keep Project Aegis a **local-first, evidence-driven trading research platform**. Move toward a narrowly authorized **one-instrument, NSE cash-equity, long-only, 1-minute completed-candle live-data paper pilot** while preserving existing historical M2 and AI research work. Prove the whole chain—data rights and authentication → point-in-time evidence → signal → Python risk → simulated execution → transactional accounting → journal → restart/replay—before widening instruments or introducing ML into the authorized paper lane.

- **Primary launch proposer:** existing deterministic, versioned SMA strategy. No requirement to retrain a model before the plumbing pilot.
- **Selected research candidate:** LightGBM tabular classifier, proposed for offline comparison against Logistic Regression and SMA. It is not trained or validated as a trading model; training, dependencies and promotion need separate authorization.
- **LLM/Claude:** optional asynchronous/offline research assistant or independent shadow proposer in a separately scoped experiment. **Never on the latency-critical path, never an authority for risk/order execution, no required subscription.**
- **One common proposal/evidence contract:** identify proposer ID/version, instrument, information cutoff, evidence references, outcome, confidence/calibration where meaningful, expiry and reason codes. Do not alias research types over Phase 8 public APIs.
- **Only Phase 8** determines permitted simulated orders, their financial state and risk. No new simulator, no broker real-order path. M2 remains historical-only and does not become Phase 8.
- **First step is documentation and verification, not changing `RESEARCH_ONLY`.** Live source access, broker subscription, normal DB operation, and a paper-mode switch require distinct explicit approvals.

### Status discipline

Use **reported**, **verified**, **independently accepted**, **operationally ready** and **authorized** separately. Reported passing synthetic tests are not evidence of profitable strategy performance or a live-ready calendar. Approved design is not deployed implementation. This review must not overwrite historical records or mark unreviewed phases accepted.

## 1. Starting facts and uncertainties

| Component | Current best-supported status | Architectural consequence |
|---|---|---|
| Git | Owner reports `main` HEAD `18ddc9da7fa5021b3709f4a0658e03eb0de611f6` (Checkpoint 1), 17 modified + 22 untracked, 0 staged | Preserve dirty work; no `git add .`, restore, reset, clean, stash, commit or push without authorization |
| Checkpoint 2 | Owner reports two blocking corrections, 34 new regressions, 305 focused passes, Ruff/format/mypy passed; **independent acceptance pending**, no commit | One narrow acceptance gate; do not treat Checkpoint 2 as approved or stage its previously rejected manifest |
| M1B | Owner-reported bounded independent acceptance | Reuse accepted one-instrument qualified historical execution; no real-world performance claim |
| M2-A/B | Owner history reports accepted, stage-specific scopes; retain M2-B correction-review caveats | Preserve approved M2 v1 contracts and canonical evidence limitations |
| M2-C | Independently accepted for qualified historical per-sleeve M1B composition, per owner history | No overall M2 or operational acceptance |
| M2-D | Implementer-reported technical ACCEPT; independent signoff pending | Preserve, no implied M2-E authorization |
| M2-E | Not started | Historical valuation is a separate later task, not prerequisite to one-instrument forward-paper plumbing |
| Phase 8 | Paper accounting/risk/reconciliation foundation implemented; scoped synthetic tests | Execution authority only; actual operating mode remains `RESEARCH_ONLY` |
| Phase 10 | Synthetic forward-runtime foundation reportedly exists; real Kite feed not demonstrated | A new live-data adapter and integration gates required |
| Calendar | Source-backed NSE cash candidate `CONFIGURED`, not `CONFIRMED` | Cannot silently qualify market sessions for operational trading; needs approved confirmation and rolling maintenance into 2027 |
| Data source and permissions | Real permitted source-use and retained historical candles not demonstrated | API entitlements, use terms, and recorded provenance must be verified before ingestion |
| Research evaluation | Classifier research exists; strategy P&L/locked-holdout promotion not accepted | No claim ML is superior; shadow first |
| Decision journal / live-data paper MVP | PLANNED / PENDING APPROVAL, not implemented; existing audit logs and synthetic foundations remain separate | Implementation and operation require separate authorization |
| Latency | Prior illustrative 190ms/250ms numbers were **not measurements** | Instrument real durations, publish actual p50/p95/p99; do not promise subsecond until measured |

**Documentation conflict:** the available 2026-11-15 archive still says M2-B review pending and M2-C not started. This is stale relative to later owner-reported milestones; reconcile with the live repo and evidence, retaining uncertainty. Do not rewrite history to imply independent artifacts exist when none are present.

## 2. Product and deployment boundaries

**Current safe state:** `RESEARCH_ONLY`, local Windows + Python 3.11 + SQLite + Streamlit; synthetic tests only unless separately authorized. Existing DB and user records remain protected.

**Proposed operating states** (design only; names and runtime switches subject to future approval):
1. `RESEARCH_ONLY`: offline research, synthetic replay, no live broker access.
2. `LIVE_DATA_OBSERVE_ONLY`: explicitly authorized read-only Kite market data, local observation/journaling; **no simulated order submissions** until accepted.
3. `LIVE_DATA_PAPER`: explicitly authorized read-only Kite market data plus Phase 8 simulated orders, one approved account and strategy; **no real broker orders**.
4. `LIVE_TRADING`: **not an implementation target; separate future regulatory, safety, architecture and owner authorization. Default NO-GO.**

Mode switches must be explicit; not inferred from environment variables, valid tokens, a paid API plan, passing tests or a UI checkbox. Prevent privilege escalation via code imports, tools, dashboard and plugins. Keep broker order endpoints out of the paper runtime's supported transport: allow market-data GET and WebSocket only, with a separately controlled authentication flow; reject attempts to invoke HTTP order endpoints. A shared Kite API token may be capable of placing orders at the broker, so 'read-only adapter' is a software enforcement boundary, **not broker-issued read-only credential authority**. Confirm transport restrictions with negative tests.

## 3. Proposed system-of-systems architecture (not current implementation)

```text
Zerodha paid market-data entitlement + daily auth     Approved calendar + source-use rights
                       |                                     |
        GET market-data / WebSocket read-only            trusted validators
                       |                                     |
                Instrument / time / session / quote normalization
                       |                                     |
                   immutable event + quality + import provenance
                       |
       Live tick observation -> candle construction + gap classification
                       |
          finalized completed-candle event, stable ID, cutoff, watermark
                       |
         ┌─────────────┴─────────────────────┐
         |                                   |
  ACTIVE: deterministic SMA             SHADOW: ML / future LLM
         |                                   |
  proposal + risk evidence                 shadow proposal only
         |                                   |
  Phase 8 permission/risk check              journaling
         |
  Phase 8 simulated order/fill/ledger/reconciliation
         |
      Append-only decision/audit journal (including HOLD/BLOCKED/NO_DECISION)
         |
     Delayed outcome evaluation + performance/health monitoring
         |
      Offline statistical research / explicit candidate promotion
```

**No model makes a real broker request.** Shadow/LLM failures must not block the active deterministic proposer. Risk-reducing exits remain independent of ML and ordinary risk/account halts, subject to existing reconciliation and evidence controls.

## 4. End-to-end lifecycle and contracts

### A. Before market open

- Confirm explicit operator authorization, account/instrument/strategy scope, operating state, valid calendar **with official verified coverage**, source rights and API subscription. No auto-upgrade from `CONFIGURED` to `CONFIRMED`.
- Acquire session in a separate trusted auth step; Kite access tokens expire daily, normally next day around 06:00 IST. Never embed key, secret, token or request token in repo, logs, exception traces, prompts, model artifacts or database records.
- Fetch and pin instrument master/token mapping with exchange, segment, trading symbol, stable identity and date; detect token remaps, listing/delisting/series changes, lot/tick-size changes and unsupported instruments. Do not assume token remains stable forever.
- Restore runtime watermark and journal; reconcile Phase 8 ledger/cash/positions/marks, refuse new entries on inconsistency. Do not auto-repair corrupted ledger.
- Preload approved strategy and frozen settings; verify sufficient **permitted, qualified, completed pre-session history** for indicator warm-up. If absent: no signal; no lookahead or synthetic indicator seed.
- Check clock skew, disk space, DB writable transaction, single operator/instance lease, health probes, local timezone and power/sleep. Explicit startup controls for daily/session loss limits and kill switch.

### B. Market-data ingestion

- Read-only WebSocket/REST adapter with bounded reconnect/backoff and network/API quotas. Track token expiry, provider heartbeat, connection generation, event arrival time, exchange time if supplied, stream gaps and missing/duplicate/out-of-order frames.
- Distinguish **sampled quote/tick observations** from full exchange trade tape and official OHLCV candles. WebSocket observations alone cannot guarantee complete minute high/low/volume; label their candles `OBSERVED_FROM_STREAM`, never forge canonical historical quality.
- Maintain per-instrument counters (volume, quote age, gaps), sequence/cutoff evidence, feed health and approved session state. Reject/record out-of-session, stale, malformed, duplicated or future-dated evidence. Never convert data gaps to zero price/volume or fabricate candles.
- Separate historical backfill from live eligibility. Backfilled events do not retroactively trigger orders; they may repair offline research datasets with provenance and audit markers. Compare provider historical candles when available, but do not assume immediate publication or a guaranteed matching API lag.

### C. Candle, feature and proposer

- Schedule by **validated completed candle** and watermark, not just wall-clock minute boundary. Commit stable event ID keyed by instrument + interval + session + candle boundary + source/run identity; crash recovery must not double-trigger.
- Feature calculation must be deterministic and point-in-time. Record feature-set hash, warm-up count, missingness/quality and time cutoff. **No future revised candles in an earlier decision.**
- Active deterministic SMA emits BUY/SELL/HOLD, or separate `NO_DECISION` for missing/untrusted data. Do not confuse a genuine HOLD with a skipped decision or risk BLOCKED.
- Later LightGBM inference operates on a frozen artifact loaded before market open; no retraining per tick or ad hoc feature changes during a session. A model error or expired model blocks its own entries; it must not silently fall back to a different proposer.
- Claude/LLM tooling, if explicitly enabled for research, runs asynchronously with strict permission, rate/budget caps, privacy and timeout; output is a proposal, never trusted market evidence or a command. No API call on Phase 8 execution critical path.

### D. Risk and simulated execution

- Phase 8 remains sole authority for account state, long-only limits, cash, risk caps, daily loss, calendar, quote recency, source use, instrument active status, validation, stop/target and order idempotency.
- A proposal is not an order. Revalidate mutable market evidence and capacity **inside the transaction or equivalent trusted submission boundary**, not solely at UI/proposer time.
- Fill at a distinct available **execution-time observation**, never at the generating candle's close without eligibility. Enforce existing spread/slippage, tick rounding, explicit cost assumptions and liquidity/capacity; paper fills are assumed, not evidence of actual venue execution.
- Preserve the existing all-or-none Phase 8 simulation for the first pilot; don't silently add partial fills, realistic queue priority, shorting or margin. Record simulation limitations and avoid overstated precision.
- Existing Phase 8 transaction/ledger is financial source of truth; journal references it, not duplicates it. Valid risk-reducing SELLs may proceed during ordinary entry halts if all SELL-specific checks pass; a reconciliation hard halt blocks both sides. If required audit persistence or ledger transaction fails, fail closed; do not produce unrecorded simulated fills.

### E. After each decision

- Durable journal for `BUY`, `SELL`, `HOLD`, `BLOCKED`, `NO_DECISION`, and `SYSTEM_EVENT`; link exact evidence, account risk state, proposer, code/feature/model versions, prediction, elapsed stage timings and order result.
- Record outcome labels **later**, in separate immutable/append-only rows with explicit horizon and whether observation was actually possible. No outcome data can reenter earlier inference or prior decisions.
- Evaluate actual simulated ledger P&L and hypothetical counterfactual price movement separately; don't call one the other. Compare shadow proposers on identical cutoff/candle IDs, same tradable universe and costs.

### F. End of day and restart

- Close/reconcile paper ledger, stale marks, open simulated positions and stop/target conditions according to documented policy; no unapproved real-world flattening, no synthetic fills when market is closed.
- Check missing decision IDs and scheduler events, reconcile quote and journal counts, archive immutable run manifest, emit operator-readable data-quality/health report.
- Restart: cold load latest approved config, ensure no duplicate orders, reconstruct state and journal gap classification. Do not backdate fills or infer missing trades. If uncertain, transition to no-new-entry state and require operator review.

## 5. Cross-dimensional risks ('diagonal' review)

| ID | Crossing of systems | Failure mode | Mandatory mitigation / test |
|---|---|---|---|
| X01 | Subscription × runtime | Free Personal API key works for login but no historical/WebSocket market data | Entitlement preflight, explicit denied-permission result; don't buy automatically |
| X02 | Authentication × privacy | Daily token expiry or accidental token leakage | Isolated auth, secret redaction, controlled re-login, no tokens in journal |
| X03 | Calendar × execution | `CONFIGURED` spoofed to `CONFIRMED` or missing special session | Re-read approved configuration at trusted boundary, fail closed; adversarial mutation tests |
| X04 | Candle × research | Observed stream high/low mistaken for exchange-complete OHLC | Data-class/source tags, no automatic promotion, measured provider parity and missing-quote treatment |
| X05 | Event time × trading time | Candle closes on clock but provider evidence arrives late | Finalization watermark, cutoff rules, after-hours rejection, latency categories |
| X06 | Historical data × labels | Future revised bars/corporate actions leak into prior decisions | Immutable as-of dataset snapshots; point-in-time adjustment rules and embargo |
| X07 | Instruments × positions | Symbol rename/delisting, tick or token remap misroutes order | Stable instrument identity, session-date mapping, verification preflight |
| X08 | Network × risk | Feed disconnected with open simulated positions | No new entries; protective-exit retry on fresh quote; operator alert; no invented fills |
| X09 | Restart × idempotency | Replayed candle causes second BUY | Persistent event IDs, transactionally checked proposal/order keys, restart tests |
| X10 | SQLite × audit | Database locked/full while decision is evaluated | Single-writer policy; fail new entries; Phase 8 ledger remains authoritative for exits; recovery marker |
| X11 | Paper price × profitability | LTP treated as executable bid/ask, missed spread and impact | Explicit conservative fill policy, stress/mark distinction, report limitations |
| X12 | Risk state × protective exits | Entry halt traps SELL or retry permanently closes control | Separate entry/risk-reducing exit gates; reconciliation hard halt precedence; retryable exits |
| X13 | Model × journal | Prediction cannot be reproduced after model replacement | Frozen artifact hash, feature schema, code/params and observed input refs |
| X14 | Optimization × leakage | Journal future outcomes fed into historical feature at cutoff | Role-separated outcome tables, temporal query guards, holdout audit |
| X15 | Model competition × capital | SMA/ML shadow performance compared on different observations or shared capital assumed independent | Paired identical decisions, explicit shadow vs executed accounting, no fake executable portfolios |
| X16 | LLM × authority | Prompt injection or malformed JSON becomes market fact/order | Bounded read-only tools, schema, independent trusted evidence resolution, no execution capability |
| X17 | Scheduler × health | No ticks produces no events, silently appears HOLD | Explicit NO_DECISION/gap/health events and bounded per-session coverage reconciliation |
| X18 | Dashboard × safety | UI read turns into write/order or displays stale as current | Read-only query-only path, timestamps and stale labels; AppTest security cases |
| X19 | Local host × reliability | Windows sleeps, clock drifts, process dies, disk fills | Host readiness checks, local time health, startup reconciliation and bounded restart tests |
| X20 | Source rights × retention | API data stored/reused beyond permitted terms | Consent/terms review, retention and export policy, local-only scope; never equate declaration with legal permission |
| X21 | Budget × autonomy | Paid Kite/LLM usage or unexpected broker charges without consent | Operator-authorized spend only; budgets; no AI-triggered purchasing |
| X22 | M2 × paper | Historical sleeve allocator mistakenly used as live risk/capital engine | Explicit stage isolation; Phase 8 owns paper account; M2 remains historical-only |
| X23 | Migration × preservation | Schema upgrade touches normal DB during tests | Temp DB migrations only; backup/recovery plan with separate authorization for normal data |
| X24 | Live state × documentation | Stale roadmap declares rejected/pending code accepted | Dated status, owner-reported evidence qualifiers and precise bounded gates |
| X25 | Evaluation × selection | Model chosen after viewing holdout, multiple-comparison bias | Pre-registered protocol, sealed holdout, attempt ledger and separate promotion review |
| X26 | Execution × time budget | Sub-250ms target claimed without real timings; data-finalization latency ignored | Publish observed p50/p95/p99 end-to-end and per-stage, no hard guarantees before measurement |
| X27 | Data feed × regulatory context | Exchange/provider/API terms or broker retail algo requirements misunderstood | Preflight source terms and restrictions; separate regulatory assessment before *any* real order path |
| X28 | Paper account × open position | Daily restart loses cost basis, existing stop state, realized P&L or risk state | Transactional ledger reconciliation and persistent stop retry state; fail closed on divergence |
| X29 | Config × emergency stop | UI or LLM re-enables halted entries without approval | Operator-only guarded mode and emergency stop, persisted reason, tests |
| X30 | Privacy × reproducibility | Raw LLM prompts/responses disclose secrets, or hashes overclaimed as exact replay | Minimal metadata retention, authorized response snapshots only, explicit replay limitations |

## 6. Auditability and controls matrix

Every important stage requires: a source of truth, stable identity, time of knowledge, failure classification, audit record, negative tests, and an owner/approval boundary.

| Stage | Source of truth | Invariant if unavailable |
|---|---|---|
| Calendar | Approved configuration and verified evidence | No qualifying operational candle or new BUY |
| Source rights/market data | Approved entitlement, source-use and validated observation | No new BUY, no fabricated ticks/candles |
| Identity/time | Pinned instrument mapping and exchange-time cutoff | No evaluation/new BUY on ambiguous instrument/time |
| Prediction | Versioned deterministic strategy or approved immutable ML artifact | No new entry from missing/failed proposer |
| Risk | Phase 8 inside authoritative transaction | No order without validated risk and fresh evidence |
| Financial accounting | Phase 8 transactional ledger and reconciliation | No simulated fill if financial audit cannot persist |
| Decision journal | Durable immutable event refs and reason code | No silently unaudited new entry; retain protective exit safety priority |
| Result claims | Frozen experiment evidence, matched costs and out-of-sample design | No profitability/edge/promotion claim |

## 7. Error handling, observability and operator experience

- Structured, bounded error codes (e.g. `AUTH_EXPIRED`, `CALENDAR_UNCONFIRMED`, `SOURCE_UNVERIFIED`, `STREAM_GAP`, `CANDLE_INCOMPLETE`, `FEATURE_WARMUP`, `MODEL_UNAVAILABLE`, `ENTRY_HALTED`, `ACCOUNT_RECONCILIATION_HALT`, `AUDIT_WRITE_FAILED`); never encode sensitive data in error text.
- Metrics: feed uptime, coverage by expected session, no-evaluation rate, timestamp lag, heartbeat/reconnect counts, model inference latency, Phase 8 validation latency, journal durability lag, rejected proposals, simulated fill delay, ledger health, disk errors, model drift and independent shadow comparison.
- Read-only local dashboard with badges for status (e.g. configured/confirmed, observed/historical, healthy/stale, paper/not-live), clear operator stop and error instructions only if allowed by the permitted runtime; no concealed trading actions.
- Each session produces immutable run manifest containing commit/build, code hash where reliable, schema, calendar, source, strategy, model, feature-set, cost policy, risk policy, operator-approved mode, instrument universe and known limitations.
- Security negative testing: broker order HTTP verbs prohibited, no secret leakage, synthetic prompt injection rejected, dashboard cannot mutate, stale trust evidence cannot be promoted by caller.
- SLO targets must be measured in actual use. Do **not** treat an earlier illustrative 190ms runtime as established. A 1m candle allows an intentionally conservative design; data quality overrides speed.

## 8. Training, LLM and scientific decision discipline

**Any authorized training would be offline.** For LightGBM use structured point-in-time features (returns, volatility, volume, momentum, indicators); a fixed economically interpretable future label/horizon; explicit embargo/purge; chronological development, validation, locked holdout and an attempt ledger. Logistic Regression would test whether complexity adds value. SMA would remain the deterministic benchmark in an authorized evaluation. Compare cost-aware trade outcomes, not accuracy alone.

- Track calibration, uncertainty, sample size, trade frequency, drawdown, turnover, adverse selection and dependence on a few trades. Fees/slippage are assumptions until verified; stress and benchmark choices are pre-registered.
- Make model freeze/promotion manual and independent. No automatic retraining on journal rows and no silent self-modifying risk limits. Distinguish current-universe backtests from point-in-time survivorship-free results.
- Claude can summarize reports or propose hypotheses *outside* the critical path; it neither trains from a one-off candle prompt nor improves numeric predictions by default. Future LLM trading proposal comparison requires an independent protocol and approval.
- Do not choose numerical pass thresholds after looking at the holdout. Unapproved threshold values remain `OPEN`; platform reliability gates (zero real orders, transaction and duplicate invariants) are mandatory irrespective of strategy statistics.

## 9. Detailed missing-work map

### P0 — Necessary before first live-data paper session

1. Reconcile repo facts and complete Checkpoint 2 narrow independent acceptance; leave Git unchanged unless separate commit authorization.
2. Approve exact pilot scope, broker subscription/budget, data use, read-only transport, calendar coverage and authorization mode. No subscription or access under documentation-only scope.
3. Define and implement decision journal contract with durable BUY/SELL/HOLD/BLOCKED/NO_DECISION coverage, outcome separation, source/candle/model/config linking, full failure recovery, no secrets.
4. Prove synthetic streaming end-to-end through validated completed candle, Phase 8 simulation, idempotency, accounting, journal and dashboard with fault-injection/restart tests.
5. Implement and validate read-only Kite adapter under separate implementation authorization: authentication boundary, market data only, instrument master, WebSocket, reconnect, rate limits, snapshots, no order capability.
6. Test source-derived candle quality, gap classification, session/clock handling, historical warm-up provenance and operational calendar approval; reject incomplete/stale evidence.
7. Run explicit observe-only pilot and review evidence before enabling paper order simulation.
8. Conduct supervised one-instrument paper session with a freeze/no-new-entry switch, local monitoring and reconciliation.

### P1 — Needed for evidence and robust expansion

- Longer actual-session observation (candidate 30–60 sessions, not an approved profitability threshold), drift/gap/mark monitoring and operational postmortems.
- Pre-registered cost-aware ML strategy evaluation and paired shadow competition; LightGBM + Logistic Regression + SMA; no live model promotion before acceptance.
- Backup/restore dry run, maintenance, archived manifests, safe schema migrations; calendar renewal and API entitlement review.
- Reconcile and separately review M2-D; M2-E has not started and requires separate authorization before implementation in its approved historical scope and milestone order. M2 work is not a shortcut into live-paper accounting.

### P2 — Deferred

- Broader universe, multiple simultaneous strategies and genuine portfolio paper risk; derivatives, leverage, partial fills, broker execution, latency-critical 1s trading, cloud service, automatic model selection, LLM-driven execution, official real order integration.

## 10. Acceptance gates

| Gate | Evidence to collect | Fail/stop conditions |
|---|---|---|
| G0 Governance | Repo status reconciled, independent Checkpoint 2 acceptance, exact owner scope approved | Stale/contradictory state or unauthorized work |
| G1 Synthetic trust | Calendar provenance, source-use, quality, journal, Phase 8 and restart adversarial tests | Privilege bypass, duplicate order or misbound evidence |
| G2 Broker read-only | API entitlement and terms verified, token protection, forbidden-order-verb negative tests | Any reachable real-order path, secrets exposed |
| G3 Observe-only | Real feed quality, timestamp/coverage metrics, no order use, gap handling and daily operator reports | Unconfirmed calendar, unchecked source rights, unstable/gapped feed |
| G4 Supervised paper | Approved mode, fresh real evidence, valid Phase 8 account, ledger reconciliation, complete journal, kill switch | Unlogged order, stale data order, bad ledger, ambiguous position |
| G5 Strategy/ML evaluation | Frozen manifest, legal dataset, untouched holdout, paired baselines, after-cost metrics, uncertainty | Leakage, cherry-picked results, absent costs, repeated holdout tuning |
| G6 Any broader promotion | Independent scope review plus explicit user approval | No automatic leap from backtest/paper to live trading |

## 11. Open decisions with recommended defaults (recommendations are **not approvals**)

| Decision | Architectural recommendation | Resolution needed |
|---|---|---|
| Pilot universe | One liquid NSE EQ instrument, long-only, no leverage/short/derivatives | Owner selects permitted instrument before live connection |
| Interval | Completed 1-minute candle only initially | Verify live observation fidelity and processing lag |
| Strategy | Existing versioned SMA as active; other proposers shadow | Freeze exact parameters at pilot approval |
| Execution | Phase 8 all-or-none simulation, fresh next eligible quote, conservative cost model | Exact quote/fill timing and capacity assumptions documented |
| Overnight positions | No implicit carry/forced square-off policy | Decide paper-account delivery/intraday/EOD policy, gap-risk handling and restart semantics before first BUY |
| Market microstructure | Exclude auction/pre-open, halts, unusual trading sessions and circuit-bound situations until explicitly supported | Review exchange session types and permitted quote/fill semantics |
| Inference freshness | Freeze the allowed time between finalized candle, prediction, risk requalification and execution quote | Set from measured feed latency, not assumed 190ms pipeline |
| Risk sizing | Approve explicit *simulated* capital, per-order/per-position exposure, loss caps and stop/target rules | No silent defaults and no real funds |
| API | Paid Kite Connect data access if user approves ₹500/month (verify current terms/entitlement) | Explicit authorization; free Personal plan insufficient for live/history |
| Calendar | Maintain official NSE cash date coverage into 2027; keep fail-closed until truly `CONFIRMED` | Admin evidence and documented approval |
| Source rights | Confirm private analytical use, local retention/terms; capture declaration | User/legal terms verification |
| ML | LightGBM candidate with Logistic Regression and SMA baselines; offline, shadow-only | Dataset and evaluation thresholds preregistered |
| LLM | Not required, optional offline/async; no decision authority | Separate value/cost review before adding subscription |
| Outcome horizons | Illustrative 5/15/60 *trading-minute* outcomes, no artificial overnight extrapolation | Freeze before study; report censored horizons |
| Performance thresholds | Do not invent Sharpe/drawdown/win-rate cutoff | Approved research protocol before promotion |
| Session SLO | Publish measured lag/coverage metrics; do not promise <250ms | First observe-only measurements and justified limits |
| Journal retention | Local minimal durable metadata and source refs, no credentials; retention per data rights | Approved exact retention/export policy |
| Real trading | Default NO-GO | Separate later legal/security/operational assessment and explicit authorization |

## 12. Documents and source references

Current navigation: [handoff](./PROJECT_HANDOFF_2026-11-18.md),
[ML/optional LLM plan](./ML_LLM_AND_EVALUATION_PLAN.md),
[Decision Journal v1](../tasks/DECISION_JOURNAL_V1.md), and
[live-data paper MVP](../tasks/PHASE_10_LIVE_DATA_PAPER_MVP.md).
The bounded MVP does not replace the
[existing full Phase 10 task](../tasks/PHASE_10_LIVE_DATA_FORWARD_PAPER_TRADING.md)
or waive its observation, multiple-strategy, fill-model and AI evaluation requirements.

Local project docs examined (2026-11-15 archived snapshot): `PROJECT_CHARTER.md`, `PROJECT_STATE.md`, `ARCHITECTURE_DECISIONS.md`, `OPEN_DECISIONS.md`, `AI_RESEARCH_PROTOCOL.md`, `DEVELOPMENT_ROADMAP.md`, `SYSTEM_ARCHITECTURE.md`, `VALIDATION_AND_ACCEPTANCE.md`; also a `CLAUDE.md` file from the conversation. Their exact current repo versions must be checked before integration.

Official external references (checked 2026-11-17):
- https://support.zerodha.com/category/trading-and-markets/general-kite/kite-api/articles/what-are-the-charges-for-kite-apis
- https://support.zerodha.com/category/trading-and-markets/general-kite/kite-api/articles/historical-data-and-live-market-data-payment-plan
- https://zerodha.com/products/api
- https://kite.trade/docs/connect/v3/websocket/
- https://kite.trade/docs/connect/v3/historical/
- https://kite.trade/docs/connect/v3/user/

Prices, API limits and terms may change. Reconfirm at activation. Any use of broker API or private data requires explicit prior authorization.
