# System Architecture

> This document describes the architecture and bounded implementation behaviors; it is
> not a current acceptance ledger. Read [`PROJECT_STATE.md`](./PROJECT_STATE.md) for
> current status, [`ARCHITECTURE_DECISIONS.md`](./ARCHITECTURE_DECISIONS.md) for
> approved decisions, and the relevant `tasks/` specification for milestone contracts.
> `RESEARCH_ONLY` remains the active application mode, and Phase 8 remains the sole
> execution/risk boundary.

## M2 portfolio architecture status


The approved M2 v1 design is specified in
[`tasks/M2_PORTFOLIO_ACCOUNTING.md`](../tasks/M2_PORTFOLIO_ACCOUNTING.md). M2-A is
recorded as accepted according to project-owner-supplied history. M2-B shared
qualification received an independent read-only ACCEPT on 2026-11-16 for its bounded
scope according to owner-reported history; the original audit artifact is absent. The
schema-provenance correction requires focused independent review. M2-C is independently
accepted for qualified historical per-sleeve M1B composition. M2-D accounting is
implemented and technically ACCEPTED; distinct-reviewer independent sign-off remains
pending. These decisions are recorded from project-owner-supplied acceptance history;
technical ACCEPT does not establish independent acceptance of M2-D. M2-E has not
started and is not authorized; overall M2 is not accepted.
See [`PROJECT_STATE.md`](./PROJECT_STATE.md) for the dated status snapshot. M2 itself is
historical research/backtesting only and must not invoke Phase 8. The implementation
status is not inferred from this architecture description.


## AI-1B bounded historical research review

The AI-1B implementation is conditionally accepted as a read-only research layer, not as a
live-trading or autonomous-execution system. It is intentionally bounded: the tool layer
exposes historical discovery, coverage inspection, candle retrieval, and quality analysis
using read-only SQLite connections, explicit cursor validation, and provenance-aware
eligibility checks.

The implementation is acceptable for offline research only when the caller continues to
separate four distinct concepts: (1) completeness and integrity, (2) source-use eligibility,
(3) evidence verification status, and (4) execution/risk validation. A dataset can pass
quality checks and still be ineligible because its imports are unverified or prohibited, and
proof of a valid hash is not itself a legal or operational authorization.

The current design remains intentionally conservative: it validates cursor continuity and
page-level continuity using a bounded change-detection fingerprint rather than a snapshot
model, and it fails closed when the dataset exceeds the verification bound. This is a
research integrity guardrail, not a guarantee that the database has an immutable historical
state across an arbitrary time window.

## High-level target components

This list describes the intended system decomposition, not a claim that every adapter,
workflow, or integration is currently available. See
[`PROJECT_STATE.md`](./PROJECT_STATE.md) for implementation and acceptance status.

1. **Instrument master:** provider-neutral exchange, symbol, contract metadata, and active status stored in SQLite.
2. **Data adapters:** retrieve permitted historical/live market data.
3. **Data validation:** check timestamps, missing bars, duplicates, outliers and stale feeds.
4. **Research workspace:** store references, hypotheses and strategy specifications.
5. **Strategy engine:** deterministic signal generation.
6. **Backtest engine:** chronological event simulation with costs.
7. **Validation engine:** out-of-sample, walk-forward and sensitivity analysis.
8. **Paper broker:** simulated orders, fills, positions and cash ledger.
9. **Risk engine:** hard limits and fail-closed controls for simulation.
10. **Experiment registry:** reproducibility metadata and results.
11. **AI orchestration:** bounded tools for research, coding assistance and report explanations.
12. **Candlestick and price-action research library:** deterministic read-only detection utilities under `src/aegis/candlesticks.py`, isolated from strategy execution and backtesting layers.
13. **Technical-indicator research calculations:** deterministic Decimal-based indicator utilities in `src/aegis/indicators.py`, isolated from strategy decisions and execution.
14. **Dashboard/monitoring:** status, results, errors and cost reporting.

The Instrument Master is implemented in `src/aegis/instruments.py`. New registry operations accept tick sizes and strike prices as `Decimal`, integer, or decimal text and store them as decimal text; floating-point inputs are rejected. The legacy `tick_size REAL` column remains to preserve old records. Migration 3 to 4 copies legacy values to `tick_size_decimal` using SQLite text conversion. This provides a usable text representation of the value stored in SQLite, but cannot recover the exact original decimal supplied before conversion to `REAL`. Do not automatically overwrite or delete legacy values. Verify migrated values against an authoritative source before relying on them in financial calculations.

The Phase 5 candlestick/price-action module is an accepted, read-only research utility. It accepts completed OHLCV bars, validates timezone-aware timestamps, and reports timestamped formations and confirmed structure events. Its shape-based reversal formations do not establish strategy context or predictive value. Swing-cluster zones, pullbacks, and OHLCV supply/demand are explicitly limited approximations. The module is not wired to Strategy Lab, the backtesting engine, brokers, or execution. Phase 5 acceptance does not establish verified exchange-calendar coverage, source-use permissions, trustworthy market-data availability, deployment readiness, or authorization for live trading.

Phase 6 implements the approved indicator subset documented in
[`tasks/PHASE_06_TECHNICAL_INDICATORS.md`](../tasks/PHASE_06_TECHNICAL_INDICATORS.md).
The library consumes completed validated candles and returns timestamp-aligned,
versioned Decimal results. It does not infer missing bars or exchange holidays, aggregate
higher timeframes, integrate with strategies, or authorize trading decisions. Session
VWAP requires explicit caller session context and resets by local session date.

## Historical CSV ingestion

The local historical importer stores new OHLCV rows in the additive historical_candles table introduced by schema version 5. Schema version 6 adds nullable experiment-configuration hash and hash-origin columns. Schema version 7 adds `data_imports.source_use_status`, defaulting existing and new imports to `UNVERIFIED`, and source-use declaration history. Open, high, low, and close values use canonical decimal text; parsing, OHLC comparisons, and reliable tick checks use Decimal. The importer never writes imported prices to SQLite REAL columns. The pre-existing market_data table, including its REAL columns and records, remains untouched for compatibility and preservation.

The CSV header is timestamp,open,high,low,close,volume. Exchange, trading symbol, and interval are import arguments. Prices use plain base-10 text without exponent notation; trailing fractional zeroes are removed for canonical storage without rounding. Volume is a non-negative base-10 integer within SQLite's signed integer range.

Timestamps must be ISO-8601 date-times with an explicit UTC offset and whole-minute precision. They are converted to Asia/Kolkata for alignment, then stored as UTC ISO text in YYYY-MM-DDTHH:MM:SSZ form. Intraday intervals (1m, 5m, 15m, 1h) align from 09:15 Asia/Kolkata. Daily (1d) timestamps must be midnight Asia/Kolkata. Stage 4's separate quality analyzer evaluates these timestamps against local exchange/segment calendars; importing data does not itself prove calendar completeness.

Only active registered instruments are imported. Tick alignment is checked only when an exact registry tick size is present and the legacy REAL tick_size is NULL. Migrated REAL-derived tick sizes are not considered reliable; alignment is skipped with that reason in the import result. Existing migrated values remain unchanged and must be verified against an authoritative source before financial use.

Duplicate policy is deterministic and non-overwriting: the first valid row for a duplicate timestamp within a file wins; an existing candle is retained on later imports. Duplicates are skipped and recorded as quality issues. Identical file bytes for the same instrument and interval are recognized by SHA-256; different files with overlapping candle identities are counted as overlaps, while new rows are accepted.

Each import attempt stores the filename basename, SHA-256 checksum, requested and resolved instrument, interval, start/completion timestamps, status, row counts, duplicate counts, and error summary in data_imports. Rejected rows and duplicate rows are recorded in data_quality_issues with source row numbers. Candle inserts, row issues, and final import counts/status commit in one transaction. Unexpected transaction failures roll back the batch, then record the import as failed in a separate transaction.

Source-use permission is tracked at the import/dataset level because the CSV importer does not create or require a reusable `sources` row; `data_imports.source_id` is optional and is not populated by this flow. Imports default to `UNVERIFIED`, including records migrated from older schemas. An explicit user/administrator declaration records `VERIFIED_PERMITTED`, `UNVERIFIED`, or `PROHIBITED`, along with the actor, rationale, and timestamp in `import_source_use_history`. This records a declaration only; it does not automatically verify licences or determine legal rights. Before declaring actual data permitted, the user must independently verify that the source terms allow the intended research/backtesting use. Every import contributing candles to a backtest must be `VERIFIED_PERMITTED`; unverified and prohibited data fail closed, and no override is provided. Completed runs retain their status snapshot if a declaration later changes.

The dashboard labels this view historical CSV data, shows per-instrument/interval counts and coverage, recent import outcomes, quality issues, and a bounded exact-text candle table. It does not present the data as live.

## Strategy Lab (Phase 3)

`src/aegis/strategy_lab.py` currently provides a versioned SMA-crossover specification and
deterministic, Decimal-based long-only signal generation. Its strategy specification exposes
the minimum candle history needed for consecutive slow-window SMA comparisons. Strategy Lab
listing excludes explicitly typed backtesting experiments while retaining validation of
malformed Strategy Lab records. SMA division uses a local precision
of `max(28, integer_places + fractional_places + digits_in_input_count + 12)` significant
digits and explicit `ROUND_HALF_EVEN`; non-terminating averages therefore have deterministic
finite Decimal representations. Under Python's default Decimal context this pins the existing
half-even behavior, while preventing callers' ambient rounding mode from changing signals.

The runner consumes completed candles only and emits informational entry/exit signals at
candle close; it does not calculate returns, simulate fills, size positions, alter risk
limits, or submit orders. Registered specifications, experiment configuration snapshots,
and run results use `strategies`, `hypotheses`, `experiments`, and `runs`. Schema version 6
adds nullable `experiments.configuration_sha256` and `configuration_hash_origin` columns.
New registrations store a SHA-256 of the canonical complete configuration separately from
the JSON. A run verifies that hash and verifies that the duplicated strategy identity,
instrument, interval, and dates agree before it creates a run record. Hashes detect a
configuration edit when the separately stored hash is unchanged; they do not protect against
a privileged actor changing both values. Existing Phase 3 configurations have no historical
integrity guarantee: their hash remains NULL until the first structurally valid run attempt
backfills it (even if the later market-quality gate blocks that attempt), and the run records
`FIRST_RUN_BACKFILL` rather than claiming prior verification.

Successful run provenance records strategy-spec and experiment-configuration hashes,
strategy, market-quality, and historical-ingestion implementation source hashes, the raw
calendar configuration hash and effective resolved calendar/session hash, dataset and
source-file hashes, execution parameters, quality details, and a Git commit only when Git reports a clean worktree. Dirty
or unavailable revision state is recorded explicitly without inventing a commit. Source
hashes are calculated from files on disk; a running Python process may already have loaded
code before those files change, so a disk hash is not guaranteed to identify the exact
in-memory code that executed. Git revision and source hashes improve traceability but are
not a complete executable-artifact attestation mechanism. Hashes also do not reconstruct
source files, calendar snapshots, or candle data that are later lost or changed; preserve
the corresponding versioned files and database backups for replay.
Strategy versions remain immutable through the application registration API within their
instrument and interval scope.

Execution is fail-closed: a run requires a fully covered CONFIRMED calendar, COMPLETE
quality status, 100% expected candle completeness, FRESH data, and no invalid or duplicate
candles. The checked-in calendar is a CONFIGURED NSE cash candidate for 2025-2026, not
CONFIRMED; runs remain blocked until full calendar coverage is independently verified.
Instrument selection reflects current registry
membership only; historical index membership and survivorship-bias correction are not
implemented. AI hypothesis drafting and generated-code evaluation are deferred; no generated
code is executed.

The Phase 4 engine is a deterministic research simulator, not an order-routing or paper-broker
integration. Signals are generated at completed candle close and execute at the next eligible
session candle open. All-or-none order capacity uses the previous completed candle's volume;
the execution candle's eventual volume is unavailable to the decision. The baseline is
long-only, single-position, fixed-quantity, and leaves final open positions marked to market
with estimated adverse liquidation costs reported separately. Charges and slippage are
configurable assumptions, not official fee schedules. Runs also require sufficient strategy
history, snapshot the exact registered instrument/tick inputs and contributing import
permissions, and label runs lacking legacy provenance as `LEGACY_INCOMPLETE`; no missing
historical instrument or permission information is fabricated.

Public backtest calculations use explicit local Decimal contexts for simulation, benchmark,
and relative-return arithmetic. Market-quality completeness percentages use integer
half-up rounding, so caller Decimal precision and rounding settings do not affect a run or
change the caller's context. Backtest listings include typed `BACKTEST_REGISTERED` records;
the canonical configuration hash, backtest configuration, strategy snapshot types and
specification, linked Strategy Lab experiment, and relational strategy/hypothesis references
are validated before listing. Invalid registrations raise a `BacktestError` identifying the
experiment instead of being silently omitted. Unrelated null-strategy records remain
excluded; pre-status typed backtest rows with a null relational strategy link retain their
legacy listing path and are validated against the referenced strategy snapshot.

### Interrupted run reconciliation

Run creation and completion are separate transactions so the database remains usable during
analysis. An abrupt process termination can leave a `RUNNING` row. The application does not
automatically fail such rows because another process may still own them. Since run rows do
not record process IDs, first identify the run ID and `started_at` in `runs`, confirm that
all Aegis application processes capable of owning it have terminated, and inspect the logs.
Only then update that specific row with a statement constrained by both its ID and
`status = 'RUNNING'`. Record that reconciliation was manual in `error_message`; never
bulk-update RUNNING rows based only on age.

After confirming termination, the targeted reconciliation has this form:

```sql
UPDATE runs
SET status = 'FAILED', completed_at = CURRENT_TIMESTAMP,
    error_message = 'Manually reconciled after confirming process termination'
WHERE id = ? AND status = 'RUNNING';
```

### Manual strategy change review

Before registering changed rules, write a change proposal with rationale and a new semantic
version; independently review its specification and mathematical behavior; run unit and
regression tests; confirm complete, fresh, valid data under a fully covered CONFIRMED
calendar; obtain human approval for research registration; and link the proposal to the prior
strategy version in the review record. Registration itself does not constitute approval.
AI-generated code, autonomous approval, and execution are not part of this workflow.

## Exchange sessions and market-data quality

`src/aegis/market_quality.py` loads local, version-controlled exchange/segment calendar rules from `config/exchange_calendars.json`. The checked-in list contains a CONFIGURED NSE cash candidate for 2025-2026, with evidence and limitations recorded in `docs/EXCHANGE_CALENDAR_NSE_CASH_2025_2026_EVIDENCE.md`. It is not independently reviewed or operationally accepted; 2027 and other segments remain uncovered. The report distinguishes unavailable, configured-but-unverified, partially covered, and fully covered confirmed calendars. Calendar records retain their source note and declared coverage; an administrator is responsible for verification and updates. No holiday is inferred or fabricated.

The analyzer is read-only against SQLite. It uses Stage 3's UTC start timestamps and 09:15 Asia/Kolkata intraday alignment, the exclusive session close, and midnight Asia/Kolkata daily labels. Early closes clip the final expected interval; an in-progress clipped candle is not counted until the close. Session breaks create separate windows. Ambiguous/nonexistent local session wall times and intraday sessions before the Stage 3 09:15 anchor fail analysis rather than being guessed or silently skipped.

Missing/unexpected candle findings are confirmed only on dates covered by a confirmed calendar. Configured calendars produce provisional findings; uncovered dates are not called missing. A completeness percentage is calculated only with full confirmed coverage and a nonzero completed-candle denominator. Overall quality status follows the deterministic rules in `docs/EXCHANGE_CALENDAR_CONFIGURATION.md`. Freshness requires an explicit timezone-aware `as_of`; configurable thresholds count missing completed expected candles so closed hours, weekends, and configured holidays do not age data by wall-clock time. No external service or database migration is required.

## AI roles (logical roles, not necessarily separate models)
- Research librarian: summarises cited educational material.
- Hypothesis researcher: proposes testable hypotheses.
- Strategy author: drafts candidate specifications and code for review.
- Experiment analyst: explains deterministic metrics.
- Risk reviewer: checks experiment design and risk reports.
- Supervisor: schedules approved workflows and reports status.

Initially, one LLM may serve all roles using separate prompts and restricted tools. Do not run many agents concurrently by default. Use a simple Python workflow first; introduce a multi-agent framework only when measured complexity warrants it.

## Trust boundaries
- LLM output is untrusted input.
- Strategy code generated by an LLM must be reviewed and tested in an isolated environment.
- The AI layer has no broker order-placement capability.
- Risk limits are configuration-controlled and cannot be changed by agents.
- The Phase 8 paper execution API is implemented and remains the sole execution/risk
  boundary. Its presence does not enable a paper-trading application mode; the only
  configured mode is `RESEARCH_ONLY`. No broker order path is authorized.
- Secrets and account data must not be sent to an LLM.

## Initial deployment
Local development on the user's computer. SQLite for metadata and experiment results. Parquet/CSV for controlled datasets where licensing permits. Streamlit dashboard locally. No cloud server required initially.

## Future broker integration

Any future Kite Connect integration must initially be limited to approved read-only market-data access. Verify current Zerodha terms, authentication, static-IP rules, exchange/SEBI requirements, rate limits and data-use rights before implementation.

Broker order placement requires a separate implementation review, explicit authorization and independent safety controls. It must remain disabled by default and require explicit human enablement.

## Research protocol and architecture invariants before AI-1C

The project must keep separate the following layers:

1. Development data: exploratory analysis and hypothesis generation.
2. Validation data: bounded model and strategy selection.
3. Locked holdout data: final evaluation only, with access logged and controlled.

A proposal or strategy must never be treated as valid merely because it produces a positive
raw result on exploratory data. Research integrity requires a pre-registered evaluation rule
set, a frozen model/prompt/tool configuration, and a written holdout policy.

Historical contamination controls are required. Where the model may know historical market
outcomes, the project must reduce this risk by masking identifiers where practical, noting the
model's training cutoff, and treating in-window periods as lower-confidence evidence until
post-cutoff or independent validation is available. Model and prompt versions, tool versions,
parameters, and data windows must be fixed before the locked holdout is examined.

Look-ahead, leakage, and survivorship-bias assumptions are prohibited. Feature generation,
benchmark comparison, and strategy testing must be point-in-time and time-ordered. The project
must also treat incomplete or disputed calendar coverage and source-use eligibility as separate
concerns from performance evaluation.

The project must also keep the following separations explicit:

- Data completeness/integrity.
- Source-use eligibility.
- Evidence verification status.
- Execution/risk validation.

This separation is required before the AI-1C workflow can claim a valid research proposal or
comparison against deterministic baselines.

## ML research (Phase 7)

`src/aegis/ml_research.py` provides an in-memory, provenance-gated research harness. It
builds decision-time features from completed candles with Phase 6 indicators, labels a
future directional outcome, purges overlapping targets across chronological train,
validation, and test partitions, and compares deterministic logistic regression with
training-majority and SMA20/50 classification baselines. Sensitivity uses validation
only; expanding walk-forward validation is available. Reports include the dataset and
source fingerprints, declared provenance, feature names, library version, parameters,
partition times, and classifier metrics. No model or result is persisted to SQLite.

This harness does not simulate a trading strategy, model transaction costs, or establish
after-cost performance. Its synthetic unit tests verify code behavior only. The checked-in
calendar has only configured, unconfirmed NSE cash coverage for 2025-2026, so Phase 7
acceptance remains pending until permitted and quality-gated market data and confirmed
calendar coverage support cost-aware, untouched out-of-sample evaluation.
Nothing in Phase 7 changes `RESEARCH_ONLY`, bypasses risk controls, or authorizes
paper/live execution.

## Paper execution foundation (Phase 8)

`src/aegis/paper_trading.py` is an isolated, local simulation API. It has no broker
adapter, network data source, strategy signal subscription, or live-order operation.
Callers must supply a registered NSE cash-equity identity, a permitted completed import,
a source-permission declaration and a `DatasetQualityReport` with full confirmed
calendar coverage, complete and fresh prior data. Quality evidence must end strictly
before execution. The fill uses only an explicitly supplied quote at execution time;
the order's signal, submission and execution times remain separately recorded. Prices apply
the existing adverse spread/slippage and tick-rounding assumptions from Phase 4; fills
are synchronous, all-or-none, and constrained by caller-supplied execution-time
capacity. Eventual candle volume is not used.

Schema version 8 adds paper accounts, orders, fills, cash ledger entries, long-only
position projections, realized-P&L history, marks, lifecycle events, and daily starting
equity. Order submission, fill, cash entry, position projection and events commit in one
transaction. Deterministic idempotency keys prevent repeat fills. Reconciliation
reconstructs cash and positions from immutable fill/ledger records and fails closed on
disagreement. It verifies the order-to-fill-to-ledger relationships and their account,
instrument, quantity, price and side before replay; it does not repair corrupted records.
Projected exposure replaces the prior instrument mark once with the complete post-order
position at the execution quote. Daily-loss gates block additional BUYs. A valid, fresh,
long-only SELL can reduce exposure during risk/data halts, while a reconciliation halt
blocks both sides; fresh matching SELL evidence is still required, but stale existing
portfolio marks do not trap an otherwise validated exit. Selling never clears a halt.
Risk settings are mandatory per account;
no financial limits are silently defaulted. Pending orders, cancellation, partial fills,
leverage, shorting and automated strategy routing are not implemented.

The dashboard shows simulated reports and order/fill history read-only; it cannot create
accounts or submit orders. The application mode remains `RESEARCH_ONLY`. The service
does not supply market data; independently confirmed calendars and Phase 4's
pending acceptance remain prerequisites to any real-data operation. Paper results are
not real balances or evidence of profitability.

## Synthetic Forward Runtime (Phase 10A-2)

`src/aegis/forward_runtime.py` supplies a deterministic synthetic quote provider,
provider-neutral quote contract, per-instrument feed validation and health state, and
streaming 1m/5m/15m candle aggregation. Quotes require confirmed configured session
coverage and are rejected when invalid, duplicated, delayed, out of order, or outside a
session. Decision evidence is pinned to the runtime's exact permitted source import and
completed candle. Candidate selection is capped, and scheduler event identities are
stable per completed candle; restart recovery reconstructs missing scheduler events but
does not create proposals or submit orders.

Forward BUY/SELL proposals require timezone-aware expiry and are accepted only through
`ForwardRuntimeService.route_event`, which reconstructs evidence from the exact pending
runtime event, permitted synthetic import, and completed candle. The canonical proposal
digest is persisted on the event and carried with the execution binding to Phase 8.
Generic Phase 8 submission cannot consume a forward-runtime import or supply a runtime
binding. The deterministic mock proposal intake therefore routes through the existing
Phase 8 paper execution and risk boundary using an independently persisted latest-quote snapshot.
Quote-triggered stop/target exits are long-only, idempotent and retryable after a rejected
attempt or restart. Restart performs Phase 8 account reconciliation and halts on
corruption; the runtime does not repair financial records. Accepted trade quotes update
`paper_marks` transactionally as a **last-trade-price projection**, not as a bid,
liquidation price, or executable quote. Cash, fills, and realized P&L remain derived from
the Phase 8 ledger. Runtime dashboard summaries use read-only SQLite access and report
feed health, mark freshness, recovery, valuation, and reconciliation status.

This phase is a research-only simulation. It adds no live market feed, broker connection,
real-order path, or AI trading brain; `RESEARCH_ONLY` remains mandatory.

The Phase 10A-2 independent review initially rejected its reviewed implementation scope;
a later safety-remediation report is conditional. The Phase 10A-3 calendar candidate has
an implementation report but remains `CONFIGURED`, not operationally accepted. These
subtask reports do not mean that Phase 10 live-data forward paper is complete, accepted,
or authorized. Phase 10 remains planned and prerequisite-gated; see
[`PROJECT_STATE.md`](./PROJECT_STATE.md) and
[`OPEN_DECISIONS.md`](./OPEN_DECISIONS.md).

## AI Agent and Controlled Tool Framework (Phase 9)

`src/aegis/ai_agent/` provides a provider-agnostic, bounded AI research assistant.
The module exposes four sub-components:

- **`models.py`**: Typed data classes for messages (`ChatMessage`, `Role`), tool calls (`ToolCall`, `ToolResult`), provider responses (`LLMResponse`, `ModelUsage`), and audit records (`AgentAuditRecord`, `AgentResearchResult`).
- **`security.py`**: Deterministic permission enforcement (`enforce_tool_permission`), secret redaction (`redact_secrets`, `redact_nested_secrets`), prompt-injection defense (`tag_untrusted_data`), and query-bound validation (`validate_query_bounds`). Security is enforced by Python code, not by prompt instructions alone.
- **`providers.py`**: `LLMProvider` abstract base class. Concrete implementations: `MockLLMProvider` (fully offline, deterministic, no API key required), `GeminiProvider` (REST via stdlib `urllib`), `OpenAIProvider` (REST), `AnthropicProvider` (REST). No provider SDK dependency is required; standard library only.
- **`tools.py`**: `ToolRegistry` with strict recursive argument validation, permission gates, bounded output, and process-isolated handler timeouts. Custom handlers must be importable/pickleable for Windows `spawn` execution. The default registry exposes ten read-only tools: `get_instrument_info`, `get_dataset_quality_summary`, `compute_technical_indicators`, `detect_price_action_patterns`, `get_backtest_summary`, `get_strategy_experiment_results`, `discover_historical_instruments`, `inspect_historical_coverage`, `retrieve_historical_candles`, and `inspect_historical_data_quality`. Paper-account reports and positions are deliberately excluded from provider-visible tools.
- **`orchestrator.py`**: `AIAgent` executes bounded loops (defaults: `max_steps=5`, `max_total_tool_calls=10`, aggregate token ceiling 20,000). Constructor ceilings cap configurable resources. Provider outputs, token usage, tool-call shapes, and audit arguments are validated; malformed provider results fail closed. Citations contain bounded tool-output evidence, and confidence is never elevated solely because a tool completed.

Architectural constraints:
- All quantitative computations (indicators, patterns, quality analysis, backtest performance) remain in deterministic Python. The LLM receives only tool outputs, never raw floats.
- Tool mutation keywords (`order`, `buy`, `sell`, `cancel`, `delete`, `eval`, `shell`, etc.) are blocked at registration time by `enforce_tool_permission`.
- Retrieved data is redacted, HTML-escaped, and wrapped in `<data_block>` tags as defense-in-depth only. Prompt instructions and delimiters are not security boundaries; deterministic permissions, strict schemas, and the explicit tool registry remain authoritative.
- Provider response bodies are limited to 1 MB, provider output is capped (1,024 tokens by default), tool output is bounded (25,000 characters by default), provider calls have validated timeouts, and orchestration enforces step, tool-call, and provider-reported aggregate-token ceilings.
- Tool arguments are recursively redacted in audit records. Raw prompts/responses are not persisted; the audit records hashes, bounded tool output summaries, usage metadata, and errors.
- No LLM API key is required for unit tests or offline use; `MockLLMProvider` provides fully deterministic coverage.
- The AI component is optional: all deterministic research, paper-trading, and backtesting services operate normally when no provider is configured.
- Provider outages, timeouts, and malformed responses fail closed: they produce an `AgentResearchResult` with `status="FAILED"` and do not affect any deterministic service.
- AI agent cannot change execution modes, risk limits, strategy configurations, database schemas, or place orders.
- The Streamlit dashboard exposes an optional AI Research Assistant panel with a prominent disclaimer and no order-submission capability.

### Historical research data tools (AI-1B)

`src/aegis/historical_research.py` reuses the existing Instrument Master,
`historical_candles`/`data_imports` records, read-only SQLite connections, and the
`analyze_dataset` quality engine. It does not create a second registry, candle store, or
quality implementation. The four registered tools have these contracts:

| Tool | Inputs | Result |
|---|---|---|
| `discover_historical_instruments` | Required timeframe; optional exact exchange/symbol, inclusive timezone-aware timestamp range, minimum record count, quality status (`ANY`/`PASS`/`FAIL`/`UNKNOWN`), limit (max 100), and continuation cursor. | Bounded page of instruments with actual stored history, source-use status, quality, and eligibility; discovery scans at most 500 candidates per call. `complete`, `has_more`, `next_cursor`, and `completeness_status` distinguish a finished result from an incomplete scan or page. Instrument Master membership alone is not eligibility. |
| `inspect_historical_coverage` | Exchange, exact symbol, timeframe; optional inclusive timestamp bounds. | First/last stored candle, count, duplicate/continuity information, source-use status, quality and exclusion/eligibility status. |
| `retrieve_historical_candles` | Exchange, exact symbol, timeframe, required inclusive timezone-aware bounds, limit (max 100), and optional continuation cursor. | Chronologically ordered unique candle page, requested/actual range and count, source identities, quality summary, and deterministic page evidence reference. `has_more`, `next_cursor`, and `complete` indicate whether more pages remain. Only data whose contributing imports are all `VERIFIED_PERMITTED` is returned. |
| `inspect_historical_data_quality` | Exchange, exact symbol, timeframe, and inclusive exchange-local `YYYY-MM-DD` bounds. | Existing quality-engine results and warnings, distinct from source-use and research-eligibility status. |

All four tools have `READ_ONLY` permission and execute through the existing registry's
strict schemas and isolated, time-limited handlers. Cursor tokens bind continuation to the
normalized query and page size, validate their position against stored data, and bound
candle pages to the row-ID snapshot captured on the first request. They are continuation
tokens, not authenticated credentials or immutable database snapshots. If data changes so
a continuation cannot produce a page, retrieval reports `DATA_CHANGED` rather than a
success-shaped empty page. The service caps timestamp queries at 3,660 days; intraday
quality evaluation is further bounded to 92 days. Invalid arguments are rejected, and the
tools expose no SQL, Python, filesystem, network, broker, order, or database-write
interface.

Tool output is size-limited by the registry. Oversized responses are replaced with a
structured JSON envelope whose status is `TRUNCATED` and `complete` is false; any preview
is explicitly partial and must not be interpreted as a complete research result.

Quality `PASS` requires the existing engine to report complete data with confirmed full
calendar coverage and an expected-candle count. Detected integrity/completeness failures
remain `FAIL`; unavailable or unconfirmed evidence remains `UNKNOWN`. Eligibility also
requires every contributing source-use declaration to be `VERIFIED_PERMITTED`. The
declaration records the user's permission decision; Aegis does not independently decide
legal rights. Candle-page evidence references hash the canonical request, page cursor and
snapshot, returned candle values, contributing import/source identities and statuses, and
quality status and warnings. Identical page inputs and data reproduce the same reference;
distinct pages or material provenance/quality changes have distinct references. Generated
references and all current proposal evidence remain verification `UNKNOWN`; reference
generation is not independent provenance verification.

Known limitations: retrieval returns at most 100 records per call; discovery scans at most
500 candidates per call and callers must follow cursors until `complete` is true before
treating results as exhaustive; calendar-dependent quality cannot pass without locally
confirmed coverage; and import permission is user-declared rather than independently
verified.
AI-1C remains responsible for any bounded research/decision workflow that consumes these
tools, including evidence resolution and validation before it can create proposals. No
autonomous decision, provider, or execution path is added by AI-1B.
