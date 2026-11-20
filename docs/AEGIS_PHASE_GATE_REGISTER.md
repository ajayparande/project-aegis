# Project Aegis — Phase Gate Register

**Register version:** 1.3
**Snapshot date:** 2026-11-20
**Branch / HEAD:** `main` / `9b306bdf71a121392cbcc9badbdf6c3767ef2a0c`
**Worktree:** dirty; index empty; `main` is 26 commits ahead of `origin/main`
**Requirements source:** [Master Product and System Requirements](./PROJECT_AEGIS_MASTER_PRODUCT_AND_SYSTEM_REQUIREMENTS.md)
**Validation evidence:** [Phase 1 Foundation Validation](./reviews/PHASE_01_FOUNDATION_VALIDATION_2026-11-20.md); [isolated GitHub Actions run 44764101110](https://github.com/ajayparande/project-aegis/actions/runs/44764101110)

This register records implementation gates, not permission to operate. `ACTIVE_PHASE`
identifies the only currently authorized implementation boundary; it is not an
acceptance verdict. Later phases and milestones remain locked until the active phase
closes under I04.

## Current authority addendum — 2026-11-20

The approved future product is AI/LLM-led: the agent originates evidence-backed
proposals, supporting analysis and ML provide evidence, and deterministic Python
remains authoritative for validation, risk, authorization, routing, accounting, and
reconciliation. Unified PAPER/LIVE gateways and a controlled transition are future
requirements, not current implementation or operating permission. Existing research-only
code and bounded historical acceptances remain as recorded; no wider M-series acceptance
is inferred.

The isolated Phase 1 CI backport is accepted for its exact scope below. The current
assignment closes Phase 2 software acceptance documentation only. Phase 2 is accepted
for the reviewed software scope below; operational evidence remains outstanding. The
application remains `RESEARCH_ONLY`; broker connectivity, credentials, live orders,
forward-paper operation, and live execution are not authorized. Phase 3 remains blocked
pending independent Phase 1 acceptance of the broader local baseline and a separate
Project Manager assignment.

The user-referenced **“PROJECT AEGIS — BUILD GUIDE FOR AI CODE ASSISTANTS,” Part 00
and Part K (v4)** was not found in this repository/worktree. Its contents are not
inferred here. The available master requirements and repository-approved constraints
remain in force.

## Current task and implementation gate

```text
CURRENT_TASK: PHASE_02_SOFTWARE_ACCEPTANCE_CLOSURE
CURRENT_TASK_SCOPE: DOCUMENTATION_ONLY
ACTIVE_IMPLEMENTATION_PHASE: NONE
```

| Scope | Dependencies | Status | Evidence, acceptance boundary, and blocker |
|---|---|---|---|
| **PHASE_01_FOUNDATION — Local Foundation** | None | **ACCEPTED — isolated CI backport only** | GitHub Actions run [44764101110](https://github.com/ajayparande/project-aegis/actions/runs/44764101110) succeeded for branch `ci/phase1-foundation`, commit `97712ca3eedcce8f3671737f4e76a55030c04ace`, based on `7bdd4cfe2c217e0628872affa74d25e4671db950`. Hosted pytest reports 420 passed; Ruff, format, MyPy, `pip check`, Gitleaks, and `.gitignore` checks passed. This does not validate local `main` at `9b306bdf...`, its other 25 unpublished commits, or the dirty M2–M5/forward-runtime/research changes. It is not independent acceptance or operational readiness for those wider changes. See the [acceptance record](../tasks/PHASE_01_FOUNDATION.md) and [validation report](./reviews/PHASE_01_FOUNDATION_VALIDATION_2026-11-20.md). |

Phase 2 software acceptance does not establish operational readiness or unlock Phase 3.
Phase 3 remains blocked pending independent acceptance of the broader Phase 1 local
baseline and a separate Project Manager assignment. The Phase 3 task marks its
deterministic requirements complete; its unchecked AI-drafting and generated-code
isolation items are explicitly deferred to Phase 9, so no missing in-scope Phase 3
increment is identified.

## Historical phases

| Identifier | Scope | Dependencies / relation | Status | Evidence and limitations |
|---|---|---|---|---|
| **PHASE-02** | Market-data foundation: instruments, imports, quality, calendar and source-use gates | Phase 1; real-data use also needs source/calendar evidence | **ACCEPTED — reviewed software scope only** | The initial independent audit returned REJECT for inconsistent SQLite snapshots. The single blocker was corrected: readiness uses one read-only connection and explicit `BEGIN` for instrument, quality, provenance, source-use, and import-ID checks. The deterministic WAL concurrent-update regression failed before correction and passed after. Focused tests: 170 passed; Ruff lint/format, MyPy (62 source files), and `git diff --check` passed. The corrected code was not independently re-audited and hosted CI was not run. Operational evidence remains outstanding; see below. |
| **PHASE-03** | Deterministic Strategy Lab | Phase 1; qualified inputs for eligible runs | **BLOCKED** | Independent Phase 1 acceptance of the broader local baseline and a separate Project Manager assignment remain required. The Phase 3 task marks its deterministic core complete; AI drafting and generated-code isolation are explicitly deferred to Phase 9. |
| **PHASE-04 / M1B** | Backtesting and bounded one-instrument execution | Phase 2 data/provenance and Phase 3 strategy | **BLOCKED** | Phase 4 independent report remains “APPROVE WITH CONDITIONS”; real-data use is not ready. M1B acceptance is bounded and owner-reported without an in-repository dated artifact. |
| **PHASE-05** | Descriptive candlestick/price-action library | Completed candles | **IMPLEMENTED_AWAITING_AUDIT** | Implementation report records acceptance with limitations; distinct independent phase acceptance is not established. |
| **PHASE-06** | Bounded deterministic indicator subset | Completed candles | **IMPLEMENTED_AWAITING_AUDIT** | Implementation report records acceptance with limitations; distinct independent phase acceptance is not established. |
| **PHASE-07** | ML research and evaluation | Permitted, quality-gated data and frozen evaluation protocol | **BLOCKED** | Harness exists; permitted holdout and cost-aware empirical evaluation are absent. No model is accepted for trading. |
| **PHASE-08** | Simulated execution, accounting, risk and reconciliation foundation | Trusted input evidence; remains the sole simulated execution/risk boundary | **IMPLEMENTED_AWAITING_AUDIT** | Bounded implementation and tests exist. No phase-wide independent acceptance or forward-paper authorization is inferred. |
| **PHASE-09 / AI-1B** | Bounded AI provider/tool framework and historical research tools | Qualified, permitted evidence | **IMPLEMENTED_AWAITING_AUDIT** | Conditional/bounded reports do not establish AI-1C, autonomous trading, or execution authority. |
| **PHASE-10** | Full live-data forward PAPER product and operational evidence | Prior gates, authorized permitted feed, confirmed calendar and source evidence | **BLOCKED** | Synthetic work is not operational acceptance. Phase 10A-2 had a REJECT followed by conditional remediation; full phase remains open. |
| **PHASE-11** | Deployment, monitoring and recovery | Accepted operating scope and explicit deployment/budget approval | **NOT_STARTED** | No deployment or reliability acceptance evidence. |
| **PHASE-12** | Independent live-readiness assessment | Prior accepted phases and separate readiness evidence | **BLOCKED** | No completed assessment; default remains NO-GO. Assessment is not live authorization. |

## Phase 2 baseline and software acceptance closure

**Software verdict: ACCEPT, limited to the reviewed Phase 2 software scope.** The
initial independent audit returned REJECT for one blocker: readiness assembled
instrument, dataset-quality, and source-provenance evidence from separate SQLite
snapshots. The correction in `assess_evidence_readiness()` opens one read-only
connection, executes `BEGIN`, and uses that transaction for instrument activity,
`analyze_dataset()`, source-import identity/provenance, source-use declarations, and
caller-supplied import-ID validation. A deterministic WAL concurrent-update regression
failed before the correction and passed afterward.

The focused Phase 2 suites reported 170 passed. Ruff lint and formatting passed; MyPy
passed for 62 source files; `git diff --check` passed. These are implementation
verification results, not hosted CI evidence. The corrected code was not independently
re-audited. No separate Phase 2 acceptance report existed in the repository, so this
closure is recorded here, in the project-state snapshot, and in the Phase 2 task.

This software acceptance does not establish operational readiness. Authoritative
confirmed calendar coverage, permitted real historical OHLCV, independent source-rights
and entitlement evidence, and Phase 4 acceptance/readiness remain outstanding.
Affected workflows must continue to fail closed, and no live-trading authorization is
granted.

The local engineering scope described in [the Phase 2 contract](../tasks/PHASE_02_MARKET_DATA.md)
already exists; this acceptance is limited to that reviewed software scope:

- The provider-neutral NSE/BSE instrument master validates identity and derivative
  fields, stores exact decimal metadata, and supports CRUD/search and row-level bulk
  import in [`instruments.py`](../src/aegis/instruments.py).
- Local CSV OHLCV ingestion validates explicit-offset timestamps, supported interval
  alignment and candle values; it stores canonical decimal prices, checksums, import
  history, and row-level quality issues without overwriting existing candles in
  [`market_data.py`](../src/aegis/market_data.py).
- Local JSON calendars distinguish `CONFIGURED` from `CONFIRMED`, represent coverage,
  holidays and exceptional sessions, and drive completed-candle, coverage, quality,
  duplicate, and freshness analysis in [`market_quality.py`](../src/aegis/market_quality.py).
- Imports default to `UNVERIFIED`; audited user declarations are recorded as provenance,
  not independently verified legal rights. Strategy Lab, backtesting, and paper BUY
  gates require permitted source status and their applicable quality evidence
  ([`database.py`](../src/aegis/database.py), [`strategy_lab.py`](../src/aegis/strategy_lab.py),
  [`paper_trading.py`](../src/aegis/paper_trading.py)).
- Upstox-related provider, HTTP, stream, and research files also exist in the unpublished
  local baseline ([`upstox.py`](../src/aegis/providers/upstox.py),
  [`upstox_http.py`](../src/aegis/providers/upstox_http.py),
  [`upstox_market_stream.py`](../src/aegis/providers/upstox_market_stream.py),
  [`upstox_collection.py`](../src/aegis/providers/upstox_collection.py),
  [`upstox_observation.py`](../src/aegis/upstox_observation.py), and
  [`upstox_research_session.py`](../src/aegis/upstox_research_session.py)). They are
  outside the isolated Phase 1 CI commit; their presence does not establish source
  entitlements or operational acceptance. They were not executed or connected to.

**Remaining operational blockers:** source licensing/entitlements and retention/rate-limit terms
must be established for the intended source; a permitted real historical dataset is
needed; authoritative exchange-calendar coverage must be independently confirmed for
the intended exchanges, segments, and dates (the checked-in NSE cash candidate covers
2025–2026 and remains `CONFIGURED`); and Phase 4 acceptance remains pending. These
requirements are not satisfied by synthetic fixtures or a recorded source-use
declaration. No external provider/live feed is authorized by this closure.

**Phase 3 disposition:** implementation did not begin. The Phase 3 task marks the
machine-readable strategy specification, experiment registry, SMA baseline, supported
universe, deterministic tests, run provenance, and strategy-version immutability as
implemented. Its remaining unchecked AI-drafting and generated-code isolation items
are explicitly deferred to Phase 9. The current gate also requires independent Phase 1
acceptance of the broader local baseline and a separate Project Manager assignment;
neither is recorded here. No Phase 3 requirement was invented or pulled forward.

## M-series

| Identifier | Scope | Dependencies / relation | Status | Evidence and limitations |
|---|---|---|---|---|
| **M1B** | Qualified one-instrument historical execution interface | Phase 2/3 evidence; child of Phase 4 scope | **IMPLEMENTED_AWAITING_AUDIT** | Owner-reported bounded independent acceptance; no dated original artifact in repository. Does not accept all Phase 4. |
| **M2-A** | Portfolio contracts and exact-paise allocation | Approved M2 v1 contract | **IMPLEMENTED_AWAITING_AUDIT** | Owner-reported accepted; original independent artifact absent. |
| **M2-B** | Shared portfolio qualification | M2-A and Phase 2 trust gates | **IMPLEMENTED_AWAITING_AUDIT** | Owner-reported independent ACCEPT for qualification only; schema-provenance correction still requires focused independent review. |
| **M2-C** | Qualified per-sleeve M1B composition | M2-A/B | **IMPLEMENTED_AWAITING_AUDIT** | Bounded acceptance is reported, but original artifact is absent and an older checkpoint conflicts. Does not accept parent M2. |
| **M2-D** | Portfolio aggregation and reconciliation | M2-C | **IMPLEMENTED_AWAITING_AUDIT** | Technical ACCEPT is reported; distinct-reviewer sign-off remains pending. |
| **M2-E** | Daily valuation and hypothetical liquidation | M2-D and approved M2 policy | **NOT_STARTED** | Not started or authorized. |
| **M2-F** | Synthetic whole-M2 integration/replay | M2-A–E | **IMPLEMENTED_AWAITING_AUDIT** | Related tests exist; whole-stage acceptance is not established. |
| **M2-G / M2 parent** | Independent whole-M2 review and acceptance | All mandatory M2 stages | **BLOCKED** | M2-E, M2-B correction review, M2-D sign-off and full parent evidence remain open. M2 is historical-only and does not use Phase 8. |
| **M3** | Real data/calendar/provider qualification | Phase 2 and external source/calendar evidence | **BLOCKED** | Reports classify actual qualification as partial/blocked; sample remains `UNVERIFIED`, calendar `CONFIGURED`. |
| **M4** | Paper equity evidence and read-only views | Phase 8 records | **IMPLEMENTED_AWAITING_AUDIT** | Code/tests exist; no distinct independent M4 acceptance located. |
| **M5-A** | Synthetic multi-instrument scheduling | Trusted existing runtime | **IMPLEMENTED_AWAITING_AUDIT** | Scheduling-only; no decision or order authority. Independent remediation review remains pending. |
| **M5-B1** | Read-only hypothetical portfolio pre-trade | M5-A evidence and existing risk contracts | **IMPLEMENTED_AWAITING_AUDIT** | Hypothetical report only; owner-reported technical acceptance is not independent acceptance or execution authority. |
| **M5-B2** | Proposed portfolio paper execution through Phase 8 | Explicit authorization and all contract prerequisites | **BLOCKED** | Contract only; no implementation. It is not the active phase and is not authorized by this register. |

## AI milestones and product gates

| Identifier | Scope | Dependencies / relation | Status | Evidence and limitations |
|---|---|---|---|---|
| **AI-1B** | Bounded read-only historical research tools | Permitted source/evidence | **IMPLEMENTED_AWAITING_AUDIT** | Conditional scope only; no AI-1C or trading authority. |
| **AI-1C** | End-to-end evidence-backed research loop | AI-1B, permitted data and approved evaluation protocol | **NOT_STARTED** | No connected, independently accepted loop established. |
| **Claude Trading Skills** | Approved-source/provenance workflow into reviewed causal logic | Rights, provenance and review policy | **NOT_STARTED** | No qualifying implementation or test evidence. |
| **GATE-0** | Trusted research foundation | Phase 1 and data trust | **BLOCKED** | Phase 1 and Phase 2/M3 gates remain open. |
| **GATE-1** | Synthetic decision/risk/accounting/journal/recovery proof | Trusted contracts and Phase 8 | **IMPLEMENTED_AWAITING_AUDIT** | Components/tests exist; no independent full-gate verdict. |
| **GATE-2** | Feature-complete real-data AI PAPER product with unified PAPER/LIVE gateways | GATE-0/1 and all mandatory product capabilities | **BLOCKED** | Unified AI-first product, operational data, finished UI, multi-symbol behavior, live gateway contract, and full end-to-end acceptance are not established. |
| **GATE-3** | Live readiness | Accepted GATE-2 and external account/legal/security evidence | **BLOCKED** | No live authorization or readiness assessment. |
| **GATE-4** | Audited PAPER/LIVE mode transition | GATE-3 and explicit owner authorization | **NOT_STARTED** | Current app remains `RESEARCH_ONLY`. |
| **GATE-5** | Bounded autonomous live validation | GATE-4 | **NOT_STARTED** | No live orders or authorization. |
| **GATE-6** | Scope expansion | Accepted live evidence and explicit scope grant | **NOT_STARTED** | No expansion authorized. |

## Gate controls and evidence rules

1. The Phase 1 `ACCEPTED` status is limited to the exact isolated backport and workflow
   run above. It does not validate local `main` or its unpublished and uncommitted work;
   no independent acceptance of that broader baseline is inferred.
2. Phase 2 software acceptance is limited to the reviewed scope and does not satisfy its
   operational evidence requirements. The AI-first PAPER/LIVE target remains
   future-state and does not alter current `RESEARCH_ONLY` safety rules.
3. This register does not assert on-disk database version or contents. The dirty
   worktree's source schema constant is 18; the normal application database was not
   inspected. The CI commit above did not validate the dirty worktree's database changes.
4. No entry in this register grants broker access, credentials, data rights, paid
   services, live orders, forward-paper operation, or authorization to begin a later
   phase.
5. On a subsequent evidence change, update this register's version and date, retain the
   previous acceptance record, and identify the exact tested baseline and worktree.
