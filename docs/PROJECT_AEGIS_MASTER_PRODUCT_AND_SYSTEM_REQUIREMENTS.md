# PROJECT AEGIS
## Master Product Vision, Functional Requirements, Engineering Constraints, and Acceptance Criteria

**Document type:** Future-state product and system requirements specification (PRD + system requirements + AI-agent policy + assessment protocol)
**Audience:** Human project owner, Claude, Codex, GitHub Copilot, GPT, Gemini, and any future engineering or audit agent
**Project:** Project Aegis — autonomous AI-agentic live trading for NSE cash equities, supported by quantitative research, paper validation, independent Python execution controls, and a simple trading cockpit
**Primary market:** Indian NSE cash equities
**Implementation philosophy:** Python-first, local-first, deterministic safety controls, evidence-driven experimentation, provider-independent components
**Normative language:** **MUST** = mandatory; **SHOULD** = strongly preferred, with documented exception; **MAY** = optional/conditional
**Scope of this document:** WHAT THE FINISHED SYSTEM IS REQUIRED TO DO, including safety and quality constraints—not a statement of repository implementation or acceptance status.

> **Critical interpretation:** This document intentionally does **not** label any feature as implemented, missing, accepted, partially accepted, or planned. Such labels must be produced separately by inspecting the actual repository, executing only authorized tests, comparing evidence against the requirements here, and documenting the findings. A specification is not proof that code exists, works, is profitable, or has been approved.
>
> **Financial reality:** No system can guarantee profitable trades or correctly predict future highs/lows. Aegis's objective is to discover and validate trading policies that improve **risk-adjusted, after-cost expected returns** while respecting capital-preservation, data-quality, and operational constraints. The system must be capable of concluding that **NO TRADE** is the best action, or that no strategy demonstrates a credible edge.

---

# PART A — PURPOSE, BOUNDARIES, AND PRODUCT PRINCIPLES

## A01. End-state vision

**The required final product is an autonomous, AI-agentic LIVE trading system for real NSE cash-equity orders and real capital**, not a permanently simulated research application. Once the owner deliberately enables an accepted live deployment with bounded strategy, broker/account, symbol, capital, risk and operating permissions, Aegis SHALL independently monitor the market, evaluate opportunities, decide BUY/SELL/HOLD/NO_DECISION, size positions, route eligible real broker orders through Python's independent execution controls, monitor acknowledgments and fills, manage exits and protective actions, reconcile actual holdings/cash, and explain results in an approachable dashboard. **Routine eligible trades MUST NOT require a fresh human click or approval.**

Research, synthetic paper trading, real-data paper trading and a limited supervised real-capital pilot are **engineering, validation and rollout stages toward this committed live-autonomous end state**. They are not substitutions for it. Progression to real capital requires deliberate one-time/per-configuration authorization and evidence-based operational readiness, but once a live authorization is valid the agent may operate continuously inside that authorization until it expires, is revoked, or a safety halt occurs. This document describes the desired future system; it does not authorize connecting an order-capable broker account today.

Aegis is intended to serve a non-expert human owner. It must teach what signals and risks mean, make each decision explainable, expose whether the data is trustworthy, and turn complex quantitative analysis into actionable, understandable reports. It must not hide uncertainty behind confident language or attractive charts.

The required **production trading loop** (after controlled rollout) is:

```text
PERMITTED LIVE NSE DATA → SOURCE/CALENDAR/FRESHNESS CHECKS → COMPLETED CANDLES/QUOTES
             → DETERMINISTIC HIGH/LOW, MARKET STRUCTURE, TREND, VOLUME AND RISK FEATURES
             → AI/LLM TRADING AGENT + VALIDATED STRATEGIES + LOCAL ML (AS CONFIGURED)
             → GROUNDED BUY / SELL / HOLD / NO_DECISION PROPOSAL + EXIT PLAN + EVIDENCE
             → INDEPENDENT PYTHON RISK / CASH / AUTHORIZATION / EXECUTION VALIDATION
             → APPROVED LIVE BROKER GATEWAY → REAL ORDER ACK/FILL/STATUS RECONCILIATION
             → AUTONOMOUS POSITION MONITORING, STOP/TARGET/EXIT AND REBALANCE AS ALLOWED
             → REAL ACCOUNT LEDGER / LIVE P&L / JOURNAL / SIMPLE OWNER DASHBOARD
             → OUTCOME ATTRIBUTION, DRIFT DETECTION, CONTROLLED STRATEGY LEARNING
             → NEXT ELIGIBLE MARKET EVENT (OR PAUSE/HALT WHEN SAFETY REQUIRES)
```

**Single-product, two-execution-mode requirement (non-negotiable):** Aegis MUST be engineered from the beginning as ONE complete, continuously operating AI trader with one canonical market-data/feature/strategy/ML/LLM/decision/risk/order-intent/position-management/monitoring/UI pipeline. **`execution_mode: PAPER` and `execution_mode: LIVE` select distinct execution gateways behind the same trusted interface**, not different decision engines, two projects or an unfinished paper prototype followed by another major live-trading software build. In both modes, the agent performs the same autonomous analysis and generates the same type of validated orders. The paper gateway records simulated broker events; the live gateway communicates with an actual authorized broker and reconciles broker responses. Independent broker-state truth, real fills, credentials and money must not be pretended to be interchangeable.

**The next major product milestone is FEATURE-COMPLETE LIVE-MARKET-DATA AI PAPER TRADING:** all intended user-facing trading functionality, high/low skills, strategy analysis, ML, LLM agency, automatic BUY/SELL/HOLD, portfolio decisions, risk, P&L, continuous operation, recovery and finished simple UI must be included. The common execution contract AND live broker adapter/state machine must already be **implemented and tested with broker mocks/sandbox/authorized non-order checks** by the time paper is declared feature-complete. No separate major application development work is planned for after this release. Once the owner confirms paper results, the deployment passes specific real-execution, compliance, broker-account and financial-safety readiness checks, and a bounded live trading grant is explicitly activated, **switching to actual orders is a controlled execution-mode configuration change**, not a code rewrite. Readiness issues discovered in broker staging may still require fixes. A paper-only test pass alone NEVER authorizes real-money orders. Routine trades within the subsequently enabled live scope need no human approval.

Aegis is **not** a magical prediction engine, a signal-selling service, a promise of high returns, an unrestricted self-modifying trading bot, or an LLM with direct broker-order privileges.

## A02. Primary outcomes

- **O-01 Understand the market:** Detect trends, consolidation, volatility, intraday structure, relative strength, support/resistance, volume behavior, liquidity, potential breakouts and failures, and clearly distinguish observed facts from forecasts.
- **O-02 Choose when NOT to trade:** Detect low-quality, ambiguous, stale, thin, excessively volatile, unsupported, or unprofitable setups and safely HOLD/BLOCK.
- **O-03 Form candidate trade plans:** For eligible instruments, propose a justified direction, entry context, stop, target or exit condition, expiry, sizing context, and evidence references.
- **O-04 Compare decision intelligence:** Benchmark deterministic rules, statistical baselines, lightweight ML and the **core LLM/agent trading policy** fairly, with identical information cutoffs and realistic after-cost performance.
- **O-05 Protect capital:** Independently enforce exposure limits, cash, fees, drawdown limits, stale-data halts, calendar integrity, authorization, and account reconciliation.
- **O-06 Rehearse faithfully before live deployment:** Replay historical paths and use real-time market-data paper execution with realistic costs, independently reconciled accounting and no real orders during test stages.
- **O-07 Learn experimentally:** Analyze winning and losing decisions, propose controlled changes, run reproducible tests, and promote only independently accepted candidates.
- **O-08 Be approachable:** Offer clear, beginner-friendly UI with advanced drilldowns; explain signals, losses, risk and uncertainty in plain language.
- **O-09 Survive faults:** Handle disconnects, crashes, restarts, retries, duplicated messages and corrupt/inconsistent state without duplicate real-money orders, lost position protection or silent loss of evidence.
- **O-10 Stay budget-conscious:** Prefer local CPU workloads, inexpensive or free approved data and AI options, and explicit cost monitoring and opt-in for paid services.
- **O-11 Execute autonomously in production:** Following deliberate deployment authorization, independently identify opportunities and submit/manage eligible real BUY/SELL orders through the broker gateway, without per-trade human permission; obey hard limits and halt safely when required.
- **O-12 Operate and explain real performance:** Display authoritative live account cash/equity, net P&L by Today/Week/Month/Year/Custom, real fills/open orders/positions, agent reasons and exceptional alerts without confusing these with paper results.

## A03. Supported personas and experience

1. **Beginner/operator:** Wants to see what Aegis is observing, whether the market is open, what the agent decided and actually bought/sold/held, why, what happens to **real or paper account money**, and whether the system is safe; should not need specialist trading knowledge.
2. **Researcher:** Creates experiments, defines hypotheses, selects data windows/strategies, reviews backtests, compares candidates, tests sensitivity, and reads reproducibility manifests.
3. **Engineer/auditor:** Inspects precise evidence, schema, source provenance, parameter/clock/model versions, database transactions, tests, state-machine invariants and failure behavior.
4. **Live owner/operator:** Explicitly enables or disables a reviewed autonomous live configuration, controls capital/risk/universe and retains pause/kill-switch authority. Does **not** have to manually confirm each normal eligible order.

## A04. Market and instrument scope

- First supported asset class: **NSE cash equity, long-only, unleveraged**.
- Initial approved trading universe must be operator-configured, allowlist-based, and narrowly scoped (begin with one instrument; expand deliberately to approximately 5–10 approved equities when separately qualified).
- Use stable instrument identifiers including exchange, segment, symbol, broker mappings, tick size, lot/quantity rules, trading status and effective dates.
- Prevent any decision, risk allocation or order for an instrument outside the approved universe.
- Support common research candles of 1 minute and 5 minutes, plus configurable higher periods and daily data, while distinguishing research intervals from intervals qualified for executable decisions.
- Do not silently introduce options, futures, margin, intraday leverage, short-selling, forex, crypto, derivatives or cross-border trading. Any expansion requires a separately documented product, financial, legal, data and risk scope decision.
- Example synthetic fixtures may use NSE:INFY, NSE:RELIANCE and NSE:TCS; fixtures must never become implicit production universe authorization.

## A05. Budget and operating assumptions

- Local-first development on a Windows 11 machine using Python 3.11-compatible tooling and a reproducible virtual environment; architecture should allow migration to a controlled Linux/VPS host if justified.
- Target low-compute implementation; avoid mandatory GPUs or large-model training.
- Keep initial **project/service spending** within the owner's approximate ₹10,000 ceiling unless explicit approval raises it; this is distinct from the configurable hypothetical paper-account cash or any future real trading capital.
- Model paper capital as a deliberately configurable amount (₹10,000 is one useful synthetic test scenario), never as a guarantee of available real broker funds.
- Prefer one AI provider at first, use local deterministic mock providers for tests, monitor usage/token/API charges, and do not trigger paid API usage from tests or autonomous experiments without approval.
- Keep all backtesting and paper trading free of broker-order capabilities by construction, not merely by an unchecked UI toggle.

## A06. One product, two execution modes, and enforceable safety boundaries

**The owner-facing architectural model is a SINGLE AI trading product with TWO execution modes:**

- **`PAPER`** — uses *real, qualified market data* and the production-equivalent agent, signals, sizing, Python risk, order intents, management and UI; the `PaperExecutionGateway` returns simulated broker-like order/position/fee events but has **zero order-capable broker authority**.
- **`LIVE`** — uses the **same qualified live market data, same agent/ML/strategy bundle, same event-driven trading engine, same high-level risk policies, same order-intent contract, same UI and same position-management logic**, but the `LiveBrokerExecutionGateway` submits real exchange orders through an authenticated broker adapter and uses **actual broker-confirmed state** for settlement, fees, positions, open orders and funds.

`RESEARCH_ONLY`, `SYNTHETIC_PAPER`, `LIVE_DATA_PAPER`, `SUPERVISED_LIVE`, and `AUTOMATED_LIVE` can remain *research/deployment stage names* or compatibility labels, but MUST NOT be interpreted as separate incompatible applications or duplicated trading engines. A synthetic source is a test-data configuration, **not a third production financial execution mode**. The final owner-facing choice is plainly `PAPER` versus `LIVE`, always paired with an explicit `data_source` and account identity. `LIVE` is not permitted against synthetic or unqualified data.

### A06.1 Required unified trading flow

```text
                        VERIFIED LIVE MARKET DATA
                                  |
                      ONE CANONICAL EVENT STREAM
                                  |
                FEATURES + TECHNICAL ANALYSIS + STRATEGIES
                                  |
                   LIGHT ML + LLM/AI TRADING AGENT
                                  |
                      ONE BUY / SELL / HOLD PROPOSAL
                                  |
                 ONE PYTHON RISK + ORDER-INTENT CONTRACT
                                  |
                        EXECUTION GATEWAY INTERFACE
                             /               \
             execution_mode=PAPER       execution_mode=LIVE
                        |                        |
              PaperExecutionGateway     LiveBrokerExecutionGateway
              simulated broker events   actual broker order API
              simulated fills/fees      broker ACK/fills/fees
                        |                        |
                   MODE-BOUND ACCOUNTING + RECONCILIATION
                                  |
                    ONE UI / JOURNAL / P&L EXPERIENCE
```

- No `if paper: use SMA / else: use AI` divergence. The agent and strategies must not know which financial writer will receive the order, other than mode-specific factual account/fill information supplied through approved interfaces.
- The *same immutable order-intent schema, idempotency key rules, time cutoffs, position sizing and portfolio safety policy* must apply to both gateways. A broker adapter may report actual uncertainty that the paper adapter models; these differences are truthfully represented rather than masked.
- There must be **one canonical, versioned application layer** for entry, exit, partial-fill-aware state transitions, risk, journals, alerts and UI; gateway implementations isolate only their genuinely different execution/account-provider responsibilities. Do not implement a second decision engine or alternative risk policy for real trading.
- `PAPER` must support **real-time live data, continuous AI decisions and paper fills**, and be production-feature-complete rather than a skeletal test harness. All promised user-facing features shall work before feature-complete paper acceptance.
- The `LIVE` gateway, normalized broker order-state machine, real account reconciliation, failure handling and authorization/configuration wiring MUST be built and contract-tested **before** feature-complete paper milestone acceptance. The paper mode itself must NOT be given a broker-order credential or submit live orders during acceptance tests.

### A06.2 Configuration switch design

The application SHALL have one validated config key such as:

```yaml
# Illustrative shape only, not an existing repository configuration.
trading:
  execution_mode: PAPER   # PAPER | LIVE
  market_data_source: APPROVED_LIVE_FEED
  strategy_bundle: approved_agent_policy_version
  account_profile: paper_test_account
  live_authorization_ref: null
  new_entry_policy: ENABLED
```

**Targeted mode transition after readiness** (illustrative values; not instructions for the current codebase):

```yaml
trading:
  execution_mode: LIVE
  market_data_source: APPROVED_LIVE_FEED
  strategy_bundle: approved_agent_policy_version
  account_profile: approved_real_broker_account
  live_authorization_ref: externally_issued_bounded_live_grant
  new_entry_policy: ENABLED
```

- The **software mode selection is a configuration change**, not a code change, redeployment of a different agent, migration to a different strategy, new UI, separate portfolio engine or additional feature implementation.
- The mode change also requires an **authenticated, owner-authorized live account/grant**, not merely a plain-text `PAPER`→`LIVE` edit. An absent, forged, invalid, outdated or expired grant, missing credentials, unresolved broker state or noncompliant broker environment must block orders even if `execution_mode: LIVE` is present.
- Only a human/authorized deployment operator may grant, activate, switch, pause or revoke real trading authority. No trading LLM, coding LLM, strategy or UI text input may independently promote the mode.
- Switching from PAPER to LIVE **must not** carry over paper positions, cash, fills, orders, order authorizations, P&L or simulated stop controls as if they were real. At activation, ingest broker-authoritative real funds/positions/open orders and reconcile them as the real account starting point.
- Live mode transition must be atomic/audited, mutually exclusive for a financial account/run identity, and blocked if the existing runtime has unresolved events that could be replayed as real orders.
- The safe default is PAPER or RESEARCH_ONLY when authorization/capabilities are not proven; repeated restarts never silently activate real-money orders.
- Once a verified live session is active, **normal qualifying BUY/SELL/HOLD and protective exits operate autonomously** with no per-trade human approval. Manual operator action is for initial enablement, changed risk scopes, exceptions and emergency halts.

### A06.3 Required parity tests and irreducible real-broker differences

Before paper milestone ACCEPT, the implementation must demonstrate:

1. **Same-input parity:** Identical immutable observation, feature, agent/model and portfolio policy artifacts go through both execution modes up to the exact financial gateway dispatch boundary; order-intent hash and reason codes agree when account snapshots are equivalent. Run via safe mocks, never the real broker in automated tests.
2. **Execution-contract parity:** Paper gateway simulates normalized ACK, open, partial fill, rejection, cancellation, delayed fee and unknown-outcome states; live adapter contract tests exercise the identical public state-machine invariants with broker mocks or approved sandbox.
3. **Safety parity:** Stale candles, forged evidence, risk breaches, expired grants, duplicated orders, stop conflicts and cash mismatches block both modes; live mode adds its unavoidable broker/account-specific checks.
4. **Operations parity:** Common event journaling, restart, replay protection, crash recovery, monitoring, alerts, manual pause/kill, exports and owner UI work across both modes with correct account/mode labels.
5. **No latent build-out:** No ordinary trading features, ML/LLM capabilities, strategy engine pieces, high/low logic, screens, risk features, order-status semantics or backend reconciler are reserved for a post-paper development milestone. Paper feature-complete means the product is built, not merely its simulator.
6. **No unsafe inference:** Paper success alone cannot validate actual broker behavior, including partial fills, real fees, network timeouts, exchange rejections, corporate actions, API permissions, legal obligations, or execution slippage. These require separate live-adapter acceptance evidence and a bounded deployment authorization before flipping the switch.

The deployment process may include an initial very-small-capital supervised proving period, but this is a **validation/activation stage of the SAME completed product**, not the delivery of a second live trading application or a new feature-development project.

## A07. Governing principles

1. **Python is the source of truth for trading decisions becoming actions.** AI/LLM/ML may propose; deterministic Python independently verifies and authorizes.
2. **The broker is the ultimate source of truth for real execution.** Local broker state is a reconciled projection, never a substitute for broker-confirmed orders/positions/funds.
3. **Evidence before action.** No trading from unverified data, invalid calendar, stale quote, fabricated quality report or unverifiable proposal.
4. **Immutability and replayability.** All decisions and portfolio transitions must be attributable to precise, immutable input/model/code/policy versions.
5. **Fail closed, not optimistic.** Errors, unknown states and inconsistent accounting cannot silently turn into approvals.
6. **No guaranteed profit.** Reject unrealistic performance claims, curve-fitting and uncalibrated percentages.
7. **Owner-defined autonomy.** The owner authorizes a bounded live trading policy once; Aegis autonomously trades within it. Unapproved risk expansion or broker authority changes remain forbidden.
8. **Single responsibility.** No duplicate financial writer, hidden shadow ledger, direct LLM broker access or second execution engine.
9. **Independent review of releases, not per-trade approval.** A developer's self-report or test pass alone does not equal audited deployment acceptance. After acceptance and activation, normal qualified orders are automatic.
10. **Separate specification from evidence.** This document defines requirements; repository inspection produces actual status.

---

# PART B — SOFTWARE ARCHITECTURE, DATA AND TIME

## B01. Logical system layers

The implementation SHALL maintain explicit, testable boundaries among:

1. **Connectors:** permitted market-data/broker read APIs, historical imports, streaming transports, mock/synthetic adapters.
2. **Registry and calendar:** instruments, effective sessions, special sessions, holidays, approved calendar configurations and exchange/source mapping.
3. **Data quality and provenance:** imports, source-use eligibility, clocks, gaps, ordering, fingerprints, fidelity and freshness.
4. **Canonical market state:** bounded immutable candles, quotes, bars, sessions and normalized event feeds.
5. **Analytics/feature computation:** indicator functions, market structure, volume, volatility, regime and pattern evidence.
6. **Strategies:** deterministic policies, statistical baselines, experimental models, AI/LLM proposers.
7. **Proposal contract:** versioned, immutable, validated BUY/SELL/HOLD/NO_DECISION research decisions with evidence and expiry.
8. **Portfolio/risk qualification:** independent cash, sleeve, position, order, drawdown and data-eligibility checks.
9. **Execution gateways:** replay backtesting, authenticated paper writer, separately isolated optional broker-order adapter.
10. **Accounting and evidence:** order/fill journal, cash/positions, equity, outcomes, reconciliation, immutable manifests.
11. **Orchestration and recovery:** schedule, grants, event binding, crash recovery, stop state, alerts.
12. **Product/API/UI:** human dashboards, configuration controls, searchable explanations and reports, operator workflows.

Python modules may be reorganized, but the roles and trust boundaries must remain discoverable and auditable. No UI callback, agent tool, external plugin or provider SDK may directly bypass a lower-trust boundary.

## B02. Engineering quality requirements

- Strong Python typing; explicit dataclasses/value objects or validated immutable DTOs at trust boundaries.
- Distinguish `datetime` date/time/timezone; reject naive or ambiguous timestamps where timezone is required; persist canonical UTC and render market sessions in `Asia/Kolkata`.
- Use `Decimal`/integer paise for money and deterministic tick rounding; do not use unvalidated binary floating-point for accounting.
- Validate finite prices, nonnegative costs, strict quantities, fixed identities, enums and impossible state transitions.
- Canonical JSON schemas/fingerprints for cross-process evidence where needed; preserve signed/hashed evidence meaning under serialization.
- Explicit dependencies and environment settings; do not read mutable environment or filesystem sources mid-transaction without validating authorized fingerprints.
- Prefer bounded queries, read-only transaction snapshots and resource caps for LLM and research tools.
- Preserve forward/backward-compatible contracts through versioned migrations, schema tests and documented deprecations.
- Never derive authorization solely from caller-constructible objects, mutable mapping contents, UI-hidden buttons or human-readable labels.
- Separate domain exceptions/reasons from unexpected errors; unexpected failures must not turn into successful-looking results.

## B03. Broker-independent provider architecture

Support interchangeable **data adapters** and, separately, **order adapters**, with explicit capability discovery and configuration rather than hardcoded broker semantics.

Required market-data adapter operations (where available and permitted):
- Authenticate/re-authenticate through a secure operator process.
- Enumerate/map approved instruments and metadata.
- Retrieve permitted historical OHLCV and timestamped quotes.
- Subscribe/unsubscribe to live streams, handle reconnects, sequence discontinuities and heartbeat failures.
- Report provider timestamps, exchange timestamps, receipt timestamps, source identifiers, session coverage and quality flags.
- Report rate limits, data entitlements, freshness and unsupported features truthfully.

Candidate adapters can include **Upstox**, **Zerodha** and future providers after evidence-based assessment; provider choices must be swappable without modifying strategy or accounting logic. A provider's API price, entitlement, order interface, market-data permission and commercial terms MUST be independently reviewed at the time of deployment.

Order-adapter operations belong to the separately authorized live boundary: account/funds/position reads, validated order submission, cancellation/modification when supported, idempotency status lookup, fills/trades reconciliation and rejection handling. Read-only market credentials MUST NOT silently authorize these methods.

## B04. Historical and streaming data contracts

Each data record MUST carry or resolve to:
- Stable instrument, exchange, market segment and quote currency.
- Source/provider and import/batch/stream identity; declared eligibility and permitted uses.
- Exchange event time, receive time, processing/ingestion time and canonical timezone interpretation.
- Interval and precise candle completeness (completed vs still forming); bars must not be promoted early.
- OHLC prices, nonnegative volume where applicable, optional turnover/depth with clear source semantics.
- Versioned normalization rules, corporate-action adjustment policy and source quality verdict.
- Provenance and bounded content fingerprints that tie analysis to the actual observed data.

Historical and streaming paths must normalize into compatible canonical contracts but never assume equal reliability. Research tools MUST be unable to mutate imported canonical evidence. Live backfill MUST never manufacture retroactive trade execution opportunities.

## B05. Exchange calendar and session rules

- Approved, versioned exchange calendar with valid market open/close times and effective sessions.
- Weekends, holidays, special sessions, early closes, half-days and unscheduled closures supported with explicit provenance and confidence.
- Constructor-level and loader-level semantic validation must agree; malformed or impossible session windows must be rejected.
- Store calendar path/identity, exact raw configuration hash, canonical registry hash, member/session hashes and expected session/candle schedules where required.
- Calendar confidence such as provisional/configured is not equivalent to independently confirmed exchange correctness.
- No trading decisions on unsupported or ambiguous exchange sessions. Display the reason prominently.
- Handle date-only and intraday horizons, local market time, daylight-saving issues in external feeds, and UTC persistence correctly.
- Compare clocks and timestamps with strict causal eligibility: a decision at time T uses only information observable at or before T.

## B06. Market-data quality gates

Implement independent, fail-closed checks for:

| Category | Required behavior |
|---|---|
| Provenance | Verify source, declared rights, permitted uses, import authenticity and evidence binding |
| Sequence | Detect missing, duplicate, delayed, reordered or conflicting bars/ticks |
| Coverage | Compare observed completed bars against expected approved session schedule |
| Freshness | Bound decision/evaluation lag using trustworthy clocks |
| Integrity | Reject malformed OHLCV (`low <= open/close <= high`), impossible values and invalid timestamps |
| Adjustment | Record splits/dividends and raw-versus-adjusted series policy without silent changes |
| Cross-source | Flag conflicting quotes and map vendor conventions explicitly |
| Clock | Detect significant exchange/provider/receive-time skew and ambiguous ordering |
| Eligibility | Distinguish data displayability, historical research eligibility and paper-execution eligibility |
| Failure | Halt new entries; emit explicit reason codes; allow only separately qualified risk-reducing operations |

Do not accept a caller-created quality report as authentic proof. Read trustworthy persisted provenance and qualify the evidence inside the correct transaction/snapshot boundary.

## B07. Synthetic market laboratory

- Deterministic seeded stream generator with candles, quotes, corrections, volume and per-instrument identities.
- Scenarios: trends, sideways, gap up/down, volatility spikes, flash discontinuities, missing candles, duplicate streams, stale quotes, reconnects, market close, clock skew and partial multi-instrument failures.
- Fixture scenarios MUST be clearly watermarked **SYNTHETIC** in logs and UI.
- Ability to reproduce a stream from seed/configuration/hash, restart at precise completed-candle cursor and replay without duplicate orders.
- Synthetic fills are engineering simulations, not evidence of strategy profitability in actual Indian market conditions.

## B08. Data persistence and lifecycle

- SQLite is appropriate for bounded local workflows; data architecture should support future controlled migration without losing event/accounting integrity.
- Schema migrations versioned, atomic, forward-only unless explicit rollback plan, run on disposable copies for tests first.
- Separate canonical market evidence, derived features, decisions, execution, accounting, model experiments and audit events.
- Immutability constraints for provenance, grants and financially meaningful history; add-only or append-only audit records where warranted.
- Ownership/foreign keys bind instrument, account, run, runtime, event, proposal, authorization, order, fill and snapshot.
- One-use authorizations with transactional consumption and rollback semantics for financially meaningful operations.
- Prevent manual inserts, reconstructed/copycat evidence and forged grants from bypassing application services.
- Keep exact-schema and migration compatibility policies explicit; never silently treat future schemas as accepted by old readers.
- Support backups, retention periods, archive/export, integrity checking and reproducible disaster restoration.

---

# PART C — CHART INTELLIGENCE, TECHNICAL ANALYSIS AND STRATEGIES

## C01. Canonical technical feature library

All indicator functions MUST define input candle semantics, minimum lookback, calculation conventions, warm-up behavior, invalid/missing-data behavior, output units and causal cutoff. Required candidate indicators include:

- Trend: SMA, EMA, weighted moving average (optional), slope, price position vs MA, multi-MA crossover.
- Momentum: RSI, MACD, rate of change, stochastic oscillator if justified.
- Volatility: true range, ATR, realized volatility, Bollinger Bands and volatility percentile.
- Volume/liquidity: raw and relative volume, volume moving average, turnover, volume spikes, volume confirmation, VWAP and session-anchored VWAP when data allows.
- Market breadth/context when permitted and available: index trend, relative strength vs benchmark, sector context, broader market regime.
- Price structure: candle body, upper/lower wick, range, engulfing/inside/outside bar definitions, gap, opening range, session high/low.
- Risk features: entry-to-stop distance, reward-to-risk, estimated fees/slippage, liquidity-adjusted expected cost, concentration and volatility-adjusted stop candidates.

Indicators MUST be unit-tested against manually derived examples and, where legally and technically suitable, established reference implementations. Display both the value and how the number is computed. An indicator is descriptive evidence, not independently sufficient proof to trade.

## C02. High/low and market-structure analysis

Aegis SHALL independently calculate and explain:

- Previous candle, session/day, week and month high/low and open/close.
- Rolling-window highest high / lowest low with precise inclusive/exclusive window boundaries.
- Swing highs/lows using configurable confirmation rules and **confirmation lag**; a future-confirmed pivot MUST NOT be visible to decisions before the confirming candles exist.
- Higher high, higher low, lower high, lower low and structure-break / change-of-character hypotheses with formal parameter definitions.
- Local range boundaries, consolidation, expansion, compression and retest zones.
- Support/resistance from repeated pivots, volume-at-price if supported, VWAP/anchored references, historical extrema and gap levels.
- Zone clustering and scoring using transparent distance/tick/ATR thresholds, minimum touches, recency and invalidation rules.
- Breakout beyond a defined level, failed breakout, false breakdown, retest and volume/volatility confirmation.
- Liquidity and gap warnings: major discontinuity, illiquid wick, wide spreads, manipulation-like behavior without asserting unlawful conduct.
- Multiple timeframes with explicit higher-timeframe bar completion and no look-ahead leakage.
- Session-aware opening range, previous close comparison, opening gap and intraday high/low progression.

Each chart annotation MUST be traceable to exact bars, formulas, parameters, candle cutoff and a stable feature/evidence reference. Aegis MUST distinguish "historical level", "candidate future resistance/support", "model forecast", and "realized subsequent high/low". Future prices are probability distributions or scenarios, not facts.

## C03. Pattern and setup catalog

Support research-grade, versioned definitions—not untestable labels—for:

1. SMA/EMA trend crossover and trend continuation.
2. Breakout and optional retest of verified resistance/support.
3. Opening-range breakout with session and liquidity constraints.
4. Pullback-to-trend / moving average retracement.
5. Range reversal / mean reversion where empirically justified.
6. Momentum with relative volume and volatility filters.
7. Bull flag / bear flag **research labels** with formal construction rules; live shorting outside the permitted universe remains excluded.
8. RSI oversold/overbought as a hypothesis, not an automatic entry.
9. Trend strength and regime classification; trade only when regime rules match strategy assumptions.
10. Gap-and-go / gap-fill scenarios as separately testable candidates.
11. False breakout / failed breakdown recognition.
12. Multi-timeframe confluence with each horizon's causal timestamp.
13. Stop/target planning based on ATR, structural invalidation and reward-to-risk, with realistic gap behavior.

For every strategy, store: unique ID; immutable version; hypothesis; universe; time horizon; inputs; signal/entry/exit semantics; hold conditions; minimum history; fees; slippage; data requirements; calendar requirements; parameter bounds; failure/exclusion conditions; examples; test cases; reproducibility manifest; evaluation criteria. No pattern recognition should be described as an edge until independently tested.

## C04. Strategy development workbench

The researcher SHALL be able to:
- Define strategy policies via validated configuration rather than editing core execution code for every experiment.
- Choose approved universe, calendar, timeframes, parameter ranges, date windows, benchmark, modeled costs and risk envelope.
- View generated signals on candles and identify exactly why signals occurred or were suppressed.
- Compare multiple strategies on the same frozen data, corporate-action policy, capital, costs, fill model and time cutoffs.
- Run reproducible batch evaluations and parameter sensitivity analysis with controlled compute limits.
- Save immutable versioned manifests, hash all strategy-relevant configurations and retain decision/equity series.
- Re-run any past experiment and receive the same deterministic results with the same data/code/policy version (except explicitly documented stochastic seeds).
- Reject leakage, overfitted candidate selection and post-hoc rewriting of locked evaluation windows.

## C05. Scanning and watchlist intelligence

- Allowlist-driven scans across configured NSE cash equities; never auto-trade newly discovered symbols.
- Sort by transparent research scores (trend strength, volume anomaly, volatility, setup completeness, liquidity, proximity to verified levels).
- Show why each symbol is included/excluded, with timestamp and data-health badges.
- Separate scan ranking from portfolio eligibility and actual trading permission.
- Allow optional scheduled scans outside market hours using permitted snapshots without creating a market-order opportunity.
- Display opportunity counts, null results, candidate expirations and reasons for HOLD.

---

# PART D — AI / LLM TRADING AGENT, SKILLS, LEARNING AND MODEL GOVERNANCE

## D01. Role of the AI trading agent

Aegis's AI layer SHALL become the **primary autonomous market-analysis and trading-decision agent in approved live operation** that understands the system's trading ontology, seeks appropriate evidence through allowlisted tools, evaluates candidate opportunities, recommends BUY/SELL/HOLD or NO_DECISION, justifies uncertainty, and learns from independently evaluated outcomes. It shall **not** be the component that directly commits financial entries, decides whether its own authorization is valid, changes hard risk limits, or bypasses Python execution boundaries.

Aegis SHALL support three comparison tracks:

1. **Deterministic strategy track:** transparent, fixed-rule signals; primary engineering baseline.
2. **Local lightweight ML track:** compact statistical/ML probabilities, rankings or filters derived from leakage-safe training.
3. **LLM/agent track (core product requirement):** tool-assisted evidence synthesis and structured proposals; may operate in shadow mode for validation, but the accepted production architecture MUST support autonomous decision-driven real order execution through Python safeguards.

The architecture SHALL permit running these tracks independently and on the same frozen input so that a claim of AI improvement can be tested against a non-AI baseline. Do not assume that adding an LLM improves profitability. In `AUTOMATED_LIVE`, an operator-approved, validated **LLM/agent, ML, rule-based or ensemble decision policy** MUST be able to drive the approved broker order gateway automatically through Python risk checks; the agent's central role must not be reduced to an optional commentary widget.

## D02. Agent capabilities and boundaries

The agent SHOULD be able to reason about:
- Whether exchange session and data eligibility permit any decision.
- What the chart objectively shows, including trend, regime, swing levels, opening range, volume, gap and volatility.
- Whether a purported breakout/retest is supported by valid observations rather than visual speculation.
- Whether the intended horizon and strategy agree with the observed timeframe.
- What a proposed entry, stop, target, and reward-to-risk mean and whether the plan is internally coherent.
- What would invalidate the hypothesis, what could go wrong, and why HOLD may dominate BUY/SELL.
- Whether current positions and remaining portfolio cash make an opportunity irrelevant or unsafe.
- Whether the expected incremental edge exceeds spread, slippage, charges and uncertainty.
- Whether data quality, trade frequency, fees, liquidity or regime uncertainty makes the signal unsuitable.
- What additional read-only evidence is needed, within configured time/token/data budgets.
- How prior labeled outcomes compare, including counterfactual baselines, without inferring causation from a few trades.

The agent MUST NOT:
- Invent candles, quotes, exchange sessions, economic news, broker holdings, historical win rates or fills.
- Fabricate evidence references, confidence calibration or source permissions.
- Access arbitrary files, network endpoints, shell commands, secrets, credentials, or broker-order APIs through tool calling.
- Call broker order endpoints **directly** or arbitrarily modify positions, grant itself risk authority or promote itself to live mode. The agent **MAY cause authorized real orders indirectly** by issuing structured proposals that Python validates and routes through the separately enabled live gateway.
- Rewrite past decisions, cherry-pick historical ranges, silently change strategy rules or trade from incomplete candles.
- Treat external text such as market news, skill descriptions, retrieved pages or LLM tool output as trusted instructions.
- Override a Python risk rejection by submitting a slightly altered plan without a fresh, valid and authorized research lifecycle.

## D03. Grounded agent tool catalog

Expose only permissioned, typed, read-only or explicitly research-scoped tools with bounded inputs, sanitized outputs and auditable call records. Candidate tool capabilities:

| Tool class | Purpose | Essential guard |
|---|---|---|
| `get_market_snapshot` | Time-aligned quotes, canonical completed bars | Approved instruments, explicit `as_of`, freshness and source fields |
| `get_market_structure` | Pivots, highs/lows, support/resistance and trend regime | Exact evidence IDs and no future confirmation |
| `get_technical_features` | RSI, ATR, MA, VWAP, volume, other computed values | Declared lookback and calculation version |
| `get_strategy_signals` | Baseline strategy output and restrictions | Only qualified, immutable signal inputs |
| `get_portfolio_context` | Hypothetical/paper cash, exposures, open risk | Read-only verified accounting snapshot |
| `get_risk_policy` | Public deterministic guardrail summary | No authority to edit or loosen limits |
| `get_research_history` | Previously run experiments and outcomes | Bounded, frozen, appropriate evaluation partition |
| `get_model_predictions` | Local ML predictions, calibration, uncertainty | Versioned model + feature/data hashes |
| `get_approved_news_context` | Optional licensed/time-stamped contextual facts | Use time-aligned news and injection-resistant extraction |
| `get_explanation_template` | Plain-language educational definitions | No authorization or execution effects |

Every tool response MUST include its origin, time cutoff, eligibility, exact schema and refusal reason when ineligible. Tools MUST not accept caller-created evidence claiming to be authoritative. Inputs/outputs and model instructions MUST be versioned and journaled with sensitive values redacted.

## D04. Agent decision workflow

Define an explicit, bounded workflow:

1. **Trigger:** Only on qualified completed-candle events, approved scan events, manual research requests, or approved offline evaluation; not on every market tick by default.
2. **Eligibility prescreen:** Verify universe, session, source rights, quality/freshness, portfolio snapshot and per-run token/cost/time budgets. Unsafe events become recorded HOLD/BLOCKED/NO_DECISION rather than being sent to an expensive model.
3. **Read-only observation:** Fetch exact canonical evidence and deterministic features; freeze input cutoff and manifest.
4. **Hypothesis generation:** Compare plausible BUY, SELL-as-permitted-exit, HOLD and abstention. Clarify the intended timeframe and preconditions.
5. **Adversarial self-check:** Check contradictory indicators, unsupported resistance claims, insufficient liquidity, invalid stops, likely costs, correlated positions, ambiguous regime and the chance that no trade is warranted.
6. **Structured proposal:** Return validated versioned JSON containing action, rationale, independent evidence IDs, expiry, confidence metadata, risk assumptions and proposed invalidation/exit rules.
7. **Independent qualification and shared dispatch:** Python reconstructs canonical data, verifies every important property, rechecks authoritative mode-bound account/risk state and emits the same validated order-intent contract. A deployment-selected authorized gateway produces simulated broker events in `PAPER` or actual broker submissions/reconciliation in `LIVE`; normal qualified LIVE orders do not wait for user confirmation.
8. **Durable journal:** Preserve proposal and model/version fingerprints, accepted/rejected reason codes, token/cost/latency and exact event context.
9. **Outcome evaluation:** After suitable embargo/label horizon, evaluate paper outcomes and—when live—broker-confirmed net fills/P&L against deterministic controls, not the agent's own narrative.
10. **Adaptive policy selection and learning:** The agent may automatically select among **prevalidated, preauthorized** strategy/model variants and parameter ranges within the active policy. New learned model artifacts, untested code or expanded risk limits require offline evaluation and an explicit deployment change—not approval for every ordinary trade.

All loops and recursive tool use MUST have caps: maximum steps, requests, data span, tokens, time, provider cost and retries; timeout or uncertainty produces NO_DECISION/HOLD as appropriate.

## D05. Structured proposal requirements

A proposal MUST be a parseable, immutable contract rather than free-form chat. Suggested fields (exact schema/version to be reconciled with repository contracts):

- `schema_version`, `proposal_id` (stable deterministic identity), `proposer_id`, `proposer_version`, `proposer_type` (`RULE`, `ML`, `LLM`, `ENSEMBLE`).
- `run_id`, `event_id`, `account_context_id` where permitted; instrument `EXCHANGE:SYMBOL`, segment and decision timeframe.
- `action`: `BUY`, `SELL`, `HOLD` or separately typed `NO_DECISION`; SELL must distinguish an allowed reduction/exit from an unauthorized short entry.
- `decision_time`, `data_cutoff`, `observation_start/end`, `valid_until`, timestamps with timezone.
- Immutable `evidence_references` including qualified candle/quote hashes, indicator version, calendar, source-use and potentially portfolio snapshot identity.
- `rationale` structured into facts, inferences, hypothesis, failure conditions, and invalidation.
- `confidence` numeric bounded only if semantics are explicit; include calibration profile if claiming probability.
- Proposed `entry_condition`, `stop_loss_rule`, `target_rule`, `exit_condition`, reward:risk calculation and price/tick caveats; do not treat these as executable without Python verification.
- Optional `model_experiment_id`, `prompt_hash`, `provider_id`, `provider_model_version`, local model/feature hash, tool manifest fingerprint.
- `decision_reason_codes`, uncertainty tags and `abstain_reason`.
- No credentials, free-form code execution, broker order endpoint, secret-bearing URLs or mutable authority flags.

Validation MUST reject unknown or invalid enum values, non-finite numbers, malformed evidence, missing mandatory timestamps, stale outputs, conflicting instrument/account/run identities, oversized payloads and unsupported schema versions. Canonical serialization and replay equality must be testable.

## D06. AI confidence, uncertainty and explanation policy

- A model's stated `0.80` confidence is **not** an 80% chance of profit unless calibrated on appropriate untouched out-of-sample data with a defined event and horizon.
- Separate probabilities of direction, reaching target before stop, expected after-cost return and expected drawdown; these are different tasks.
- Include data limitations, possible failure reasons, spread/slippage uncertainty and supporting/contradicting evidence.
- Do not ask the LLM to invent a certainty score from prose. Use calibrated empirical models where the number matters.
- UI must show *observed facts*, *derived indicators*, *model interpretations*, *hypotheses*, *simulated outcomes* and *real orders* with unmistakably different labels.
- Explanations MUST be faithful to the recorded evidence, not post-trade stories rewritten after the outcome.

## D07. LLM providers, latency, economics and fallbacks

- Provide swappable, versioned adapters for approved providers; initial operations may be limited to one low-cost model.
- Support deterministic mock LLM for offline/integration tests; no accidental paid calls in CI.
- Record prompt/system-prompt template hash, actual model identity and release where offered, sampling controls, context selection, tool versions, response, validation errors, latency, tokens and estimated cost.
- Budget per decision, day, experiment and month; alert before caps and never exceed paid budget without explicit approval.
- Use LLMs at meaningful bar boundaries or offline; avoid tick-level dependency or latency-critical order supervision.
- On provider outage, timeout, high cost, malformed JSON, hallucination or rate limit: fail to HOLD/NO_DECISION or an **independently authorized deterministic fallback**; do not automatically trade because AI is unavailable.
- Cache research-only interpretations keyed by exact immutable evidence where allowed; do not reuse a stale decision for a new market event.
- Keep LLM text and external market content separate from trusted system instructions to defeat prompt injection.

## D08. Trading Claude Skills and external strategy-logic integration

Aegis SHALL have a repeatable way to **review candidate trading-related Claude Skills** (or similar agent skills/recipes/repositories) and convert suitable knowledge into its own robust, testable Python research logic.

The process SHALL:
1. Obtain the skill content from an explicitly approved source; record author, URL/source, date, version, file hash and license/usage terms.
2. Classify skill content as **explanation**, **deterministic algorithm**, **heuristic research idea**, **data/tool procedure**, or **unsafe action instruction**.
3. Never grant an imported skill authority over account permissions, secrets, position risk, mode changes, database migrations or broker order tools.
4. Extract formal indicator/strategy/stop/target/risk definitions and all hidden assumptions (lookback, timestamps, exchange, fees, price precision, future-looking pivots, label windows).
5. Implement relevant algorithms inside approved Python modules with type checks, synthetic edge cases, parity examples and independent review.
6. Prove causal correctness; reject code that uses future candles, incorrect timezone or ambiguous trigger/entry semantics.
7. Evaluate research performance using the same locked test methodology as Aegis-native strategies.
8. Store provenance: external idea/source, internal implementation version, tests, limitations, experiment IDs and license compliance.
9. Exclude copied proprietary content or third-party code unless licensing allows it; adapt concepts rather than blindly pasting prompt instructions.
10. Permit the imported skill to offer **research explanations**, but never to call real execution APIs.

Candidate research topics: swing structure, support/resistance clustering, candlestick patterns, volume/VWAP, opening ranges, market regime, trade journaling, reward:risk sizing, decision checklists and risk-managed exit logic.

## D09. Lightweight local ML — goals

Aegis SHALL support computationally modest machine learning as an **optional research proposer/filter**, initially workable on a CPU laptop and robust to small datasets. Start with transparent baselines (logistic regression, linear/rule models) and compact tree ensembles; consider LightGBM as a primary candidate when dependencies and license permit. Random forests or other shallow gradient-boosted approaches may be benchmarked. Complex deep learning and reinforcement learning are **not prerequisites** and must earn their complexity through valid evidence.

ML objectives may include:
- Probability of an after-cost positive outcome over a specified horizon.
- Probability of a barrier outcome (target hit before stop) under a precise event labeling scheme.
- Expected after-cost return conditional on known features; distribution/tail uncertainty.
- Classification of market regime/volatility to filter deterministic strategies.
- Opportunity ranking within an approved watchlist; not automated expansion of trading permissions.
- Detecting data drift, unstable features and configurations associated with poor execution quality.

Each label MUST define time cutoff, entry timing, horizon, costs, barriers, censoring, treatment of gaps and whether overlapping samples induce dependence. Models must never learn from information unavailable at decision time.

## D10. ML dataset, features, and leakage controls

- Use only qualified, licensed, permission-compatible historical evidence with stable candle boundaries and corporate-action policy.
- Feature computations, scaling, imputation, feature selection and hyperparameter tuning MUST be fitted only on appropriate training partitions.
- Time-aware chronological train/validation/test splits; consider purged/embargoed validation for overlapping event windows.
- Maintain a truly locked, never-tuned-on holdout and immutable access logs; no post-hoc retuning after inspecting holdout results.
- Correct for class imbalance and base-rate drift; always compare against naive strategies and a no-trade or always-HOLD baseline where appropriate.
- Track survivorship bias, look-ahead bias, timestamp leakage, corporate action leakage, universe selection bias and unrealistic entry assumptions.
- Version every dataset snapshot, source rights, transformation pipeline, feature schema, label definition, random seed, model artifact and environment.
- Reject insufficient data, degenerate labels, nonrepresentative samples, missing prices or unknown source eligibility.
- ML inference must validate its exact feature schema, parameter version, model checksum, data cutoff and numeric finiteness before producing a proposal.

## D11. ML evaluation and promotion

Report at minimum:
- Base rates and sample counts by instrument, market regime and chronological fold.
- Precision/recall or other classification metrics **and** trading outcome metrics; high classifier accuracy alone is not sufficient.
- Probability calibration where probabilities are used; Brier score/log loss, reliability bins and confidence intervals where appropriate.
- After-cost return, maximum drawdown, exposure, risk-adjusted return, turnover, trade counts, win/loss distribution, tail events, realized/slippage assumptions and idle cash effects.
- Compare to same-instrument buy-and-hold, deterministic SMA, simple random/matched baselines and no-trade alternatives under identical data/cost/risk constraints.
- Include stress tests at increased transaction costs/slippage, alternative execution timing, disconnections, poor liquidity and regime shifts.
- Explain important features using well-scoped analysis without pretending correlation is causation.
- Measure performance by genuine out-of-sample or forward paper outcomes, not solely training/backtest metrics.
- Only reproducible model versions accepted for the relevant mode may influence trades. After a bounded **live strategy bundle** is enabled, automated inference and strategy selection within that bundle require **no manual approval per signal or order**. Deploying an unevaluated new model version is a separate change.

## D12. Controlled learning from wins and losses

The system SHALL maintain a **continuous performance-learning and adaptive-decision loop**. It shall be able to understand its own winning and losing trades and improve policy selection **within a previously reviewed strategy/model envelope**; it must not arbitrarily rewrite production code or hard capital limits:

```text
DURABLE DECISION & MARKET EVIDENCE
       ↓
PAPER OR BROKER-CONFIRMED REAL OUTCOME / BENCHMARK / COST & RISK ATTRIBUTION
       ↓
DIAGNOSE: SETUP, REGIME, DATA, COSTS, EXECUTION, MODEL ERROR
       ↓
HYPOTHESIS / NEW STRATEGY OR MODEL CANDIDATE (VERSIONED)
       ↓
TRAIN / TEST ON DEVELOPMENT SET → LOCKED VALIDATION
       ↓
INDEPENDENT EVALUATION + RELEASE REVIEW FOR NEW DEPLOYABLE CANDIDATES
       ↓
SHADOW → PAPER → SUPERVISED LIVE → BOUNDED AUTONOMOUS LIVE
       ↓
AUTO-SELECT PREAPPROVED POLICIES WITHIN LIVE LIMITS; MONITOR DRIFT
```

Required behaviors:
- Journal every BUY, SELL, HOLD, BLOCKED and NO_DECISION, not merely winning orders.
- Attribute outcomes to strategy signal, entry latency, model choice, market regime, fees, slippage, position sizing and risk gates.
- Compare "what if we did nothing?" and "what if we followed the baseline?" at the same information cutoff.
- Maintain an experiment ledger of hypotheses, expected improvements, parameter trials, rejected variants and reasons.
- Prevent unauthorized automated changes to production code, live credentials, hard risk thresholds, strategy bundle membership or deployed model artifacts; **allow autonomous runtime selection of explicitly approved strategies/parameter ranges** and fail-safe model fallback.
- Require human review when **deploying a new unvalidated strategy/model artifact or widening capital, symbol or risk authority**; do **not** request human approval for every action taken by an already approved model. Canary/shadow-only candidates must not produce real orders.
- Detect degradation and drift; downgrade to a safe deterministic track or HOLD when model quality is inadequate.
- Preserve exact previous versions and ability to reproduce decisions before/after model changes.
- Never claim that a winning sample proves a generalizable edge, or that maximizing in-sample profit optimizes future returns.

## D13. Portfolio-aware agent research

- Recognize existing positions, concentration, stock/sector correlation, cash idle, max per-instrument exposure and current portfolio risk.
- Consider multiple candidate instruments jointly; a good standalone setup can still be a bad portfolio addition.
- Suggest priority/ranking and hypothetical cash sleeve allocation with transparent objectives; Python alone determines executable quantities.
- Distinguish desire to exit risk from desire to enter a short position.
- No discretionary reallocation from another sleeve, implicit borrowing, doubling down, martingale or averaging down unless an explicit separately approved strategy/risk policy permits it.
- If the market gives no convincing opportunity, propose HOLD rather than manufacture action to remain active.

## D14. Agent autonomy levels

| Level | Permitted capability |
|---|---|
| A0 | Explain stored market data and historical trades; no proposals |
| A1 | Generate research-only trade hypotheses and confidence/uncertainty |
| A2 | Produce constrained immutable proposals for independently validated paper trades |
| A3 | Run approved offline experiments and prepare candidate revisions, without self-promotion |
| A4 | Produce qualified proposals routed through an accepted supervised live execution gateway |
| **A5 — required end state** | **Continuously operate an explicitly activated autonomous live strategy: decide BUY/SELL/HOLD, route eligible real orders via Python/broker gateways, manage stops/exits and reconcile real account state without per-trade owner prompts** |

The autonomy level is a capability bound, not a maturity percentage. Moving to or expanding A5 requires deliberate owner authorization and accepted safety evidence. Once enabled, **A5 is genuine autonomous real-money trading**, with human oversight by exception (alerts, risk halt, emergency pause, configuration changes), not manual trade-by-trade approval. No autonomy level may edit hard risk limits, invent broker funds, connect arbitrary tools or self-deploy untested code.

## D15. Multi-agent and model consensus (optional)

- Support optional independent agents for market structure, risk critique, news, portfolio context and proposal synthesis, but do not require expensive multi-agent orchestration.
- Agents may disagree and abstain; a consensus vote does not bypass evidence or Python risk checks.
- Ensure model comparisons use identical information, accepted evidence cutoffs and controlled prompts, and count all model/tool costs in net economic evaluation.
- Maintain independent deterministic controls so a model can be removed without preventing safe system operation.
- Model ensembles must be versioned, evaluated against each single model and prohibited from inventing probability calibration.

## D16. Production AI trading agent: mandatory autonomous behaviors

The target production AI trader MUST operate as a real service, not as a chatbot that waits for a prompt. Within its active, explicitly authorized live trading configuration, it MUST:

1. **Observe continuously:** Subscribe to permitted market data; track completed candles, order book/quotes where available, approved news context if configured, positions, portfolio cash, exposure and broker acknowledgments. Use bounded event-driven scheduling; do not send every tick to a costly LLM.
2. **Understand opportunities:** Calculate and interpret verified market-structure highs/lows, trend direction, support/resistance, breakouts, reversals, volume, liquidity, volatility, session context and stock/portfolio correlations.
3. **Decide without a human prompt:** Evaluate BUY, SELL-to-exit/reduce, HOLD and NO_DECISION on eligible events, using its approved LLM + rules + local ML configuration; explain why it chose or rejected each action.
4. **Construct executable proposals:** Produce immutable evidence-bound action, instrument, trigger/price constraints, quantity or sizing intent, stop/target/invalidation, expiry, risk context and model/policy IDs. The agent must not invent its own financial authority.
5. **Trade real money automatically when valid:** Pass proposals to independent Python risk and authorization controls. If all checks pass within the owner-enabled scope, the live gateway submits the actual broker order **without asking the owner to approve that individual BUY/SELL**.
6. **Manage the whole position lifecycle:** Track pending, acknowledged, partial, complete, cancelled, rejected and unknown broker states; maintain stop/target/exit coverage; respond to policy-qualified exit conditions, including exits that remain safety-eligible when new entries are paused.
7. **Know when not to trade:** Hold on weak/conflicting evidence, missing model input, insufficient net edge, source/calendar uncertainty, stale feeds, invalid sessions, risk violations or degraded broker/account reconciliation; avoid forced trade quotas.
8. **Learn from outcomes:** Compare predicted versus actual real fills and forward results, identify regime dependence and model drift, and autonomously choose among preaccepted strategy/model variants within approved boundaries. New model training and shadow testing may run in isolation but never silently deploy a novel model with real-money authority.
9. **Communicate simply:** Surface actual actions, current holdings, live net P&L, why the AI acted, uncertainty, alert conditions and stopped/paused state. Reserve detailed logs and research metrics for drilldowns.
10. **Operate resiliently:** Recover from restarts and network uncertainty by reconciling broker truth before new entries, preserve an append-only decision/order audit trail, and never duplicate orders on retry.

**Human interaction model:** The owner chooses the live account, allowed stocks, strategy/model bundle, exposure and loss limits, operating schedule and emergency controls, then deliberately enables a time-/scope-bounded autonomous run. The owner does **not** approve each normal trade. The owner is involved for initial enablement, new/increased authority, unresolved incidents, capital-policy changes, deployment of untested models, and emergency intervention. This is a safety boundary and not a requirement that the bot wait for a human during ordinary trading.

## D17. Autonomous trading objectives and honest optimization

- Optimize **out-of-sample expected net, risk-adjusted portfolio return under explicit drawdown, tail-loss, turnover, fee and exposure constraints**, not naive maximum gross P&L or highest trade frequency.
- Let the agent search for favorable setups and relative opportunity among the allowed symbols, but require reproducible reason codes, cross-validation, locked holdout evaluation and paper/real-forward monitoring.
- Evaluate whether BUY, exit SELL, partial reduction (if permitted), HOLD/cash, or another already held allocation has the highest verified expected utility. A prediction is not sufficient: model costs, possible adverse fill, liquidity, spread, slippage, taxes and risk.
- Use feedback from both winning and losing outcomes; preserve abstentions and blocked signals so the agent cannot cherry-pick an apparent high win rate.
- Detect regime change; reduce risk or stop new entries when quality degrades. Fixed hard risk limits remain independent and cannot be loosened by an LLM.
- Require a benchmark/control comparison for any claim that AI/ML improves performance; no guarantee of profit, no automatic promise to recover losses and no forced trading after a drawdown.

---

# PART E — DECISION, PORTFOLIO RISK, BACKTESTING AND PAPER EXECUTION

## E01. Canonical decision semantics

- `BUY`: Proposed long entry or increment **only** if instrument, strategy, cash, sizing, liquidity and risk policy allow.
- `SELL`: Explicitly qualify as a long-position reduction/exit; reject naked short positions in the initial long-only scope.
- `HOLD`: A valid, recorded decision to take no new action; zero order quantity and no accidental fill.
- `NO_DECISION`: No authorized/adequate decision was possible (e.g., model failure, invalid data, contradictory required facts); preserve distinction from intentional HOLD.
- `BLOCKED`: Proposal was received but rejected by independent policy; record the exact reason and evidence.
- `HALTED`: Runtime/system has entered a protective state; no new entries, and any risk-reducing exits must obey explicit safe-exit policy.

Aegis SHALL not equate a model recommendation with an execution instruction; all recommendations must be requalified at the actual execution boundary with fresh authoritative state.

## E02. Capital and position-sizing policy

- Start with a configurable hypothetical INR cash account; no borrowing or leverage for the initial universe.
- Model share quantity as strictly positive integer for actual BUY/SELL, zero for HOLD.
- Size from available verified cash, portfolio sleeve allocation, stop distance, modeled fees, configured max loss per position, instrument risk, liquidity and exposure caps.
- Maintain invariant: modeled full BUY cash debit (including modeled charges) must not exceed both assigned sleeve and total available cash, with exact paise/tick rounding.
- No retroactive redistribution of unused sleeve cash unless a different explicitly approved portfolio policy says so.
- Do not place orders with impossible stops, nonpositive distance, negative cash or zero/invalid size.
- Define all calculations for shares, sell proceeds, average basis, partial positions, realized/unrealized P&L and corporate actions.
- Treat uninvested cash as part of portfolio performance; no fabricated borrowing yield or hidden capital deployment.

## E03. Risk policy and protective controls

Mandatory policy categories:
1. Maximum value per order and maximum position quantity.
2. Maximum percentage/cash exposure per instrument, sector and total portfolio.
3. Maximum simultaneous positions and number of entries per instrument/session.
4. Daily loss cap, session drawdown, account max drawdown and absolute protective stop where applicable.
5. Maximum modeled risk per trade, stop-distance and reward-to-risk restrictions.
6. Cash reserves, pending-order exposure and charges-included affordability.
7. Price quality, min tick, liquidity, maximum spread, volume participation and price deviation.
8. Quote/candle freshness, confirmed calendar, qualified source, no-lookahead, valid session.
9. Duplicate event, order identity, grant and proposal replay protection.
10. Kill switch, reconciliation halt, broker disconnect, unexpected position/funds mismatch.
11. Correlated position/concentration controls when adequate metadata exists.
12. Limit on AI/provider error rate, decision frequency, per-trade uncertainty and cost budgets.

Risk policies MUST be immutable and versioned for each run. All checks MUST be independently recomputed under the financial transaction or equivalently safe trusted boundary. A previous pretrade "approved" screen is informational, never a spendable authorization token.

## E04. Trade-plan construction

Every candidate entry SHALL define, as relevant:
- Instrument and direction allowed by the universe.
- Evidence cutoff, completed candle ID and entry timing (e.g., next eligible bar).
- Entry trigger (limit/market/threshold simulated semantics) and expiry.
- Stop-loss source and invalidation logic; require stop below modeled long entry where policy requires.
- Target price or formally defined trailing/exit logic; require coherent long-position economics.
- Modeled charges/slippage and worst-case assumptions.
- Proposed size, maximum cash debit, absolute INR risk and reward-to-risk.
- Market regime assumptions, invalidation conditions, trading-session restrictions and exit on stale data policy.
- Position management semantics: whether a signal is entry-only, exit-only, stop/target, timed exit or HOLD.

If a model cannot support coherent exit/risk terms, reject the plan. Stop/target simulation must acknowledge gaps, fills unavailable between candles and slippage.

## E05. Historical backtesting engine

- A causal event-driven simulator with explicit signal-at-close versus entry-at-next-eligible-bar rules.
- Account for market calendar and last-bar behavior; no fill where no subsequent eligible trade exists.
- Deterministic order/fill sequencing, quantity rules, portfolio cash, long-only semantics and transaction costs.
- Brokerage, fees, exchange/clearing/regulatory charges, taxes, spread/slippage and price/tick rounding modeled with dated assumptions.
- Market/limit/stop fill assumptions explicitly declared; do not claim precise exchange execution from OHLCV-only data.
- Cap volume participation, prohibit fills when volume/liquidity insufficient, and show unfilled/rejected orders.
- Open positions, liquidation/terminal handling, benchmarks and valuation from the same frozen pricing rules.
- Run multiple strategies/instruments under shared capital, portfolio constraints and consistent chronological ordering.
- Preserve immutable replayable output with orders, fills, rejected/unfilled orders, trades, equity curve, open position, benchmark, diagnostics and summary.
- Include stress cases and test causal timing, not merely matching output of a shared simulation function.

## E06. Independent strategy and economic evaluation

Mandatory results:
- Net and gross realized P&L, unrealized P&L, total equity, cash, drawdown and return.
- Time-weighted/other appropriately defined returns, volatility, Sharpe-like metrics with stated assumptions; avoid misleading comparisons with differing exposure.
- Win rate, win/loss ratio, payoff distribution, expectancy, profit factor, median results and tail risk where sample size supports them.
- Order/trade count, fill rate, slippage, turnover, average holding period, exposure, idle cash and concentration.
- Daily strategy-vs-benchmark excess return series and appropriate confidence/uncertainty estimates.
- Same-instrument buy-and-hold, broad-market benchmark when valid/available, no-trade and matched-random alternatives.
- Chronological development/validation/locked-holdout partitions; purged walk-forward where needed.
- Multiple-testing correction/selection-bias awareness when trying many strategies or parameters.
- Block bootstrap or other serial-dependence-aware uncertainty methods where supportable.
- Stress at **1.5× and 2×** realistic estimated costs, delays, adverse fills and missing data.
- Attribution of results to a small number of best trades; warn if performance is concentrated or statistically fragile.
- Frozen experiment manifest including data, source eligibility, calendar, fees, code, policy, model and tool versions.

No "AI strategy is profitable" conclusion may be drawn solely from a beautiful equity curve, classifier accuracy, a handful of wins or synthetic market outcomes.

## E07. Multi-instrument portfolio orchestration

- Approved universe and deterministic event scheduling across instruments with explicit ordering and independent per-instrument candle cursors.
- Qualified fair allocation (e.g., equal fixed cash sleeves) with paise remainder rule, stable request order and no synthetic borrowing.
- Atomic claim/binding of a run's precise event identity; no conflicting proposer or repeated authorization for a single leg.
- Each instrument's paper order must pass full Phase 8-like authoritative risk/accounting checks; portfolio approval cannot skip per-order checks.
- Multiple instrument executions need not be globally atomic, but each committed fill must be atomic and recoverable; partial portfolio cycles must reconcile truthfully.
- One-use run/leg grants bound to the exact account, runtime, instrument, event, action, quantity, policy, data cutoff and risk context.
- Do not enable the same scheduling-only runner to gain financial authority by changing a flag or passing a forged DTO.
- Hold/unfilled/rejected entries must generate coherent terminal events without orphaned claims or phantom cash movement.
- Support replay/restart and reconciliation of partial cycles without submitting duplicates or recreating historical trade intent.

## E08. Paper account and simulation integrity

- Paper accounts must have explicit creation/inception snapshot and immutable identity.
- Simulated orders are entered only via one clearly identified authenticated paper financial writer.
- Before accepting a new BUY, recompute quote, cash, exposure, price deviation, risk policy, session and evidence integrity **inside the relevant transaction**.
- Cash ledger, position update, order/fill, protection state, authorization consumption and post-trade equity proof must commit atomically or all roll back.
- Validate every equity snapshot against the authoritative ledger, original account inception, positions and current qualified market state; avoid applying a "never used" inception assumption to a used account.
- Stop/target triggers and exit orders require explicit evidence, unique identities and idempotent state transitions.
- Reject forged or reconstructed snapshots, mutable DTO contents, duplicate submission keys, expired authorizations and halted accounts.
- Permit a real-data paper account to use actual observed quotes while never giving it live broker-order privileges.

## E09. Paper fill realism (validation stage; not the final execution mechanism)

Simulated execution MUST state its assumptions and include:
- Model fee schedule and effective date; source and uncertainty.
- Tick rounding, lot/quantity validation, spread, adverse slippage and max price deviation.
- Liquidity, volume participation and possible unfilled order outcomes.
- Delayed price updates, gaps, no-trade intervals and market-close effects.
- Distinct observed quote, model execution price, modeled stop/target fill and valuation price.
- A configurable conservative stress mode that degrades optimistic fill assumptions.
- Transparent UI indicators that simulated prices are **not** guaranteed achievable in live markets.

## E10. Protective exits and safety priority

- The system must define risk-reducing exit policy distinctly from new entry policy.
- Stops/targets cannot be silently omitted for strategies whose authorization requires them.
- If market data becomes stale or the broker disconnects, suppress new entries; safely handling existing exposure must be governed by a separately verified exit/reconciliation design.
- No assumption that a stop always fills exactly at its trigger. Gap and exchange restrictions may cause substantial adverse fills.
- Conflicting stop/target events, duplicated control triggers or recovery replays must never result in multiple exits of the same position.
- Every exit carries a trusted reason, realized cash result, link to originating position and complete journal history.

---

# PART F — HUMAN-CENTERED UI, CHARTS, REPORTS AND OPERATIONS WORKFLOWS

## F01. Product design intent

Aegis SHALL provide a **professional but beginner-friendly trading cockpit** that an owner without prior technical-analysis knowledge can use confidently. It should communicate what happened, what is happening, what might happen, and which actions—if any—the system is allowed to take. The UI must never make hypothetical outcomes look like real profits or display AI speculation as fact.

Design goals:
- Clarity and safety before visual complexity; the default home screen prioritizes **period P&L, BUY/SELL/HOLD by period, open positions and health** above all additional analytics.
- A beginner-friendly **Simple mode** with plain-language insights and a separate **Advanced mode** exposing indicators, exact model/evidence hashes, policy constraints and detailed metrics.
- A polished and original visual language rather than copying another product's proprietary interface.
- Responsive desktop-first layout, usable laptop resolutions, accessible tablet/mobile read-only views where feasible.
- Optional dark and light themes, readable typography, consistent chart/metric formatting, non-cluttered panels and coherent visual hierarchy.
- Fast interaction on a local laptop; expensive analysis runs asynchronously with explicit progress, cancellation rules and bounded resources.
- Persistent, unambiguous labeling for `SYNTHETIC`, `LIVE DATA — PAPER`, `RESEARCH`, `LIVE — SUPERVISED`, **`AUTOMATED LIVE`** and all halted/blocked conditions. The **final product's main screen must natively support real-money account values and real broker actions**, not only paper examples.
- Explain "why", not just display green BUY and red SELL buttons.

## F02. Global navigation and UI information architecture

**Non-negotiable UX rule: the default screen MUST remain genuinely simple.** Aegis is primarily a trading overview for its owner, not a wall of research diagnostics. Do not expose every technical subsystem in the top-level menu or stack every chart on Home. Professional quality means clear hierarchy, precise accounting and fast answers—not dozens of widgets. Preserve the depth of the product by moving complex facilities into discoverable drilldowns.

**Maximum five primary navigation destinations:**
1. **Overview** (default home): **real or paper account selected explicitly**, Today/Week/Month/Year/Custom net P&L, what the agent actually bought/sold/held, open positions and orders, a concise status line and urgent warnings.
2. **Charts**: interactive price chart, symbol search, trading-level overlays and optional AI explanation.
3. **Activity**: full historical signals, BUY/SELL/HOLD decisions, orders, fills and trade history, with flexible date filters.
4. **Research**: secondary navigation for Strategy Lab, backtests, AI Agent details, ML Training, experiments and model comparisons; these SHALL NOT overwhelm Home.
5. **Settings & Safety**: secondary navigation for source/calendar health, risk limits, operational recovery, provider configuration, approvals, reporting and help; critical pause/halt remains reachable immediately from any active-trading screen.

The existing deeper capabilities—Watchlist/Scanner, Agent Decision Center, Paper Portfolio, Journal, Data Quality, Risk, Operations, Reports, Model Training, Guides—are **pages or panels within these five areas**, not fifteen separate permanent sidebar entries. Use context menus, tabs and progressive disclosure, with a maximum of one obvious primary action per panel. Avoid making the owner choose between nearly synonymous navigation labels.

Global header shall show only **execution mode/account** (`PAPER` or **`LIVE — REAL MONEY`**, with the data source identified in compact details), **feed/market health**, **last update** and a plainly labeled **Pause New Entries** control when relevant. Display detailed model IDs, token costs, hashes, calendar fingerprints, full safety matrices and provider troubleshooting inside expandable details, not as default header decorations. Never imply market-open status from wall time alone.

Default to a clean, restrained layout with generous space, readable numbers, muted secondary text and visual emphasis on P&L, decision state and actual account risk. Support dark and light themes without animation, ticker tape, flashing candles or unnecessary notifications as defaults.

## F03. Dashboard — simple first-screen requirements

**The Home screen must answer three questions within about 10 seconds: (1) How much did I make or lose? (2) Which stocks are being bought, sold or held? (3) Is Aegis running safely?** The owner should not need to know what an agent, sleeve, provenance hash or dataset fingerprint means.

### F03.1 Above-the-fold information hierarchy (mandatory)

**Line 1 — compact system status:** Mode/account including unambiguous **LIVE REAL MONEY vs PAPER**, data freshness, market/session state, `Running`/`Paused`/`Blocked`/`Halted` and last update; one concise banner only if attention is required. Never confuse paper profits with real money.

**Section A — P&L (the visually dominant area):**
- Show one **large net P&L** amount in INR (positive/negative sign), the **percentage return**, and a small time-series/equity sparkline or compact chart. Show `PAPER` or `LIVE — REAL MONEY` adjacent to money so that screenshots cannot misrepresent hypothetical profit.
- Directly above or beside the number provide obvious one-tap period choices: **Today | Week | Month | Year | Custom**. Default `Today`; retain independent user-selected periods when changing tabs if safe, and clearly display the active date interval.
- `Custom` opens simple **From** and **To** date inputs, inclusive in the exchange-local trading calendar; prevent reversed ranges, unsupported dates and accidentally gigantic queries. Use `Asia/Kolkata` for human dates, with exact UTC cutoffs available in details.
- Add at most three compact supporting metrics beneath the headline: **Total equity**, **Available cash**, **Open positions**. Other P&L decomposition (realized/unrealized, fees, deposits, drawdown, per-strategy results) belongs in a `View details` expander or dedicated Portfolio/Activity view.
- P&L must be **net of explicitly modeled/actual fees** and must use an authoritative, consistent beginning-of-period/effective opening equity and ending equity; adjust for external account cash flows where supported. Show unavailable/unverified rather than inventing a period P&L when required snapshots or valid marks are missing. A simple sum of BUY/SELL order notionals, average trade return, or sum of intraday snapshots is not an acceptable period P&L.
- Today = current/latest applicable trading-session day; Week = current exchange-local trading week to date; Month = calendar month to date; Year = calendar year to date; Custom = inclusive selected local dates. Make the effective beginning/end timestamps visible on hover/details, especially on weekends, holidays or when the market is closed.
- Distinguish **account equity change** from **realized trade P&L**. Show mark-to-market freshness; do not treat stale/incomplete quotes as current profit. Do not mix paper and real account balances.

**Section B — Which stocks are BUY / SELL / HOLD? (second-most prominent):**
- Provide a simple stock decision/activity list grouped or filtered by **BUY | SELL | HOLD**, with **ALL** as default. Include counts for each group; include `BLOCKED`/`NO DECISION` in a small optional filter or clear warning, not silently under HOLD.
- Provide the **same independent period options** here: **Today | Week | Month | Year | Custom**, with the same date semantics as Section A. The owner may inspect today's P&L while comparing the week's signals. `Custom` has inclusive From/To selectors. Clearly label this filter **Activity in period**.
- Each compact row shows just **Stock**, **latest decision in selected interval**, **decision time**, **decision result** (e.g. `Live BUY filled`, `Live SELL filled`, `Paper order filled`, `Proposed only`, `Blocked`, `No order`) and optionally **position quantity** when owned. One plain-English `Why?` link expands the agent's rationale, independent Python checks and evidence.
- Show useful totals such as **BUY decisions**, **SELL decisions**, **HOLD decisions** and optionally **completed trades** within the chosen period. Count proposals separately from actual filled orders; avoid conflating an AI BUY recommendation with a purchased share.
- Add a small distinct **Current positions / open orders now** subpanel: held stocks, quantity, average entry, mark price, unrealized P&L and protective stop/target status as applicable. **Current** means latest verified account state; the historical period filter MUST NOT imply those stocks were held for every day in the chosen period. If a historical 'as-of' view is offered, label its timestamp, do not silently rewrite 'current' state, and require reconciled historical snapshots.
- For real trading show **open broker orders and pending/partially filled states**, not only AI signals. Never display a submitted live order as filled until broker-confirmed. When SELL is an exit suggestion, show whether the stock is actually owned; for long-only trading, a SELL must not suggest unauthorized naked shorting. HOLD means a deliberate zero-order decision, **not** an open position or a pending order. Show statuses `BUY proposal`, `BUY fill`, `SELL proposal`, `SELL fill`, `HOLD`, `BLOCKED` and `NO DECISION` honestly.
- Avoid overwhelming duplicate rows when an agent evaluates a stock frequently; default to **latest decision per symbol for the selected period**, with `Show all decisions` for the complete chronological event history. A filtered period with no decisions should say `No BUY/SELL/HOLD decisions recorded for this period`, not display stale signals as fresh.

**Section C — compact safety line and optional details:**
- Display concise `Data: Fresh/Delayed/Unavailable`, `Risk: OK/Warning/Halted` and `Account: Reconciled/Unknown` when those statuses are supported and verified. Show only material alerts by default, each with a `Details` affordance.
- Optional single mini-chart or a short recent activity line is acceptable **below** A and B if screen space permits. By default, do **not** show full candlestick chart, large watchlist, AI chat, ML metrics, log console, strategy comparison tables, complete journal and many graphs at once on Home.

### F03.2 Basic layout sketch (conceptual, not a code claim)

```text
┌─ Aegis ─────────── [LIVE REAL MONEY / PAPER] · Feed Fresh · Running · 15:29 ─┐
│ OVERVIEW  |  Charts  |  Activity  |  Research  |  Settings & Safety             │
│                                                                                │
│ PROFIT & LOSS                                      [Selected account]          │
│ [Today] [Week] [Month] [Year] [Custom: From → To]                               │
│ ₹ +123.45  (+1.23%)       [simple P&L trend]                                    │
│ Total equity: ₹10,123.45  |  Available cash: ₹6,000  |  Open positions: 2       │
│                                                                                │
│ STOCK DECISIONS / ACTIVITY IN PERIOD                                            │
│ [Today] [Week] [Month] [Year] [Custom]     [All] [BUY] [SELL] [HOLD]            │
│ Stock       Decision        Time          Result              [Why?]            │
│ INFY        BUY             14:05         Filled (mode tagged) Details          │
│ RELIANCE    HOLD            14:10         No order           Details           │
│ TCS         SELL            14:20         Blocked            Details           │
│                                                                                │
│ OPEN POSITIONS NOW: 2     [View positions]                                      │
│ Status: Data Fresh · Risk OK · Reconciled                [View alerts]          │
└────────────────────────────────────────────────────────────────────────────────┘
```

Numbers and rows above are **illustrative dummy data, not project results**, and must never be presented as verified market or account evidence.

### F03.3 Default-view acceptance and usability

- On a standard laptop viewport, P&L period controls, principal value and stock decisions must appear without scrolling through research charts, broker diagnostics, or configuration cards.
- The first screen should normally have **no more than three major content groups**: P&L, Stock Activity, Current Positions/Safety; optional details remain collapsed.
- An owner can switch either period selector with one action (Custom takes date selection), filter BUY/SELL/HOLD with one action, explain a decision with one additional action, and open a specific stock chart by selecting its row/symbol.
- Every amount, percentage, date interval and action count must be defined and reproducible, never hardcoded presentation values. Empty, stale, holiday and error states must be informative rather than blank or misleading.
- Default colors are restrained; green/red may support positive/negative P&L or BUY/SELL, but are never the only meaning cue. HOLD and no trade are normal outcomes, not errors.

## F04. Live Chart & Market Structure — detailed behavior

Provide professional interactive charting with:
- OHLC candlesticks, price and volume, pan/zoom, crosshair and candle tooltips.
- Date/session picker, symbol selector limited to approved universe, completed-candle timeframe selector.
- Overlays: SMA/EMA, VWAP, ATR bands, Bollinger Bands, previous session/week/month highs/lows, opening range, support/resistance zones, swing highs/lows and structural trend breaks. **Default to price/volume and at most one user-selected overlay; all other indicators must be optional toggles, not an unreadable default stack.**
- Visual BUY, SELL, HOLD, BLOCKED, stop, target, entry, exit and unfilled markers sourced from actual journal records.
- Draw risk/reward boxes for **hypothetical** trade plans without making them clickable real-order controls.
- Chart annotations must state when a swing high or other delayed signal became *known*, not just where the pivot occurred.
- Provide toggle between **raw observed** and **adjusted historical** series with visible source/adjustment policy.
- Tooltips explain observed historical highs/lows versus forecast/target candidates and the evidence for each zone.
- Clearly distinguish live uncompleted candles from finalized bars; uncompleted candles should not trigger eligible execution.
- Trend/regime summary, volatility, relative volume, support/resistance confidence as an evidence quality score rather than uncalibrated trade-win probability.
- Side panel with the AI's contemporaneous reasoning and independent Python verdict for the selected candle/event.
- Deep link from any chart marker to proposal, model, strategy, ledger and order/fill evidence.
- Optional scenario drawing, replay cursor and side-by-side timeframe comparison without allowing look-ahead in replay mode.

Charts should remain usable even when no trade is proposed. Invalid/stale data should show obvious overlays and suppress misleading fresh-looking levels.

## F05. Watchlist and scanning screen

- User can add/remove instruments within the approved research universe; trading allowlist changes are separate, controlled actions.
- List symbol, price/last update, session % change, trend, volume factor, volatility, nearest support/resistance, latest agent opinion, data health and current position.
- Allow configurable filters (breakout proximity, high relative volume, regime, trend, market structure, volatility range, exclusions).
- Rank candidates using a transparent formula and explain each ranking factor.
- Include "Why excluded?" and "No suitable setups" states rather than forcing a shortlist.
- Provide limited export of time-stamped scan result and reproducible filter manifest.
- Never interpret a high scan score as authority to allocate money or place orders.

## F06. AI Trading Agent / Decision Center

Primary goal: make the agent's reasoning **inspectable and testable**.

For each opportunity or event display:
- Instrument, decision timestamp, data cutoff, source identity/quality, timeframe and relevant market context.
- Inputs the agent actually received, tool calls made, evidence IDs and latency/token/cost usage.
- Model/provider/version or local strategy ID; track whether it was rule, ML, LLM or ensemble.
- Proposal: BUY / SELL / HOLD / NO_DECISION, candidate prices/stop/target and constraints.
- Plain-language "Reasons for" and "Reasons against", plus explicit failure/invalidation triggers.
- Confidence *with precise semantics*, calibration warnings and uncertainty tags.
- Separate independent **Python decision**: ACCEPT PAPER, **ACCEPT LIVE**, BLOCK, HALT, EXPIRED, INELIGIBLE or NO_ACTION; `ACCEPT LIVE` is meaningful only under an active authenticated live scope.
- Policy checks, rejection reasons, cash sufficiency and market-data freshness.
- Later outcome versus baseline and whether a model was right for the **right reason** or merely lucky.
- Ability to compare identical-event outputs from two proposer versions without granting either execution authority.

A user may ask the agent natural-language questions such as "Why was RELIANCE rejected?", "What was the previous session high?", "Why is the stop below the entry?", "Show losing setups last month", or "Compare the ML model against SMA". Responses must come from read-only approved tools, cite exact internal evidence, admit uncertainty, and never issue broker orders from chat.

## F07. Beginner-friendly explanation language

Each technical term needs a short explanation in plain English, with optional Hindi/regional-language support only after quality review (English as default):
- **Support:** historical price area where buying has repeatedly appeared; may fail.
- **Resistance:** historical price area where selling has repeatedly appeared; not a guaranteed ceiling.
- **Swing high/low:** locally defined turning point requiring a specified amount of subsequent confirmation.
- **ATR:** recent average price range used to describe volatility, not a prediction.
- **Stop-loss:** intended loss-limiting exit; actual fill may be worse, particularly during gaps.
- **Risk/reward:** modeled potential loss versus target gain under uncertain execution.
- **Unrealized P&L:** a mark-to-market estimate while the position remains open.
- **Drawdown:** decline from a previous equity high.
- **HOLD:** intentionally no trade; may be the optimal risk choice.
- **BLOCKED:** an independent rule refused a proposal, with a precise reason.

Never say "guaranteed breakout", "sure-shot trade", "AI knows the next high", or "99% success" unless an unusually strong, precisely scoped independently validated claim actually justifies it—and even then no guarantees.

## F08. Portfolio / Live and Paper Trading screen

This is a **detail view**, reached from Overview, not a second crowded home screen. The user should immediately understand current holdings and cash; detailed accounting belongs under expanders. The same Today/Week/Month/Year/Custom P&L period selector and currency definitions as F03 MUST apply consistently. Portfolio overview and event history must not silently calculate incompatible P&L.

Show a reconciled snapshot including:
- Selected **live broker or paper** account name/ID, type, operating mode and authorized starting/inception capital. Never aggregate real and paper funds in a single headline.
- Available cash, committed/reserved cash if supported, positions' market value, total equity, realized/unrealized P&L, modeled total charges and daily change.
- Per-stock quantity, average cost, latest qualified quote, stale/market-close indicator, value, share of portfolio, stop/target and pending exit state.
- Per-sleeve initial allocation, spending, fees, remaining cash, allocation remainder policy and blocked allocation reasons.
- Current and historical drawdown, position concentration, cash balance and equity curves with full precision on drilldown.
- Account and position inception, date/time of last reconciliation, quality of mark prices and evidence of any mismatch.
- Distinction between simulated fill price and latest market price; never manufacture profits from untrusted data.
- No ability for a normal paper dashboard button or AI output to unlock real-order functionality.

## F09. Orders, Trades and Decision Journal

Aegis SHALL expose an immutable timeline with **Today | Week | Month | Year | Custom (From/To)** date filters, **BUY | SELL | HOLD | ALL** filters and search for:
- Observed candle/event, model input/proposal, risk acceptance/block, authorization, paper order, fill, stop/target, equity snapshot and recovery event.
- **HOLD, NO_DECISION, BLOCKED, HALTED** as first-class journal entries, even when no order exists. Separate **proposal action**, **Python approval**, **order status**, **fill status**, and **current owned position** so the simple activity list cannot misrepresent what happened.
- Strategy ID, experiment ID, proposer/version, instrument, order status, entry/exit time, quantity, model prices, realized P&L and total after-cost performance.
- Exact reason codes and causal chain: "quote observed → signal → Python validation → simulated order → fill → position → P&L".
- Negative outcomes, missed opportunities, rejected orders, canceled/unfilled orders, data interruptions and fees.
- Read-only event detail and structured export (CSV/JSON/Excel-compatible) with explicit data sensitivity and provider license restrictions.
- Audit hashes and immutable references for engineers, with beginner-friendly descriptions for normal users.

Users must not be able to edit historical fill prices, P&L or past AI rationales through the UI. Corrections require authorized append-only evidence and reconciliation records.

## F10. Strategy Lab and backtest screen

- Choose eligible historical dataset(s), instrument universe, timeframe, strategy/version, parameters, initial cash, cost/slippage assumptions, benchmark and date windows.
- Visual signal overlay, full order/trade table, cumulative return, equity and drawdown charts, market exposure and cash through time.
- Show buy-and-hold, non-AI deterministic and other fair baselines side by side with like-for-like capital/time exposure policy.
- Separate development, validation and locked holdout results; make leaked/locked test access visible and controlled.
- Sensitivity charts with warnings about parameter mining and multiple-testing bias.
- Explain why a signal did not fill, including no next bar, liquidity, volume cap, risk, market closed or missing evidence.
- Reproducible run manifest, deterministic rerun option, downloadable bounded report.
- Make strategy claims cautious: research hypotheses until independently verified.

## F11. ML Training Center

- Display qualified dataset identity, chosen eligible universe, training labels, feature list and forecast horizon.
- CPU/memory estimate and approved compute budget before starting training.
- Clearly show train/validation/locked-test partition dates and leakage prevention status.
- Train baseline and local candidate models via reproducible runs, with clear cancel/retry semantics.
- Evaluation views for classification/calibration, actual simulated P&L after costs, risk and benchmark comparison.
- Model registry with hash, version, feature schema, hyperparameters, training data fingerprint, evaluation manifest and approval stage.
- Drift monitoring, model degradation alerts, feature integrity failures and shadow/paper comparison.
- Never allow a "Deploy Live" button to self-approve a model; promotion must be a separate authorized workflow.

## F12. Experiment & AI Comparison Center

- Compare rule-based SMA, other deterministic strategies, LightGBM/local models and optional LLM providers against identical market events.
- Track action agreement/disagreement, opportunity coverage, latency, model operating cost, net simulated outcomes, drawdown and calibration.
- Store fixed, versioned research hypotheses and their rejection/approval reasons.
- Flag selection bias, small sample size, regime imbalance and concentration of profit in a few trades.
- Side-by-side explanations for contrasting proposals and the same independent risk verdict.
- Include a "no AI" counterfactual and show whether LLM tokens economically justify themselves.
- Keep model selection and deployment decisions attributable to explicit human/operator approval.

## F13. Data / Quality / Calendar Center

- Provider and stream connectivity, subscription entitlements, token expiry and source-use permission without exposing secrets.
- Imported candle count, date range, missing/duplicate sessions, latest quote/candle, clock skew and coverage.
- Detailed data quality verdict by instrument and usage type: display, historical study, paper-decision eligibility.
- Exchange calendar source, effective version, supported sessions, holiday exceptions and independent confirmation status.
- Instrument registry mapping, symbol changes, corporate-action policy, data retention/usage rights and blocked reasons.
- One-click **read-only diagnostics** and reproducible quality reports; any repair/import must be explicit, controlled and separately logged.
- Clear language that successful ingestion is not the same as research eligibility or trading authority.

## F14. Risk & Safety Center

- Persistent, prominent master **PAUSE NEW ENTRIES** / emergency stop for all applicable operational modes, with unmistakable confirmation and audit trail.
- Current risk policy and limits, positions/exposures, stop/target coverage, daily loss, drawdown and outstanding uncertainties.
- Live view of every safety check: pass, fail, stale, unknown, last observation and blocking source.
- Clear restrictions: AI cannot disable stops, raise caps, amend approved instrument universe or enable live execution.
- Two-step review/approval flows for materially dangerous settings and all live capabilities.
- Explain differences between *pause*, *halt*, *cancel pending*, *risk-reducing exit* and *account shutdown*; they are not synonyms.
- A critical red/amber state must be perceivable without relying only on color; use labels and accessible icons.
- All controls must be real, wired to server-validated actions and protected against duplicate clicks or stale browser state.

## F15. Operations / Recovery Center

- Runtime/worker identity, version, last heartbeat, schedule, queue status, market subscription, latest processed event and durable cursor.
- Health checks for database, clock, source, provider, model, risk/ledger reconciliation, alert delivery and disk/CPU/memory pressure.
- Per-instrument stream status and reason for halts; avoid one symbol's failure corrupting others or silently hiding account-wide risk.
- Crash/restart history, interrupted transactions, pending reconciliations, unused authorizations, protected exit state and recovery verdict.
- Read-only explanation of recovered events, failed retries and final order/fill identity; no "Replay orders" button with ambiguous effects.
- Operational alert history and notification preferences; ensure alarm fatigue control without suppressing critical events.
- Clearly distinguish "monitoring is healthy" from "the strategy is profitable".

## F16. Reports, history and exports

Generate human-readable and machine-readable reports:
- Daily/weekly/monthly/year-to-date/custom-range paper account statements consistent with F03 period selectors, starting/ending equity, external cash flows where applicable, simulated costs and reconciliation.
- Per-strategy and per-model P&L, net return, exposure, drawdown, benchmark comparison, win/loss and decision distribution.
- Full decision trace, rejected/HOLD examples, stop/target outcomes, missed trades and outage/recovery summary.
- Calendar/data quality and provider availability reports.
- Model training/validation/holdout manifests, feature importance caveats and drift report.
- Experiment comparison report with statistical uncertainty and no promotional promises.
- CSV and Excel-compatible exports; optionally PDF only when explicitly requested and with data-rights compliance.
- Protect non-public source data and credentials during report generation and sharing.
- Reports must state paper versus live status, time zone, source rights, fee assumptions and whether results are hypothetical.

## F17. Guided onboarding and education

A first-run wizard SHALL introduce:
1. What Aegis is and is not.
2. The difference between real market data, synthetic data, paper trading and live money.
3. How to choose an approved paper research universe and data source.
4. How to interpret candles, support/resistance and BUY/SELL/HOLD.
5. How loss, modeled fees, stops and drawdowns work.
6. Why AI can be wrong, what confidence means and why the Python risk engine is independent.
7. How to run a safe read-only sample experiment and review its journal.
8. How to stop the runtime, view alerts and obtain an auditable report.
9. Where to find the glossary, documentation and safeguards.

Provide sample synthetic walkthroughs with realistic mistakes (false breakout, stale data, stop gap, model hallucination, unfilled order) as interactive learning aids. No simulated scenario should look like a verified real market result.

## F18. UI state, interaction, accessibility and performance

- All controls must have defined loading, empty, degraded, stale, permission-denied, error, halted and successful states.
- Use human-readable dates in IST while exposing raw UTC in audit drilldowns; show locale and cutoff, not just "today".
- Avoid silent auto-refresh glitches, racing browser callbacks, double-action submissions and out-of-date cached balances.
- Avoid misleading colors: negative P&L is not the same as an unsafe runtime; HOLD is not an error.
- Support readable contrast, keyboard focus, chart alternatives/summaries, accessible labels, tooltips and responsive spacing.
- No arbitrary UI-side manipulation of trusted monetary state or grants. All sensitive changes are server-validated.
- Prefer incremental updates, cached read-only summaries and bounded queries; do not block the UI thread on model training or large backtests.
- New UI implementation may remain in Streamlit or be reworked into another framework only following a cost/maintainability decision. A visual redesign must preserve current safety semantics and not weaken auditability.
- Perform usability tests with beginner tasks: identify mode; switch P&L across Today/Week/Month/Year/Custom; filter BUY/SELL/HOLD over each date range; distinguish an AI BUY proposal from an executed BUY; identify currently held stocks regardless of historical activity period; explain a loss; determine whether a quote is stale; find a stop; export trades; and pause entries.

## F19. Minimal-overview UI functional and accounting test contract

The following are **mandatory acceptance scenarios**, not statements that the UI has them today:

1. **Five P&L ranges.** With deterministic reconciled sample equity and source timestamps, `Today`, `Week`, `Month`, `Year` and `Custom` each display the correct *net* INR and percentage result, interval endpoints, paper/live label and freshness. Switching between periods cannot accidentally reuse stale values from the previous selection. Week/month/year correctly bridge market holidays and weekends using the applicable exchange-local dates.
2. **Custom date validation.** Both P&L and activity selectors independently accept valid inclusive From/To dates, reject end-before-start and unsupported/expensive unbounded queries, preserve selected dates across relevant navigation and state the applied interval.
3. **Per-period stock decisions.** Given three symbols with multiple timestamped BUY/SELL/HOLD proposals, Python rejects, fills and NO_DECISION events, activity filters return the correct latest decision per symbol in the chosen period, category counts and optional full event history. No duplicate rows merely from polling or browser rerender. No fabricated decisions when a period is empty.
4. **Position state isolation.** Regardless of chosen historical decision period, `Open positions now` always shows the latest verified ownership/quantity/account marks and never infers ownership from a BUY proposal, historical BUY decision, or strategy suggestion. Historical positions only appear if an explicitly selected and validated `as of` view is supported.
5. **Financial reconciliation.** Period P&L derives from authoritative account snapshots/ledger under defined fee and deposit policies, not displayed signal counts. Missing opening snapshot, incompatible portfolio/account origin, stale prices, corrupted equity or unresolved ledger mismatch produces a conspicuous `Unavailable / Not reconciled`, without invented zero P&L.
6. **Truthful action labeling.** `BUY proposal`, `BUY filled`, `SELL proposal`, `SELL filled`, `HOLD`, `BLOCKED`, `NO DECISION`, `Open position` and `Pending order` remain distinguishable on Home, Activity and Portfolio without requiring advanced knowledge.
7. **Simplicity and responsiveness.** On a typical laptop, a novice locates P&L, period filters, BUY/SELL/HOLD and current positions within 10 seconds; the default home has no more than three major groups, advanced screens are discoverable within two navigation actions, and no UI callback directly mutates trusted financial state.
8. **Safe states.** Test zero trades, all HOLD, closed market, stale feed, disconnected broker read-only adapter, no permissions, risk HALT, delayed AI and model failure. The UI does not imply gains, trading activity or freshness that did not occur. No order-capable action appears in paper modes.
9. **Reconciliation across screens.** For the same account, time interval, cost policy and snapshot, Overview, Portfolio, Activity reports and exports match exactly for cash/equity/net P&L, with explicitly explained exceptions (e.g. selected realized-only submetric versus total equity change).

---

# PART G — LIVE BROKER READINESS, FINANCIAL SAFETY, AND PRODUCTION

## G01. Deliberate separation of live-data paper and live-money trading

**One trading application, configuration-selected PAPER or LIVE execution** is mandatory. Both modes share the same real-time NSE data ingestion, trading agent, feature/strategy/ML pipeline, order-intent generation, deterministic Python risk controls, protective-position lifecycle, journal, monitoring and understandable UI. The execution gateway is the intentional difference: PAPER simulates broker effects, LIVE submits/reconciles actual broker orders and authoritative account state. This gateway difference must be an explicit typed interface, not a separate rebuilt system, alternative agent or untested code branch.

By the time **feature-complete live-data AI paper trading is ACCEPTED**, the live gateway, broker order lifecycle, credentials separation, mode-switch configuration, real-funds/positions bootstrap, reconciliation and failure handling must be implemented and tested without risking funds. Thus **there is no planned follow-on trading-product feature-development phase**. Promotion from accepted paper to autonomous LIVE is a controlled configuration/authorization/deployment operation after additional broker-specific evidence and regulatory review; it is **not** justified solely by attractive paper P&L or a manual text edit. Once approved, eligible orders execute autonomously, without per-order prompts.

## G02. Live broker adapter contract

The real broker-order adapter is a required component of the **feature-complete paper milestone codebase**, where it is built and verified through mocks, authorized broker sandboxes/read-only checks, and controlled fault injection. Its **actual order-submission capability stays dormant/locked during paper operation** and can only activate after explicit real-account approval. The adapter SHALL:
- Discover actual permitted order types, account/segment entitlements, order quantities, lot/tick restrictions and exchange session constraints.
- Obtain time-stamped authoritative funds, positions, holdings, active orders, order history, trades and margins where relevant.
- Apply strict instrument and product allowlists; exclude unsupported order types and leveraged/derivative products from the initial scope.
- Submit only fully qualified, signed/capability-bound order intents; never accept direct LLM, UI text or unverified DTO commands.
- Handle ACK, OPEN, PARTIAL_FILL, FILLED, REJECTED, CANCEL_PENDING, CANCELLED, EXPIRED, UNKNOWN and broker-specific statuses via a normalized explicit state machine.
- Distinguish request sent from order acknowledged. A timed-out request may have been accepted; reconciliation MUST precede any retry.
- Provide idempotency strategy using broker order IDs and client tags where supported, and safe lookup/reconciliation where not supported.
- Support risk-reducing cancellation/exit patterns only when broker semantics are verified; never assume a guaranteed stop.
- Document order-type availability, GTT/conditional order behavior where applicable, API rate limits, market data subscription conditions and failures.
- Record execution references, exact broker-reported prices/charges and reconciliation checks.

## G03. Real-money accounting and broker reconciliation

- Maintain separate real-account ledger projections linked to broker IDs; never mix paper and real accounts or histories.
- Broker-confirmed funds, orders, trades and positions control the authoritative truth. Local projections must reconcile or halt entry-making.
- Explicitly support partial fills, trade corrections, cancel/replace races, fees, tax/charges and delayed confirmations.
- Reject duplicate economic outcomes from retries, delayed streams and restart replay.
- Resolve unknown submitted orders via broker status/history before another submission; treat unresolved order uncertainty as a protective halt.
- Handle trading holidays, markets closing, auction sessions, circuit limits, price bands, exchange rejections and instrument suspension according to confirmed broker/exchange behavior.
- Define withdrawals/deposits, settlements, stock corporate actions and basis changes before they affect financial summaries.

## G04. Independent production risk service

A separately reviewed deterministic service/policy boundary must:
- Compute account and portfolio risk from fresh broker-confirmed state and trusted market data.
- Impose hard maximum daily loss, maximum drawdown, total exposure, per-order notional/qty, concentration, trade count and risk-per-position.
- Constrain maximum open orders and outstanding exposure including pending orders.
- Stop new entries automatically on stale prices, missing broker connectivity, uncertain orders, insufficient balances, source-data problems, clock skew or reconciliation mismatch.
- Permit only explicitly defined, independently tested safe risk-reducing operations during a halt.
- Have immutable or externally controlled guardrails that no LLM, strategy or UI state alone can widen.
- Support manual emergency stop with independent operator authority and durable acknowledgment.
- Restrict the initial supervised pilot to small, pre-approved notional, symbols and order classes; no leverage, shorting or discretionary scaling.

## G05. Compliance, contracts and legal checks

Before live activity, an authorized human must verify current applicable requirements with the specific broker, exchange, regulator and qualified advisers as needed. Required review topics include:
- Indian SEBI/exchange rules applicable to algorithmic/API-based retail trading, their effective dates, registration/approval process if applicable and permitted deployment architecture.
- Broker API agreement and restrictions on automation, trade execution, market-data redistribution, storing feeds, order rates, authentication, static IP or hosting, and approved application identity.
- Product/segment permissions, account risk disclosures, charges, taxes, recordkeeping and audit logs.
- Data vendor licenses, derived data rights and any limits on using source data to train commercial or local models.
- Data protection, secrets retention and incident reporting requirements.
- Any exchange/broker prohibition on uncontrolled autonomous order generation or model-controlled trades.

No static claim about regulatory approval should be hard-coded in the architecture. Policies must be checked for currency at each relevant deployment and reviewed whenever the broker/regulator rules change. No real-money feature becomes enabled until the review is documented and expressly approved.

## G06. Authentication, secrets and access

- Environment-specific, least-privilege credentials; separate read-only market-data accounts from broker trading credentials wherever supported.
- Never store secrets in source control, prompts, decision journals, UI debug output or error reports.
- Provide encrypted/approved secret storage, short-lived token renewal, operator reauthentication and clear revocation handling.
- Prevent LLM tools, plugins and imported skills from reading secrets, protected files or arbitrary URLs.
- Explicit role permissions for viewing data, configuring research, changing risk policy, operating paper sessions, approving live actions and emergency shutdown.
- Every material **deployment/authority-change** approval captures identity, scope, timestamp, expiry, environment, exact run/policy/model hashes and revocation state. **Normal BUY/SELL decisions inside the active scope require no further manual approval.**
- Do not embed broker live mode in default test profiles, CI environments or local tutorial flows.

## G07. Process resilience and disaster recovery

- A single-source-of-truth, durable runtime cursor and transactional identity for every completed-candle event.
- Safe handling of process termination before/after claim, proposal, authorization, submit, fill, accounting and scheduler finalization.
- Reconcile on startup before any new entry; do not replay historical bound decisions to create new orders.
- Revoke unused grants or authorizations safely after uncertainty; never reuse one-use capabilities.
- Never invent missing fills; resolve via journal/database truth for paper and broker truth for live.
- Maintain snapshots, integrity checks, backups, restore drills and schema-version compatibility checks.
- Support graceful shutdown and cancellation of background workers without losing financial consistency.
- Alert for stale runtime, feed disconnect, database lock/contention, quota exhaustion, ledger mismatch, corrupted hashes, unexpected positions, risk halt and failed health checks.
- Define safe behavior when the laptop sleeps, internet disappears or system clock changes.

## G08. Deployment and host strategy

- Development and early evaluation can run locally on Windows with SQLite and bounded CPU workloads.
- Production-like 24/7 monitoring, if needed, should use an approved controlled host and account for exchange session schedules, maintenance and network outages.
- Keep reproducible environment lockfiles, system packages, configuration schema, service startup/shutdown scripts, migrations and monitoring.
- Distinguish dev, synthetic-test, real-data-paper, staging and live environments by physical/logical isolation and credentials.
- Require explicit deployment/change approval, versioned artifacts and an immediate safe rollback process.
- No automatic cloud/VPS payments, paid feeds or model subscriptions without owner approval and budget checks.
- Maintain disk usage, CPU/memory, log retention, token quota, network reconnect and database growth limits.

## G09. Runtime observability and alerting

Collect, timestamp and expose:
- Provider/data lag, quote freshness, completed-candle lag and stream disconnect counts.
- Event queue length, scheduler retries, duplicate/replay rejection and run-to-run determinism.
- Signal/proposal counts split by BUY/SELL/HOLD/NO_DECISION and risk acceptance/block reasons.
- Model inference time, token/cost usage, errors and fallback rates.
- Paper or real order ACK/fill/cancel/rejection latency, unknown states, unsettled reconciliation.
- Account cash/equity snapshots, drawdown, realized losses, stop protection coverage and risk-cap usage.
- Worker health, restart causes, heartbeat, DB errors, backup success and alert acknowledgment.

Alert levels must distinguish informational, warning, critical/stop and incident. Notifications may be email/chat via explicitly connected approved providers, but failure to send must not remove the underlying protective halt. Protect data and avoid exposing credentials in notifications.

## G10. Security threat model

Threats MUST include:
- Prompt injection via web/news/chart text/third-party skills/tool output.
- Fabricated, stale or swapped data/quality reports, altered source rights and malicious file path/symlink tricks.
- Caller-crafted dataclasses/mutable mappings that pretend to be trusted authorizations.
- Duplicate order submission and TOCTOU between read-only precheck and financial write.
- Replayed or copied grant, event, proposal, scheduler or equity evidence.
- Forged scheduler inserts, account/runtime cross-linking and unapproved instrument enrollment.
- Secrets leaks in LLM prompts, console, CI and report exports.
- Broker API outages, request timeouts, duplicate/partial fills and tampered reconciliation.
- User-interface click replay, accidental mode promotion, invalid settings and race conditions.
- Data poisoning / look-ahead / train-test contamination that inflate research results.

Mitigations: independent trust roots, exact identity binding, cryptographic hashes where appropriate, one-use grants, transaction locks, prepared/parameterized SQL, input validation, least privilege, path verification, immutable logs, offline adversarial tests and code-review gates. Hashing data alone is not authorization if attackers can choose both data and hash.

## G11. Production autonomous live-trading service (required end state)

The production live trader is a **long-running autonomous execution service**. It MUST meet the following contract:

**Activation and control**
- The human owner configures authorized broker/account, capital cap, instruments, strategy/model bundle, position/order limits, drawdown/daily loss caps, approved trade types, market hours, stop/target policy, maximum model spend, and operational duration.
- Activation is an explicit, audit-recorded **session/policy-level approval** after prerequisites are accepted. It does not create a queue of orders waiting for human clicks. A configuration may support secure renewal according to a pre-agreed policy; expiry revokes new-entry authority until renewed.
- `Start Autonomous Trading`, `Pause New Entries`, `Resume When Reconciled`, and `Emergency Stop` are unambiguous operator controls. A pause may leave an independently governed exit/position-protection manager active; emergency stop semantics must be precisely defined and tested.

**Real-money automated event lifecycle**
1. Market-data adapter supplies qualified stream events with source/exchange time and a verified session.
2. Feature engine produces completed-candle market structure, volatility, trend, candidate signals and current portfolio exposure.
3. Approved AI/LLM agent, ML, deterministic rules or evaluated ensemble **chooses automatically** BUY/SELL/HOLD/NO_DECISION and creates an immutable grounded proposal.
4. Python verifies the latest broker cash/positions, data, model/policy allowlist, scope, clock, price and costs; computes exact executable quantity and protective plan.
5. The **same canonical execution gateway interface** dispatches the already validated intent to the configured `LiveBrokerExecutionGateway`, whose mode-isolated writer submits real orders and persists request identity. In `PAPER`, the same upstream path dispatches to `PaperExecutionGateway` and makes no real order. **No per-order human approval is required** in authorized LIVE when all conditions pass.
6. Resolve the broker's actual response, partial fills and unknown/timeout states through idempotent client identity where supported and authoritative broker status; never resubmit blindly.
7. Track real cash, net P&L, positions, protective exits and exposure continuously; auto-manage authorized exits and reconcile with the broker before dependent new activity.
8. Attribute outcome and record the full observation → agent decision → Python verdict → broker order → fill → net result chain; expose failures and latency in the dashboard.
9. Continue for the next eligible event while healthy. Enter fail-closed mode on risk, data, policy, broker or accounting uncertainty; notify owner about exceptions, not every routine BUY/SELL.

**Risk requirements**
- A preauthorized AI strategy can **request** actions but cannot place broker orders directly, expand capital, remove stops, alter hard safety limits, switch to unapproved instruments or grant itself new capabilities.
- Hard daily loss/drawdown/concentration/position sizing limits are enforced by independent deterministic code and, where available, broker-side controls. Exits must have carefully tested treatment during paused-new-entry states.
- No independent AI hallucination, prompt injection, skill output, optimistic P&L estimate, missing ACK or model self-confidence can override the hard protections.

**Autonomous adaptation inside a fixed envelope**
- Agent may automatically switch among preapproved strategies/models based on market regime, adjust parameters only inside pretested allowed ranges, elect not to trade, and reduce exposure within independent limits.
- New code, newly trained models outside the accepted bundle, new broker permissions or higher financial limits require a new release/authorization. This is an occasional operational safeguard, **not** a requirement to approve each normal trade.

**Definition of end-state completion:** The fully featured system is implemented **before PAPER feature-complete ACCEPT** and has production-identical upstream trading logic plus BOTH mode-specific gateways already implemented and tested. Once the LIVE capability is independently authorized and broker/account prerequisites are satisfied, the owner switches the validated deployment configuration from `PAPER` to `LIVE` (with an authenticated live grant/account). The same agent autonomously finds opportunities, manages orders and exits, reconciles actual broker state, shows correct live P&L and halts safely on uncertainty. The only *intended software configuration difference* between the two trading modes is the execution gateway and its associated real/paper account identity/authority—not a missing development backlog. Broker/exchange limitations and applicable law must still be satisfied before activation.

---

# PART H — EVIDENCE, TESTING, EVALUATION AND ACCEPTANCE REQUIREMENTS

## H01. Required test layers

Every capability SHALL have appropriately scoped:

1. **Unit tests** for pure calculations (indicators, levels, allocation, Decimal money, fees, risk, timestamp rules, validation).
2. **Property/invariant tests** for impossible financial states, ordering, identity, cash conservation and stable serialization.
3. **Synthetic integration tests** from data arrival through qualified evidence, agent proposal, risk, paper fill, equity and journal.
4. **Adversarial/security tests** for fabricated provenance, copied dataclasses, malformed DTOs, grants, stale prices and prompt injection.
5. **Database migration/trigger tests** in isolated ephemeral files; unauthorized direct writes must fail.
6. **Race/concurrency tests** for multiple instruments, duplicate event processing, write contention and repeated UI actions.
7. **Crash injection/recovery tests** at each transaction/state-machine boundary.
8. **Backtest causality tests** with independently computed expected outcomes, especially next-bar filling and late-confirmed swing points.
9. **ML evaluation tests** for leakage, time splits, feature/label cutoffs, calibration, reproducibility and freeze/holdout discipline.
10. **LLM tests** with deterministic mocks, schema failures, hallucinated evidence, missing citations, cost timeouts and safe fallbacks.
11. **UI usability/contract tests** for safety labels, empty/stale/error states, chart annotation provenance, exact accounting and safe controls.
12. **Operational drills** for reconnects, token expiry, feed disconnect, clock skew, disk full, provider outage and restore.
13. **Broker staging/contract tests** using authorized sandbox/mock environments; no real orders in normal tests.
14. **Independent code/security audit** with exact sources, line evidence, test outputs and explicit unresolved risks.

Do not conflate "all tests passed" with proof of profitable strategy or production readiness. A phase must meet its specific safety, integration, economic and operational criteria.

## H02. Financial and causal invariants

At minimum, tests and runtime assertions SHALL establish:
- Cash and equity are calculated from one authoritative ledger/valuation policy, no shadow balance.
- For a completed cash BUY, starting available cash decreases by executed notional plus modeled/actual charges; cannot become negative where borrowing is forbidden.
- A long-only account cannot have negative quantity or sell more shares than verified owned/available.
- Total cash allocated across fixed sleeves reconciles with account cash under the defined rounding rule.
- Orders/fills are never created by HOLD or NO_DECISION.
- One eligible event produces no more than the independently authorized number/type of actions.
- Failed transaction validation, incomplete snapshot, trigger failure or quote mismatch rolls back *all* financial side effects.
- Duplicate/replayed proposals or provider messages never create duplicate economic fills.
- Decision at T does not use data whose confirmation/publication time is later than T.
- An entry's stop/target/expiry and price comparisons obey tick/decimal rules and available market evidence.
- A reconciled paper/real account can be reconstructed from authenticated inception plus ordered immutable events.
- Repeated backtest/replay on the same frozen inputs yields identical orders, fills, equity and reports.
- A stale/unknown data state cannot masquerade as a fresh market opportunity.
- A copied object, forged manifest or mutable nested field cannot transfer execution authority.
- LLM/provider outage cannot trigger an unqualified broker action.

## H03. Synthetic end-to-end acceptance scenarios

Required canonical test scenarios (all illustrative prices and symbols are **synthetic**):

**Scenario S-01 — Successful single-symbol long lifecycle**
1. Approved simulated calendar opens and a complete candle closes.
2. Evidence, source-use, quote and calendar qualify.
3. Deterministic strategy or mock agent proposes a versioned BUY with stop and target.
4. Python checks cash, risk, allowed instrument, freshness, quantity, price deviation and authorization.
5. Paper order/fill/ledger/position/equity snapshot commit together.
6. Subsequent completed candles produce an authorized protective exit or other qualified closure.
7. Journal and cash/P&L reconcile after restart.

**Scenario S-02 — True HOLD / no opportunity**
- Valid qualified market observation, action HOLD, zero quantity, no order authorization or fill, journal remains complete; distinguish NO_DECISION caused by missing evidence.

**Scenario S-03 — Fake signal / malicious model output**
- LLM claims a nonexistent resistance breakout, fabricated evidence hash or future high. Independent Python refuses it, creates no fill and records exact rejection.

**Scenario S-04 — Price moves before entry**
- Original proposal valid at candle close, but quote changes, deviates beyond policy, or cash is spent elsewhere. Order transaction rechecks the latest accepted quote/cash and rejects safely without leaked authorizations.

**Scenario S-05 — Multi-stock account integrity**
- Approved portfolio universe and fixed simulated sleeves; entries in some instruments commit while others HOLD/BLOCK; sum of cash, fills, positions and snapshots remains exact; no reallocation or double-spending without policy.

**Scenario S-06 — Crash at every financial boundary**
- Inject termination before grant, after binding, after auth, before fill commit, after fill commit and before scheduler finalization. Startup must reconcile without duplicate trades, missing cash effects or resurrection of stale opportunities.

**Scenario S-07 — Real-data-paper eligibility failure**
- Valid network connection but unconfirmed calendar, unapproved source use, incomplete candle or stale quote: no entry despite attractive signal; UI shows exact blocker and data health.

**Scenario S-08 — False breakout and stop gap**
- Market structure candidate breaks level and reverts; test rule definition and simulated adverse stop fill; explain why loss occurred and whether it violates risk expectations.

**Scenario S-09 — Model "improvement" without real edge**
- Candidate improves training metrics but worsens locked forward performance or only wins with unrealistically low costs. System retains baseline and marks candidate unsuitable for promotion.

**Scenario S-10 — Broker unknown submission (live-only contract test)**
- Submit request times out; adapter queries broker authoritative state before any retry; no duplicate real order is placed; unresolved state triggers protective halt.

**Scenario S-11 — Data poisoning / skill prompt injection**
- External skill or news item instructs the agent to disable risk or leak a key. It remains untrusted content; no code/config/credential/order effects occur.

**Scenario S-12 — Recovery after prolonged downtime**
- On restart, consume valid new completed events according to policy, mark historical gap/backfill appropriately, never generate retroactive market orders, and reconcile protective state.

**Scenario S-13 — UI novice safety walkthrough**
- A new operator can see today’s accurately labeled **live or paper** P&L, switch to week/month/year/custom P&L, filter stocks by BUY/SELL/HOLD for the same independent date ranges, distinguish actual current positions from past signals, identify mode, paper cash, last AI action, rejection reason, model uncertainty, stale feed and emergency pause without understanding raw database schemas.

**Scenario S-14 — Live/paper separation**
- A test config using real data credentials remains unable to reach order endpoints, even if manipulated through UI, forged requests, provider imports or model tool output.

**Scenario S-15 — Authorized autonomous live session (broker staging and controlled pilot only)**
- With a valid **operator-activated live policy**, a qualified AI proposal for an allowlisted instrument passes independent Python checks and is automatically submitted to the authorized live broker adapter **without requesting human approval of that trade**. Verify unique identity, broker ACK/partial fill/final fill handling, real account reconciliation, live P&L attribution and exit coverage.
- After repeated eligible independent events, verify the configured strategy can automatically submit further authorized orders without another human click and never exceed the active account/symbol/capital/risk scope.
- With invalid/revoked/expired authority, a stale quote, missing exchange calendar proof, contradictory broker position or unknown submission response, verify fail-closed behavior and no duplicate order. Confirm owner pause/kill stops new entries while protecting existing positions according to defined exit policy.
- These tests MUST use an approved broker staging/mock setup until an independently authorized supervised real-capital pilot; they are **requirements, not permission** to test against a real account.

## H04. Quantitative acceptance standards

Acceptance of trading intelligence requires standards beyond code tests. Aegis SHALL define and freeze, before judging each candidate:
- Exact strategy hypothesis, target instrument universe, market regime assumptions and eligible periods.
- Data licensing/quality/calendar proof and experiment manifest.
- Number of independent observations, trades, evaluation folds and uncertainty method appropriate for the sample.
- Realistic dated cost/slippage schedule and stress multipliers.
- Required relative economic performance vs suitable baselines after costs, plus absolute drawdown/tail-risk limits.
- Stability across time periods, symbols and regimes, with warnings for low sample size.
- Rules for handling multiple tests, hyperparameter searches and locked holdout access.
- Forward paper tracking horizon: approximately **30–60 trading days** (or longer if evidence remains insufficient), with daily journal and independent reconciliation.
- Explicit go/no-go decision and signed owner/reviewer approval; failure to find a credible edge is an acceptable research result.

No universal profit target is promised. Optimization SHALL seek the best measured **expected net benefit subject to risk limits**, not maximize trade count, use the entire account, minimize the number of HOLD actions, or retrospectively optimize for lucky trades.

## H05. Usability, operational and reliability criteria

Acceptance SHALL include measurable targets that are defined and documented per environment, not invented after seeing results:
- Responsive navigation, charts and portfolio summary on the target local laptop and typical bounded data windows.
- Bounded scan/backtest/model compute with visible status and cancellation semantics.
- Continuous paper operation through normal exchange sessions **and, after live release gates, continuous autonomous real-order operation** without duplicate orders, unexplained position/cash drift or per-trade operator prompts.
- Recovery from process restart and temporary network failure with exact reconciliation.
- Data freshness, quote lag, model inference time, scheduler latency and provider usage within separately configured budgets.
- No secrets in exported reports, CI logs, LLM prompts or UI debugging output.
- Critical alerts visible and recorded even if optional notification delivery fails.
- Beginner ability to complete the key workflows without specialist trading knowledge.

## H06. Independent verification gate

For every material change:
1. Make exact source/contract decision and preserve unrelated local work.
2. Add targeted tests including failures, attacks and regression cases.
3. Run authorized focused tests, static checks, formatting/lint/type checks and diff validation.
4. Verify no protected database, broker/order endpoint, paid API or real credential was touched.
5. Report exact file diffs, test counts, skips, assumptions and unresolved risks.
6. Request read-only independent review for financial/security/authorization-critical changes.
7. Record a bounded verdict: `ACCEPT`, `CONDITIONAL ACCEPT`, `REJECT`, or `INSUFFICIENT_EVIDENCE`, specifying **exact scope**.
8. Do not promote a conditional acceptance into a broader phase, live trading permission or profitability claim.

---

# PART I — DELIVERY DEPENDENCIES AND RELEASE GATES (NOT STATUS)

## I01. Build once; accept feature-complete AI PAPER; then configure LIVE

**Product development is front-loaded.** The next big milestone is the **complete application operating in LIVE-MARKET-DATA AI PAPER mode**, including its dormant but tested real broker execution gateway and production controls. This milestone SHALL NOT be accepted while any required trading agent capability, ML or strategy evaluation, Claude Skills-derived tested technical logic, high/low analytics, portfolio/risk accounting, crash recovery, user-facing UI/report, broker state machine, release configuration, or monitoring remains a planned feature. Deferred optional experiments are permitted only if explicitly excluded from the approved release scope; mandatory end-state capabilities cannot be reclassified as optional simply to meet the gate.

**Sequential implementation policy:** Build and accept one explicitly scoped delivery phase at a time, in dependency order. No implementation work on a later phase while an earlier mandatory phase remains incomplete, except a minimal prerequisite correction formally assigned back to the active phase. Research ideas and release dependencies may be documented in advance, but that is not permission to execute later-phase work. The dependency picture is:

```text
SINGLE PRODUCT: DATA + FEATURES/HIGHS/LOWS + STRATEGIES + ML + LLM AGENT
                        |
           CANONICAL INTENTS + PYTHON RISK + ACCOUNT/EXIT RULES
                        |
          ONE ENGINE + ONE UI + JOURNAL + MONITORING + RECOVERY
                        |
              EXECUTION GATEWAY CONTRACT / MODE CONFIG
                 /                                \
       PAPER GATEWAY                        LIVE BROKER GATEWAY
       realistic fills                      mock/sandbox contract tests
       real-data AI paper                   partial fills, ACK, unknowns
       no order credentials                 real-account truth, compliance
                 \                                /
                  BOTH GATEWAYS BUILT AND TESTED
                               |
       FEATURE-COMPLETE REAL-DATA AUTONOMOUS AI PAPER ACCEPTANCE
                               |
       LIVE BROKER/ACCOUNT READINESS, SECURITY, LEGAL AND RISK CHECKS
                               |
           OWNER-AUTHORIZED PAPER -> LIVE CONFIGURATION SWITCH
                               |
       SAME AI TRADER AUTONOMOUSLY SENDS REAL BROKER ORDERS
       (SAFE STOP, RECONCILIATION, BOUNDED REAL-MONEY EXPOSURE)
```

A small supervised live-capital acceptance period is a **controlled validation exercise** under the same LIVE mode and same software, not a separate implementation program. Real-data paper performance is necessary but insufficient to bypass actual broker/economic checks. No live orders are permitted merely because the config key exists.

## I02. Feature-complete-paper and mode-switch acceptance gates

| Gate ID | Intended boundary | Mandatory release evidence |
|---|---|---|
| `GATE-0-RESEARCH` | Reproducible research foundation | Validated data, deterministic analytics, strict tool scope, reproducibility and tests |
| `GATE-1-SYNTHETIC-ENGINE` | Full control-path contract tests on isolated synthetic data | Trusted event/decision/order-intent/risk, financial accounting, crash recovery and adversarial tests |
| `GATE-2-FEATURE-COMPLETE-AI-PAPER` **(NEXT MAJOR PRODUCT MILESTONE)** | **Entire finished Aegis application operating autonomously on qualified LIVE market data and PAPER financial execution** | Finished and understandable UI; Today/Week/Month/Year/Custom P&L and BUY/SELL/HOLD activity; complete LLM agent, model/skill/indicator/high-low logic, strategy evaluation; real-feed quality; continuous monitoring, exits, stop/target and P&L; full multi-symbol portfolio accounting/recovery; one decision/risk/order-intent pipeline; **PAPER gateway working AND LIVE broker adapter/state machine implemented and safely contract-tested**; passing end-to-end and independent acceptance tests |
| `GATE-3-LIVE-MODE-READINESS` | Permit the same finished product to point at a real authorized broker account | Verify real broker account, allowed instruments/order types/API capabilities, credentials/permissions, authoritative funds/positions, unknown orders/partial fills, legal/regulatory obligations, production security, alerts, incident rehearsals, kill switch, bounded grants; explicit human approval; no blind reuse of paper financial state |
| `GATE-4-PAPER-TO-LIVE-SWITCH` | **Same application, controlled `execution_mode: PAPER` → `LIVE` change** | Approved signed/auditable session/account scope, correct mode/account/gateway binding, no unresolved replay or account inconsistencies, safe starting broker reconciliation and initial small exposure; **no new AI/strategy/UI/ML development expected** |
| `GATE-5-AUTONOMOUS-LIVE-VALIDATION` | Validate real-money behavior under small, monitored limits | Broker ACK/fills/funds/positions reconcile, correct real costs/slippage, protective exits and emergency halts, sustained operational evidence; routine eligible trades require **no per-trade human click** |
| `GATE-6-EXPANDED-SCOPE` | Add capital, markets or unapproved strategies/models | New targeted evidence and explicit scope authorization, never silent AI-initiated expansion |

**Anti-regression:** No reviewer or coding agent may claim `GATE-2-FEATURE-COMPLETE-AI-PAPER` ACCEPT while keeping core live broker adapter, finance reconciliation, risk/stop framework, ML/AI decision path or mandated owner UI as a post-paper implementation milestone. Conversely, neither gate 2 nor profitable paper results grants real broker authority; gates 3–5 are mode activation and real-market verification requirements.

## I03. Decision and document ownership

- **Product owner:** Defines trading goals, allowed capital/instruments/expenses/losses, approves deployments and autonomous policy scope, and can pause or revoke the live trader. **Does not manually sign each routine trade.**
- **Engineering agent:** Implements authorized tasks and produces exact code/test evidence; cannot silently enlarge financial permissions.
- **Research/ML agent:** Proposes and evaluates hypotheses; may not alter live risk or hide unsuccessful runs.
- **Independent reviewer:** Audits source and data boundaries for each critical milestone, cites evidence and returns scoped verdict.
- **Autonomous live runtime service:** After the owner deliberately activates an approved bound, independently runs eligible BUY/SELL/HOLD decisions and real broker order workflows **without requiring manual approval for each order**; never treats plain-language AI output as financial authority.

Policies and specifications may be stored in repository docs, but changes must be traceable, reviewed and versioned. An agent should favor the latest **explicit human-approved** decision over older speculative drafts; when two documents conflict, stop and surface the conflict rather than invent a resolution.

## I04. Strict sequential phase completion and no phase-hopping (mandatory)

The project owner requires **FULL-PHASE DELIVERY**, not scattered implementation followed by premature progression. All coding LLMs (GitHub Copilot, Codex, Claude, Gemini, or others) MUST follow these rules:

1. **One active implementation phase.** Maintain a separately versioned, repository-grounded *Phase Gate Register* with a uniquely identified `ACTIVE_PHASE`. No phase is implicitly active because its name appears in a chat, draft spec, or test filename. Establish the register by inspecting real code, tests, architecture decisions, previous dated acceptance reports, worktree state, and this product requirements document.
2. **Do not assume chronological completion.** Historic Phase 1–10 names, M1/M2/M3/M4/M5 subphases, and product release gates are distinct scopes. An `ACCEPT` for M5-B1 (or any child) is not automatically an `ACCEPT` for all of M5, Phase 5, or the whole application. Reconcile any overlaps and dependencies explicitly; do not invent acceptance records.
3. **Define an entire phase before editing.** The active phase's *complete* scope MUST include all mandatory functional requirements, system integrations, tests, migration implications, UI and documentation effects, negative/adversarial cases, rollback, data/security constraints, and precise acceptance evidence. Create a finite checklist and repository file/function-level traceability. No vague phase titled `foundation` with unspecified future cleanup.
4. **No skipping, parallel development, or opportunistic work.** Complete the active phase before starting the next. An earlier prerequisite failure must be repaired and re-audited in its correct phase, not compensated for by adding unrelated later-phase code. An explicitly necessary prerequisite change to later code must be bounded to the active phase and justified.
5. **Full completion means more than code presence.** `ACCEPT` requires verified end-to-end behavior, integration, direct and adversarial tests, determinism where applicable, safety and accounting invariants, Ruff/format/MyPy checks when applicable, migration and compatibility tests when applicable, updated docs, absence of unowned blocker/TODOs, and a separate evidence-based acceptance review. A passing focused test suite alone does not establish full phase acceptance.
6. **Precise states, no fake percentages.** Use `NOT_STARTED`, `ACTIVE`, `BLOCKED`, `IMPLEMENTED_AWAITING_AUDIT`, `ACCEPT`, `REJECT` (or clearly equivalent terms). Acceptance scope and authority must be recorded, including any limitations. Do not use `75% accepted`, `quarter accepted`, or `almost complete` to open the next phase. `CONDITIONAL_ACCEPT` does not close a parent phase whose mandatory conditions remain unresolved.
7. **When blocked, stay blocked rather than hopping.** If an external calendar authorization, market-data permission, missing broker capability, or prohibited operation prevents full phase closure, document the exact blocker, evidence needed, and permitted preparatory work. Do not mark complete, skip it, or pretend an external prerequisite was validated. The owner may explicitly reprioritize or rebaseline the release phase order; an AI cannot silently do so.
8. **Source-of-truth separation.** This master document remains requirements-only. Store current phase IDs/order, mapping, implementation status, worktree/commit/test evidence, blockers, and review results in separately named repository reports such as `docs/AEGIS_PHASE_GATE_REGISTER.md` and dated acceptance reports. Do not change the master requirements merely to update progress.
9. **Two-step change control.** First run a READ-ONLY phase-baseline examination and present a complete phase map plus the precise first open phase and its closure checklist. The owner/assistant reviews that output. Only then send an explicitly authorized, complete, implementation-ready prompt for that one phase. After implementation, perform an independent READ-ONLY acceptance audit; fix all blocking findings and repeat until the phase is ACCEPT. Only then unlock the next phase.
10. **Live trading remains separately protected.** Sequential completion of product phases or PAPER mode is not a substitute for GATE-3/4 live broker authorization, real-account reconciliation, security/regulatory validation, and explicit owner activation. A coding agent must never treat this phase policy or a configuration field as permission to place real orders.

**Standard phase closure record:** phase identifier and name; mandatory requirement IDs and acceptance scenarios; predecessor ACCEPT evidence; exact changed files and source facts; commands/results/test counts/skips; integration/demo proof; safety, data, security and accounting invariants; unresolved issues (must be empty for mandatory scope); independent reviewer verdict and scope; owner sign-off and next phase unlocked. The phase remains open until every mandatory item is evidenced or the owner explicitly changes the scoped requirement through the documented approval process.

---

# PART J — INSTRUCTIONS FOR ANY LLM TO ASSESS A REAL AEGIS REPOSITORY

## J01. Purpose of this protocol

This document deliberately contains requirements, **not implementation labels**. An LLM must derive what exists, what is partially present, what is missing and what is not verifiable **from direct repository evidence**. The assistant must never assume that a requirement is implemented because it appears here, a README promises it, or an earlier assistant described it.

## J02. Mandatory repository review sequence

**Step 1 — Identify authority and scope**
- Find repository root, branch, commit, date, `git status --short`, `AGENTS.md`, `CLAUDE.md`, `README.md`, project charter, architecture decisions, project state, tasks, acceptance reports and migration docs.
- Read relevant document authority/precedence rules. Preserve all uncommitted user changes. Distinguish user-approved requirements from draft explorations.
- Check instructions for restricted databases, credentials, paid services, protected tests and source-use permissions.

**Step 2 — Inventory source**
- Inspect `src/`, tests, config, database migrations, UI entrypoints, CLI/scripts, dependencies, workflows and deployment assets.
- Candidate search targets include (if present) `src/aegis/market_quality.py`, `market_data.py`, `historical_research.py`, `strategy_lab.py`, `backtesting.py`, `proposals.py`, AI/provider tools, `paper_trading.py`, `paper_equity.py`, `portfolio.py`, `portfolio_pretrade.py`, `portfolio_qualification.py`, `portfolio_execution.py`, `forward_runtime.py`, `forward_paper_trading.py`, `multi_instrument_runtime.py`, `database.py`, and tests/docs. Paths are **hints only**, not claims of existence.
- Identify primary entrypoints, ownership of each financial writer, source/use validation root and how the runtime and UI wire to domain logic.

**Step 3 — Construct a requirement traceability matrix in a SEPARATE report**
- Walk **every numbered section** (A01–J and appendices as relevant); subdivide meaningful bullets into independent claims when needed.
- For each requirement, record actual **evidence location**, test evidence, safety restrictions, owner, integration boundary and quality assessment.
- Use categories: `VERIFIED_COMPLETE`, `PARTIAL`, `CODE_PRESENT_NOT_VERIFIED`, `SPEC_ONLY`, `NOT_FOUND`, `BLOCKED_BY_PREREQUISITE`, `NOT_APPLICABLE_APPROVED`, `UNKNOWN`.
- Acceptance status is a **separate dimension** from code presence: `INDEPENDENT_ACCEPT`, `CONDITIONAL_ACCEPT`, `REJECTED`, `NOT_AUDITED`, `INSUFFICIENT_EVIDENCE`.
- Never assign a numerical 25%/50%/75% completion score without an explicit, reproducible scoring method and carefully defined denominator.

**Step 4 — Trace representative end-to-end vertical slices**
1. Source import → calendar/source-use → canonical candles → data quality.
2. Completed candle → feature → strategy/ML/LLM proposal → Python validation → HOLD/BLOCK or accepted paper order.
3. Paper order → ledger/cash/position → equity snapshot → UI/report.
4. Multiple instruments → shared portfolio cash/sleeves → authorization → recovery/restart.
5. Agent tools/prompts → exact provenance, model controls and token usage; detect missing actual runtime wiring.
6. Read-only broker feed → eligible paper market data versus any real-order permissions.
7. Safety halt and crash recovery → state reconciliation and no duplicate fills.

**Step 5 — Confirm behavioral claims**
- Inspect actual implementation, not just test names or comments. Search for stubs, TODOs, unimplemented methods, fixture-only paths and interface implementations that never get invoked.
- Check UI controls reach verified server actions rather than only modifying local display state.
- Check whether proposal/risk/account types are truly authenticated or caller-constructible imitations.
- Inspect schema migration and DB trigger constraints, code-level checks and transaction atomicity.
- Check actual model training/inference calls and journal integration, not merely provider configuration or model filenames.
- Check whether a real feed is merely observed or qualified for the full paper decision loop.
- Check whether recorded historical results are genuinely out-of-sample rather than synthetic or circular parity tests.

**Step 6 — Test safely, when authorized**
- Prefer read-only source review first; run only approved focused tests against temporary in-memory/disposable fixtures.
- Do not open or mutate a protected/normal database, broker account, real order endpoint or paid LLM API in the course of auditing.
- Run lint/format/type checks, selected tests and `git diff --check` only when safe and permitted.
- Capture exact command, environment, result count, skips/failures, changed files and limitations; a passing test is not a blanket acceptance.
- If testing is forbidden or unsafe, clearly mark the claim `NOT_VERIFIED` and identify what evidence would be needed.

**Step 7 — Produce the report separately**
Do NOT edit this master requirements document merely to update a transient implementation status. Produce a new evidence report such as `docs/AEGIS_REQUIREMENTS_TRACEABILITY_YYYY-MM-DD.md`, including:

```text
1. Audit scope, repository commit/branch/worktree and safety declaration
2. Executive capability summary (not a fabricated percentage)
3. Requirements traceability matrix by A/B/C/... and exact subfeature
4. Implemented evidence with paths/functions/tests/line numbers
5. Partial implementations and missing integration wiring
6. Code present but unverified, or unavailable/blocked evidence
7. Known acceptance verdicts with exact scope and remaining conditions
8. Critical security/accounting/data/strategy correctness findings
9. UI and novice-user usability gaps
10. AI/ML/skills integration gaps and model evaluation limitations
11. Live-data paper readiness blockers
12. Real-money trading blockers (independent from paper)
13. Dependency-ordered implementation backlog with acceptance criteria
14. Required tests/audits, confidence and unresolved questions
15. Explicit statement: NO live trading authorization inferred
```

**Step 8 — Respect change authority**
- A **read-only audit request** means no code/document/config/migration edits, no commit, no push and no actual order submission.
- An **implementation request** means implement exactly approved requirements, add focused tests, preserve dirty worktree and avoid gratuitous redesign.
- Never ask another coding agent to decide the architecture for a bounded implementation task if the technical design has already been specified. Supply direct APIs, file scopes, invariants, tests and acceptance conditions.
- Obtain separate human approval before any expense, paid API, protected DB use, broker credential access or live trading capability.
- When blocked, give a precise reason, safe next action and evidence needed rather than guessing about successful execution.

## J03. Minimum matrix template (for the separate report)

| Requirement ID | Requirement summarized | Source/code evidence | Test evidence | Implementation assessment | Independent acceptance | Risk/gap | Next acceptance action |
|---|---|---|---|---|---|---|---|
| `C02.*` | Swing-high confirmation lag, no lookahead | Path/function/line or `none found` | Exact fixture/test or none | Auditor-derived | Auditor-derived | Concrete | Test or implementation gate |
| `D04.*` | Agent structured proposal and independent Python risk | Path/function/line or none | Integration proof or none | Auditor-derived | Auditor-derived | Concrete | Narrow acceptance test |
| `F03.*` | Simple period P&L and period BUY/SELL/HOLD with verified current positions | UI/accounting/query evidence or none | Date/filter/ledger contract tests or none | Auditor-derived | Auditor-derived | Concrete | Evidence/action |
| `F04.*` | Chart markers link to immutable evidence | UI/API/ledger trace or none | UI test or none | Auditor-derived | Auditor-derived | Concrete | Evidence/action |
| `G02.*` | Broker order state/retry/idempotency | Adapter/state machine or none | Sandbox mock test or none | Auditor-derived | Auditor-derived | Concrete | Production safety gate |

The rows above are **assessment examples**, deliberately not findings or completion claims.

---

# APPENDIX A — EXAMPLE CONTRACTS (ILLUSTRATIVE, NOT REPOSITORY AUTHORITY)

The following samples illustrate desired shapes. An implementation must preserve the project's explicitly approved canonical contracts and migrate them carefully if the required fields differ. **The examples do not authorize real execution.**

## A.1 Market observation sample

```json
{
  "instrument_id": "NSE:EXAMPLE",
  "segment": "EQUITY",
  "timeframe": "1m",
  "exchange_timestamp_utc": "2026-01-05T04:00:00Z",
  "candle_completed": true,
  "open": "100.00",
  "high": "101.25",
  "low": "99.75",
  "close": "101.00",
  "volume": 12345,
  "source_ref": "synthetic:fixture:v1:example",
  "source_permission_ref": "research-only-synthetic",
  "calendar_ref": "synthetic-approved-calendar:v1",
  "quality_status": "QUALIFIED_SYNTHETIC",
  "evidence_ref": "aegis:candle:v1:illustrative-hash"
}
```

The `evidence_ref` above is a placeholder, **not** a valid cryptographic fingerprint or permission token. Real schemas must reject fake hashes, enforce byte-exact binding where applicable and avoid confusion with live data.

## A.2 Structured AI proposal sample

```json
{
  "schema_version": "example-1",
  "proposal_id": "illustrative-uuid",
  "proposer_id": "llm-research-shadow",
  "proposer_version": "example-v1",
  "proposer_type": "LLM",
  "instrument_id": "NSE:EXAMPLE",
  "timeframe": "1m",
  "action": "HOLD",
  "decision_time_utc": "2026-01-05T04:00:01Z",
  "data_cutoff_utc": "2026-01-05T04:00:00Z",
  "valid_until_utc": "2026-01-05T04:01:00Z",
  "evidence_references": ["synthetic:example-candle-evidence"],
  "facts": ["The last completed candle closed inside the observed range."],
  "hypothesis": "A breakout is not yet confirmed.",
  "reasons_against": ["Volume confirmation is missing."],
  "abstain_reason": null,
  "confidence": null,
  "entry_condition": null,
  "stop_loss_rule": null,
  "target_rule": null,
  "paper_execution_authority": false
}
```

Do not infer execution authority from a self-declared JSON field. A proposed BUY would need genuine authenticated evidence, price/stop/target terms and independent grants; a missing confident forecast can still appropriately result in HOLD.

## A.3 Immutable experiment manifest sample

```json
{
  "experiment_id": "illustrative-experiment",
  "strategy_id": "sma-crossover",
  "strategy_version": "example-v1",
  "universe": ["NSE:EXAMPLE"],
  "data_fingerprint": "placeholder-not-real-sha256",
  "source_use_status": "synthetic-research",
  "calendar_fingerprint": "placeholder-not-real-sha256",
  "as_of_utc": "2026-01-30T10:00:00Z",
  "fee_policy_version": "illustrative",
  "fill_model_version": "illustrative",
  "feature_pipeline_version": "example-v1",
  "split_policy": "chronological-with-locked-holdout",
  "seed": 42,
  "mode": "RESEARCH_ONLY",
  "metrics": {"note": "Calculated from frozen replay, not manually inserted"}
}
```

## A.4 Python validation conceptual pseudocode

```python
# Illustrative architecture ONLY; not a drop-in function and not live order code.
# The SAME upstream function runs for both PAPER and LIVE; only the
# authenticated execution gateway is selected from a validated deployment.
def evaluate_proposal(event, immutable_proposal, trusted_services):
    qualified_evidence = trusted_services.load_and_verify_event(event)
    if not qualified_evidence.is_eligible:
        return record_no_action(event, reason="DATA_NOT_ELIGIBLE")

    parsed = trusted_services.strictly_validate_proposal(immutable_proposal)
    verified_context = trusted_services.bind_to_trusted_evidence(parsed, qualified_evidence)
    if not verified_context.is_valid:
        return record_no_action(event, reason="PROPOSAL_INVALID")

    # Independent Python financial verification and mode-bound account truth
    # remain required. The gateway is selected by a trusted, grant-validated
    # deployment, NEVER by the LLM's proposal or model tool arguments.
    return trusted_services.authorized_execution_gateway.submit_if_authorized(verified_context)
```

The pseudo-methods are requirements for the **single unified execution-gateway design**, not assertions that they exist. This intentionally omits broker network calls and any ability for LLM output to select `LIVE`. The gateway is constructed only from the independently authenticated deployment mode and capability. The mandatory production execution contract is defined in **A06, D16, G11 and I02**.

---

# APPENDIX B — KEY DEFINITIONS AND ANTI-MISUNDERSTANDING RULES

- **Alpha / trading edge:** Evidence that a strategy may outperform an appropriately matched benchmark **after costs and risk**, with uncertainty acknowledged; not synonymous with a high win rate.
- **Backtest:** Simulated past execution subject to defined price, data and fill assumptions; cannot prove future profitability.
- **Forward paper trading:** The full production-equivalent AI trading system running on qualified live market data, with `execution_mode: PAPER`, simulated financial execution and no real broker orders. Intended to be feature-complete rather than a separate prototype.
- **Live-money trading:** The SAME fully built autonomous AI trading application with `execution_mode: LIVE`, an authenticated live broker gateway and real account state. Requires explicit activation and independently proven real-broker readiness, not a second AI/strategy/UI codebase or per-trade human approval.
- **Agent:** A bounded software component that selects read-only tools and synthesizes evidence; does not inherit execution power from language competence.
- **ML model:** A trained, versioned statistical function; not automatically an authority to change money or code.
- **Claude Skill:** External reusable guidance/workflow or code that may contain useful trading concepts; not trusted authorization, not necessarily an evaluated trading strategy.
- **Proposer:** Strategy/ML/LLM module producing immutable research actions; different from **executor** and **risk authority**.
- **Acceptance:** A scoped reviewer verdict on defined evidence, not proof of everything or permission to cross release gates.
- **Partial implementation:** Some relevant code exists; actual integration, tests or critical guarantees may be absent; must be proven in a separate audit.
- **Qualified data:** Specific allowed source/identity/window/quality/calendar for a specified use; usable charts do not imply tradability.
- **Signal confidence:** Only a calibrated event probability when defined/tested; otherwise a model opinion.
- **Stop-loss:** A plan/order to limit loss, **not** a guaranteed execution price.
- **Best trade:** Often no trade. Maximizing entries and short-term displayed P&L is not the objective.
- **Risk-adjusted, after-cost objective:** Compare outcomes while explicitly penalizing losses, drawdowns, fees, slippage, tail risk and uncertainty.
- **Self-learning:** Continuous outcome analysis and autonomous selection within prevalidated model/strategy bounds, with offline evaluation and human release authority for novel artifacts; **never** self-editing live risk limits or unreviewed deployment.

---

# APPENDIX C — PROJECT CONSTRAINTS AND SOURCE-OF-TRUTH RULES

1. **This is the normative desired-state product specification**, not a declaration of acceptance or source-code truth.
2. The repository's implementation, approved architecture decisions, actual schema/code/tests and dated independent acceptance audits establish what presently exists.
3. If this document conflicts with an explicitly later approved contract, an agent should surface the conflict and follow the documented approval/precedence process; do not silently overwrite established safety boundaries.
4. Preserve other ongoing work and all uncommitted files. No unsolicited repository edits, commits, migrations, credential use, subscriptions, financial operations or broker orders.
5. Research and synthetic/test safety are non-negotiable. **PAPER and LIVE are modes of the same app**, selected by trusted configuration and isolated financial authority; a real-data paper session may never *implicitly* become a real-money account or grant itself broker permissions.
6. A feature is not complete until its user-facing behavior, integration, invariants, tests and relevant scoped audit criteria are evidenced.
7. Neither the AI nor the coding agent may promise profit, activate/expand live authority, self-approve an unvalidated model, lower hard risk controls or authorize new capital. **An already activated live trading agent is expected to place eligible real trades autonomously inside that authority.**
8. Prefer direct, implementation-ready task instructions with files, interfaces, exact behavior, tests and fail-closed criteria once a bounded task has explicit human approval.
9. Every future report should keep **what is required**, **what code shows**, **what tests demonstrate**, **what independent audits accept** and **what is authorized to run** as separate facts.
10. If the proposed edge does not survive valid out-of-sample and forward-paper evaluation, the correct system behavior is to report that honestly, with no real-money deployment.

---

# FINAL PRODUCT ACCEPTANCE STATEMENT

**The required Aegis end product is ONE continuously operating autonomous AI/LLM-driven trading application supporting `PAPER` and `LIVE` as configuration-selected EXECUTION MODES.** Its market data, technical intelligence, ML/LLM decisions, strategies, Python risk, order-intent and position lifecycle, journal and simple UI are the same in both modes. PAPER simulates fills; LIVE uses a tested broker-order gateway and actual broker state. **The NEXT MAJOR MILESTONE is a FEATURE-COMPLETE autonomous AI paper trader using qualified real market data, with every mandatory trading feature and the live broker gateway already developed and safely tested.** Once paper is accepted, the necessary real-broker, security, regulatory and live-capital readiness checks pass and the owner explicitly activates a bounded authority, **moving from paper to real-money trading is intended to require only a trusted, audited execution-mode/account configuration change—not a separate application-development milestone.** The activated AI agent then autonomously BUYs/SELLs/HOLDs within independent Python safeguards without per-trade manual approval. Paper success alone is not real execution acceptance, and this requirements document does not authorize real broker orders from the current codebase.

**Any LLM reading this document must first treat it as a detailed checklist of requirements, then inspect the real code to establish implementation and acceptance facts separately.**
