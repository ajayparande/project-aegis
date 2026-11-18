# Phase 10 — Live-Data Paper Trading MVP: staged specification

**Status:** PLANNED / PENDING APPROVAL; NOT IMPLEMENTED OR IMPLEMENTATION-AUTHORIZED.
**Prepared:** 2026-11-17.
**Purpose:** precise scope, contracts, testing and safe release gates for the first one-instrument supervised live-data paper exercise. This new task does **not** replace the established Phase 10A synthetic or Phase 8 design, and does not grant permission to use a paid API, normal database or live broker data.

This bounded MVP does not complete or amend the
[existing full Phase 10 task](./PHASE_10_LIVE_DATA_FORWARD_PAPER_TRADING.md), including
its AI evaluation, multiple-strategy, fill-model and 30–60 actual-session criteria.
Any approval of this narrower exercise would not waive those broader acceptance gates.
Claude remains optional and excluded from the critical execution path. LightGBM is
the selected research candidate, not a validated trading model or MVP prerequisite.

## 1. Success definition and explicit exclusions

**Success:** one permitted NSE cash-equity instrument, completed 1-minute observations, deterministic strategy proposals, trustworthy market/session/source checks, Phase 8 long-only simulated orders, complete decision audit, reproducible simulated ledger, startup/restart recovery and read-only operational monitoring. Observed actual live market data may be used only after separately authorized access and terms review.

**Not required for this milestone:** LightGBM training, Claude/API subscriptions, M2-E historical portfolio valuation, multiple instruments, cloud, 1-second trading, partially filled paper orders, shorting, derivatives, leverage or real broker order placement. No profitability claim is implied.

**Never implement or invoke** real order placement from this scope. `RESEARCH_ONLY` remains unchanged until a separately approved observation/paper mode; even then no broker real-order mode is introduced.

## 2. Target architecture and API isolation

Use existing `src/aegis/forward_runtime.py` provider-neutral contracts and Phase 8 APIs where available. Avoid duplicate signal generators, new financial accounting engine, and hidden imports of M2 research code.

Define a **read-only broker market-data port** (names below are suggested, adapt to code while preserving behavior):

- `BrokerMarketDataPort`: `load_instruments`, `get_historical_candles`, `subscribe_quotes`, `health`, `close`. Auth/login is an explicit separate operator-managed process.
- `KiteReadOnlyMarketDataAdapter`: **only** approved HTTP GET market-data requests and WebSocket market-data handshake/subscriptions; rejects disallowed methods/routes. Don't wire `place_order`, `modify_order`, `cancel_order` or generic request passthrough into paper runtime. SDK alone may expose order methods, so test actual transport isolation.
- `ObservedQuote`: instrument identity, provider instrument token, exchange timestamp when supplied, receive time UTC, normalized price, available volume/last trade fields, connection generation, source ID and integrity/quality flags.
- `MarketDataCandle`: interval and exchange-local boundaries, observed OHLCV derivation method, first/last observed quote, seen/missing counters, quality status, provider/source session ID, exact provenance and confirmation status. **Do not label tick-aggregated candle as exchange-complete without evidence.**
- `CompletedCandleEvent`: stable identity, finalized at, information cutoff, source/candle IDs, calendar and quality fingerprint and event generation. Recovery is idempotent.
- `ForwardDecision`: one of `BUY`, `SELL`, `HOLD`, `BLOCKED`, `NO_DECISION`; proposal and journal must bind the same point-in-time evidence.

REST and WebSocket rate limits, pagination/window sizes and historical availability must be read from current Zerodha docs and captured as runtime/provider settings; do not hardcode values based on forum guesses. Apply bounded retry, exponential backoff/jitter and explicit `RATE_LIMITED` health.

## 3. Work packages and acceptance

### P10-0 — Reconciliation and preflight (read-only planning gate)

- Verify live repo state, existing Phase 8/10 API boundaries, accepted milestone history, existing dirty changes and documentation statuses. Record exact source evidence and conflicts.
- Get narrow independent Checkpoint 2 acceptance for calendar trust and public API compatibility. No new exploratory audit cycle.
- Define operator scope: instrument, source permissions, calendar dates, use of one approved paper account and risk policy, maximum financial model assumptions, deployment host and explicit consent to access broker API.
- **Acceptance:** dated gate checklist and no unapproved code/credentials/mode changes.

### P10-1 — Decision audit foundation (synthetic only)

- Implement `tasks/DECISION_JOURNAL_V1.md` using isolated test DB and migration review. No mutation to normal DB without separate authorization.
- Journal every evaluated completed candle and every missed evaluation (`NO_DECISION`), including HOLD and blocked order attempts. Link Phase 8 immutable IDs.
- Test fail-closed new entries if necessary journal write fails. Preserve risk-reducing exit priority and Phase 8 ledger atomicity; no unrecorded fills.
- **Acceptance:** all decision state transitions, immutability, duplicate suppression, recovery and secret-redaction tests pass.

### P10-2 — Full synthetic end-to-end safety proof

- Reuse existing synthetic quote provider and streaming aggregation: connected/disconnected, missing minute, incomplete bar, duplicate/out-of-order tick, out-of-session, stale provider, reconnect and restart.
- Verify exact source-use declaration, approved calendar reload, quality and cutoff at the final trusted execution boundary.
- Deterministic SMA produces proposal, Phase 8 accepts or rejects and ledger remains balanced; simulated execution references a newer available quote, not a future candle.
- Validate stop/target exits and retry after rejection; ordinary entry halts do not suppress valid risk-reducing exits; reconciliation halts remain absolute.
- Dashboard remains query-only/read-only. Generate a synthetic replay + decision-journal cross-check report.
- **Acceptance:** stable event IDs, no duplicate fills, no fabricated candles or missing decisions, all accepted paper financial events reconciled.

### P10-3 — Read-only Kite Connect market-data adapter (separate approval)

- Check plan capability: currently advertised free Personal API lacks WebSocket/historical; paid Connect about ₹500/month includes both. Owner must explicitly approve subscription and API use before any external connection.
- Authentication: operator login to obtain token; daily expiry handling; local secrets storage under approved environment/OS credential boundary; safe invalidation and re-auth prompts. Do not auto-login using broker credentials or bypass 2FA.
- Symbol/token identity resolution; approved NSE EQ only; provider version and instrument master snapshot; clock and session controls.
- WebSocket market-data validation: binary decoding, packet framing, connection generation, receive timestamp, stale/out-of-order quotes, heartbeat, bounded reconnection, subscriptions and lost-session health. Historical REST is read-only, source-tagged, rate limited and pagination checked.
- Security test enforces zero reachable real-order request paths, including HTTP method/URL spies, accidental SDK method access, redirects, retry wrappers and AI tool registry.
- **Acceptance:** offline fake HTTP/WS tests first; then explicit operator-approved sandbox-like **observe-only** connection with zero simulated or real orders. Real brokerage endpoints untouched.

### P10-4 — Candle fidelity and point-in-time readiness (observe-only)

- Build/observe market data sessions and report timing, completeness, expected-vs-observed minutes, gaps, backfill publication delay, quote coverage and data provenance.
- Confirm official NSE cash market calendar and special sessions for intended dates; `CONFIGURED` is **not** sufficient for operational permission.
- Establish **documented policy** for stream-observed candles versus Kite historical minute candles: do not fabricate full true OHLC from sparse updates. If comparison detects material mismatch or observed completeness is inadequate, block signal eligibility until a trustworthy source/policy is approved.
- Establish minimum history for SMA warm-up from independently authorized qualified historical candles, not a current snapshot or post-candle repair.
- Retrospective backfill is for analysis/reconciliation, **never retroactive entry**. Health failures produce `NO_DECISION`, not HOLD.
- **Acceptance:** measured data-quality and gap evidence on actual market sessions, with documented source provenance and no real/paper orders in observe-only stage.

### P10-5 — Supervised paper-only session (separate approval)

- Operator explicitly authorizes one account, instrument, SMA version and settings. Enable Phase 8 simulated orders only after all G0–G3 preflights and confidence checks pass.
- Start with low-sized **simulated** capital; use explicit max simulated exposure, per-order size, daily loss, risk controls, stop/target and kill switch. These are simulated economics; require user risk-policy approval before any actual session.
- Freeze the session policy for pre-open, regular session, closing auction, circuit/volatility halt, pre/post-market trades, and **overnight carry versus end-of-day paper close**. Never assume an EOD liquidation fill without an actual permitted fresh quote; explicit gap-risk/overnight marks and next-session requalification are required if carry is allowed.
- Record entry/exit and HOLD/BLOCKED/NO_DECISION for every expected session event; all paper fills and P&L through Phase 8 ledger; reconcile at end of session and after restart.
- No outside-hours action, no retry backdating, no order if quote or calendar stale/ambiguous, no model or LLM dependency.
- **Acceptance:** supervised run report: market/data quality, event coverage, journal completeness, source/time bindings, fills/cost assumptions, retry/kill behavior and ledger reconciliation. No performance claim.

### P10-6 — Evidence period and go/no-go

- Proposed 30–60 actual trading sessions of monitoring (not a universal strategy pass cutoff); report outages, operator interventions, missed decisions, simulated P&L, mismatch rates, measured timings and defects.
- Formal acceptance considers operational safety and fidelity independently of hypothetical profit. Research-model promotion is **not** implied by the observation period.
- Require separate authorization to widen scope; any broker live-order work remains outside this task and default NO-GO.

## 4. State machine and failure behavior

Suggested states: `DISABLED`, `PREFLIGHT`, `OBSERVE_ONLY`, `PAPER_ELIGIBLE`, `ENTRY_HALTED`, `RECONCILIATION_HALTED`, `STOPPED`. Approved implementation may map to existing states but must preserve distinction:

- **Entry halted** for stale feed, invalid calendar, unqualified source, insufficient history, feature failure, model unavailable, audit persistence and soft risk limits. No BUY. Revalidate risk-reducing SELLs separately with fresh evidence, within existing Phase 8 policy.
- **Reconciliation halted** for contradictory order/fill/ledger/account state: block both directions; no auto-repair.
- **Stopped**/connection failure: no new entries or invented fills, preserve pending provenance and restart reconciliation; only permitted Phase 8 actions on verified evidence.
- **Observe-only:** cannot submit simulated Phase 8 orders.
- **Live real orders:** no state/path in MVP.

## 5. Operational readiness controls

- Start-of-session checklist: calendar/source/license, app subscription/rights, current broker auth, instrument identity and tick size, feed health, clock, warm-up, approved settings, storage, schema version, single instance lock, risk limits, account reconciliation, journal writer and rollback/kill plan.
- During session: bound memory, queues and buffers; measure backlog; if behind, stop new entries rather than drop silently or act on stale decisions. No API storms/reconnect loops. Track provider health per instrument; watch time monotonic for elapsed metrics, timezone-aware UTC for persisted event time.
- End-of-session: expected vs actual candle IDs, journal and ledger reconciliation, open paper position mark age, unresolved exits, clock anomalies, provider downtime, daily cost budget and risk outcomes.
- Host: Windows local power/sleep, network variability, antivirus/OS updates, one active process, backup/recovery test on disposable copy, no mandatory Docker/cloud/WSL.
- Paper model: clearly label last-price valuation vs hypothetical liquidation and executable bid/ask; Phase 8 prices are simulated assumptions, not true fills.

## 6. Automated test matrix

| Case | Required assertion |
|---|---|
| Calendar `CONFIGURED` + caller-supplied `CONFIRMED` | No trade eligibility |
| Unauthorized/expired/absent market-data entitlement | No feed accepted; no order |
| Denied source-use or incorrect instrument mapping | No signal/paper entry |
| No opening tick or missing minute | No fabricated candle, NO_DECISION/health |
| Incomplete candle at boundary | No signal before validated finalization |
| Duplicate/backdated/out-of-order ticks | Quarantine or deterministic idempotent behavior |
| Historical revision/backfill after cutoff | Cannot alter earlier recorded decision or retroactively trade |
| WebSocket reconnect during a pending candidate | Revalidate quote/time and avoid double order |
| Crash after journal pre-commit before Phase 8 | Recovery yields either consistent rejection or one order |
| Crash after Phase 8 fill before dashboard read | Exactly one fill and coherent ledger |
| Disk-full, DB-busy, journal writer unavailable | No new unaudited entries, alert; safe exit precedence |
| Feed data halted with open position | Protective exit logic remains retryable when permitted fresh quote returns |
| Account ledger tamper | Reconciliation hard halt; no auto-fix |
| After-hours / special holiday session | Approved calendar policy enforced |
| Price tick/volume/cost anomaly | Fail closed or record explicit blocked reason |
| LLM tool response malicious prompt / unavailable | No direct broker/Phase 8 authority and no critical-path delay |
| Direct attempted broker POST / order endpoint | Blocked by adapter transport; zero actual calls |
| Stream vs historical candle discrepancy | Classified, not silently normalized into valid quality |
| Shadow ML differs from active SMA | Shadow evidence recorded, cannot change Phase 8 active proposer |
| Schema migration simulation | Old data preserved, rollback intact on disposable DB |
| App/dashboard | Read-only, no credentials shown, operational status fresh/stale labeled |

## 7. Evidence at each gate

Minimum deliverables: exact code + test diff, isolated test commands/results, coverage/negative probes, real Git before/after status, schema migration/rollback evidence, operator checklists, sample journal and daily report with synthetic nonsecret data, known limitations, reviewer verdict and explicit authorization state. **No test count alone implies acceptance.**

## 8. Budget, timing and dependencies

- Brokerage subscription: official Kite API product/support pages advertised ₹500/month paid access at review; user must authorize charge separately. Free Personal app has no market data or history. Confirm billing at actual enrollment.
- Initial work can be entirely synthetic and subscription-free.
- Delivery estimates depend on existing source quality and available official calendar/data evidence; do not present speculative fixed-hour estimates as commitments.
- Work should be implemented in coherent, testable, inexpensive increments rather than repeated broad candidate audits or commit-splitting exercises.

## 9. Related documents

- [Architecture review](../docs/ARCHITECTURE_360_REVIEW_2026-11-17.md).
- [Decision Journal v1](./DECISION_JOURNAL_V1.md).
- [ML/optional LLM plan](../docs/ML_LLM_AND_EVALUATION_PLAN.md).
- [Next-session handoff](../docs/PROJECT_HANDOFF_2026-11-18.md).
- Controlling [AGENTS](../AGENTS.md), [CLAUDE](../CLAUDE.md), [approved decisions](../docs/ARCHITECTURE_DECISIONS.md), [open decisions](../docs/OPEN_DECISIONS.md), [research protocol](../docs/AI_RESEARCH_PROTOCOL.md), [acceptance criteria](../docs/VALIDATION_AND_ACCEPTANCE.md), [Phase 8](./PHASE_08_PAPER_TRADING.md), [full Phase 10](./PHASE_10_LIVE_DATA_FORWARD_PAPER_TRADING.md), and [M2](./M2_PORTFOLIO_ACCOUNTING.md).
