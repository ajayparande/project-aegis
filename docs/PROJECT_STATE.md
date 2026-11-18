# Project Aegis — Current Project State

**Snapshot date:** 2026-11-18

**Mode:** `RESEARCH_ONLY`
**Purpose:** a point-in-time status record. It does not grant permission to begin a
planned milestone. Consult the linked task and review record before relying on a status.

## Status vocabulary

| Term | Meaning |
|---|---|
| **Implemented** | Relevant code exists for the stated scope. |
| **Tested** | The named tests were actually executed and the result is recorded. Test files alone do not prove this. |
| **Independently reviewed** | A distinct review examined the exact stated scope. |
| **Accepted** | An authorized acceptance decision applies to the exact scope. |
| **Operationally ready** | Required real data, calendar, permissions, reliability, and other operational prerequisites are met. |
| **Empirically validated** | Performance conclusions are supported by a permitted dataset and an approved evaluation protocol. |
| **Authorized** | The user has explicitly approved the specific activity. No other status implies authorization. |

These statuses are independent. In particular, implemented, tested, and
independently reviewed do not automatically mean accepted; acceptance does not imply
operational readiness, empirical validity, or authorization.


## Checkpoint and next-session handoff status

Checkpoint 1 is committed locally at
`18ddc9da7fa5021b3709f4a0658e03eb0de611f6`; this documentation verification confirmed
that commit as HEAD. Checkpoint 2's approved-calendar provenance and Phase 8
`aegis.ProposalValidationResult` public-identity blockers are reported corrected.
Codex's implementation report records **34 new regressions and 305 focused tests
passing, 0 failed, 0 skipped**, with Ruff, format, focused mypy and diff checks passing.
These are reported implementation results, not tests rerun by this documentation task.
**Checkpoint 2 independent acceptance remains pending; it is not committed.**

The [2026-11-18 handoff](./PROJECT_HANDOFF_2026-11-18.md) links the proposed
[architecture review](./ARCHITECTURE_360_REVIEW_2026-11-17.md),
[ML/optional LLM plan](./ML_LLM_AND_EVALUATION_PLAN.md),
[Decision Journal v1](../tasks/DECISION_JOURNAL_V1.md), and
[live-data paper MVP](../tasks/PHASE_10_LIVE_DATA_PAPER_MVP.md).
The new journal and live-data MVP are **PLANNED / PENDING APPROVAL, not implemented**;
existing Phase 8/10A synthetic foundations remain separate. LightGBM is the selected
offline research candidate, not a trained or validated trading model. Claude is optional
and excluded from the critical execution path. These proposals do not approve or
supersede any architecture decision or relax full Phase 10 acceptance criteria.

`RESEARCH_ONLY` remains active. Broker/live-data access, subscription, normal database
operations and a future paper operating mode require separate explicit authorization.

## Current M2 development gate

**M2-C is independently accepted for qualified historical per-sleeve M1B composition.
M2-D accounting is implemented and technically ACCEPTED; distinct-reviewer independent
sign-off remains pending. M2-E has not started and is not authorized. Overall M2 is
not accepted.**

M1B remains previously accepted within its bounded one-instrument scope, and M2-A
remains accepted for contracts/allocation. M2-B remains independently accepted within
its bounded shared-qualification scope. Its independent read-only audit returned
**ACCEPT on 2026-11-16**, according to project-owner-supplied history; the original
review record is not yet in the repository. The subsequent audit found incorrect
schema-version provenance. The correction uses the maximum applied version in the
same read snapshot, with isolated regressions. Focused independent review of that
correction remains pending; no automatic reacceptance or closure is claimed.

M2-C's qualified historical composition and M2-D's actual accounting implementation are
present in the repository. M2-C's earlier duplicate-weekday rejection and remediation
reports remain historical evidence; the later independent bounded ACCEPT is the current
status. M2-D's technical ACCEPT does not establish distinct-reviewer independent
acceptance.

**Acceptance evidence:** The M2-C independent bounded ACCEPT and M2-D technical ACCEPT
above are recorded from the project owner's supplied acceptance decisions for this
2026-11-17 documentation correction. Separate original M2-C/M2-D acceptance artifacts
were not identified in the repository. No acceptance dates or new test results are
inferred from this correction date; this documentation pass is not a new independent
audit. Existing implementation-reported test evidence is retained below.

M2 remains historical research/backtesting only. It must not invoke Phase 8. M2-C is
limited to per-sleeve execution; M2-D aggregation and M2-E valuation/hypothetical
liquidation remain separate stages. The approved design remains
[`tasks/M2_PORTFOLIO_ACCOUNTING.md`](../tasks/M2_PORTFOLIO_ACCOUNTING.md).


## Milestone status

| Scope | Implementation status | Review / acceptance status | Readiness and limits |
|---|---|---|---|
| Phase 1 foundation | Task file marks foundation work complete. | See [`tasks/PHASE_01_FOUNDATION.md`](../tasks/PHASE_01_FOUNDATION.md). | Local foundation only. |
| Phase 2 market data | Instrument master, CSV ingestion, quality/calendar engine, and source-use gates are implemented in documented scope. | Full operational prerequisites remain incomplete. | The checked-in NSE cash calendar candidate is `CONFIGURED`, not `CONFIRMED`; real source-use permission and real permitted data remain unresolved. |
| Phase 3 Strategy Lab | Deterministic, versioned strategy core is implemented. | Do not treat as AI hypothesis-generation acceptance. | AI-assisted drafting and generated-code execution/isolation remain deferred. |
| M1B one-instrument execution | Qualified one-instrument execution interface exists. | **Independently accepted** for the bounded M1B interface, per project-owner status supplied for this handover; acceptance record/date is not identified in the repository. | Does not accept all of Phase 4 or establish data readiness, profitability, live readiness, or authorization. See [`tasks/PHASE_04_BACKTESTING.md`](../tasks/PHASE_04_BACKTESTING.md). |
| Phase 4 backtesting | Deterministic simulator and M1B path are implemented; synthetic regression coverage is documented. | Full Phase 4 acceptance remains pending its documented external/operational blockers. | Synthetic tests are not real-data or performance evidence. |
| M2-A contracts/allocation | Immutable request/policy contracts and deterministic paise allocation exist in [`src/aegis/portfolio.py`](../src/aegis/portfolio.py). | **Accepted** according to project-owner-supplied history; no dated independent acceptance artifact is identified in the repository. | This does not accept M2-B or M2 v1. |
| M2-B shared qualification | Coordinator exists in [`src/aegis/portfolio_qualification.py`](../src/aegis/portfolio_qualification.py); focused synthetic tests exist in [`tests/test_portfolio_qualification.py`](../tests/test_portfolio_qualification.py); M1B qualification is factored for reuse in [`src/aegis/backtesting.py`](../src/aegis/backtesting.py). | **Independently reviewed and ACCEPTED on 2026-11-16** for qualification-only scope. The original read-only audit record is not yet stored in the repository; acceptance is owner-reported. Subsequent schema-provenance correction requires focused independent review. | Does not execute strategies or simulate fills. Synthetic fixtures do not establish operational calendar/data readiness. |
| M2-C per-sleeve execution | Orchestration exists in [`src/aegis/portfolio_execution.py`](../src/aegis/portfolio_execution.py), with focused tests in [`tests/test_portfolio_execution.py`](../tests/test_portfolio_execution.py). Calendar constructor, exceptional-closure, and duplicate-weekday validation are included in the current worktree. | **Independently accepted** for qualified historical per-sleeve M1B composition, per project-owner-supplied acceptance history; see the evidence limitation above. The earlier duplicate-weekday rejection/remediation reports are historical, not the current acceptance gate. | Earlier implementation-reported validation (before these corrections): market-quality 63 passed; portfolio execution 50 passed; portfolio and qualification 79 passed; backtesting calendar 9 passed/1 skipped; Strategy Lab calendar 3 passed; historical research 32 passed. Ruff, formatting, focused mypy, and diff-check passed. These are implementation-reported results, not independent acceptance. No normal database was accessed. |
| M2-D portfolio accounting | Actual aggregation and reconciliation exist in [`src/aegis/portfolio_accounting.py`](../src/aegis/portfolio_accounting.py), with [`tests/test_portfolio_accounting.py`](../tests/test_portfolio_accounting.py) and the private M2-C evidence handoff. | **Implemented and technically ACCEPTED**, per project-owner-supplied history; **distinct-reviewer independent sign-off remains pending**. Technical ACCEPT is not independent acceptance; see the evidence limitation above. | Actual accounting only. M2-E daily valuation/hypothetical liquidation remains separate. No portfolio persistence, Phase 8 execution, forward-paper operational readiness, or live-trading authorization is established. |
| M2-E valuation/hypothetical liquidation | Not started. | Not authorized or accepted. | M2-C acceptance and M2-D technical ACCEPT do not authorize M2-E. |
| M2-F / M2-G integration and whole-M2 audit | Later stages in the approved M2 specification. | No acceptance is recorded here; overall M2 is not accepted. | Existing focused tests do not establish completion of the M2-F integration stage or the M2-G independent whole-M2 audit. M2 v1 constraints remain binding. |
| Phase 5 price-action library | Implemented as a bounded, descriptive research library. | Accepted with documented limitations in [`docs/reviews/PHASE_05_IMPLEMENTATION_REPORT.md`](./reviews/PHASE_05_IMPLEMENTATION_REPORT.md). | Not a strategy, predictive claim, or execution interface. |
| Phase 6 indicators | Approved subset implemented as deterministic research calculations. | Accepted with documented limitations in [`docs/reviews/PHASE_06_IMPLEMENTATION_REPORT.md`](./reviews/PHASE_06_IMPLEMENTATION_REPORT.md). | Deferred catalog items remain deferred; indicators do not establish strategy value. |
| Phase 7 ML research | In-memory classifier research harness is implemented. | Not accepted for empirical performance claims; see [`docs/reviews/PHASE_07_IMPLEMENTATION_REPORT.md`](./reviews/PHASE_07_IMPLEMENTATION_REPORT.md). | Cost-aware strategy comparison and credible permitted holdout evidence remain unmet. |
| Phase 8 paper execution | Local simulation, ledger, risk and reconciliation foundation exists. | Its implementation reports describe the bounded foundation; this does not approve operating it as a paper mode. | Phase 8 remains the sole execution/risk boundary, while application mode remains `RESEARCH_ONLY`. |
| Phase 9 / AI-1B | Optional provider/tool framework and bounded historical research tools exist. | AI-1B has a conditional/bounded acceptance in its documented scope; this is **not M1B** and is not acceptance of an autonomous research brain. | AI-1C end-to-end researcher workflow, evidence verification, evaluation protocol enforcement, and generated-code isolation remain deferred or unmet. |
| Phase 10 forward paper | Full live-data forward-paper observation remains planned and prerequisite-gated. | Not accepted as an operational phase. | Do not infer readiness from synthetic forward-runtime work. The initial Phase 10A-2 independent review rejected its scope; a later remediation report is conditional, and Phase 10A-3 has an implementation report. These do not equal full Phase 10 acceptance or authorization. |
| Phase 11 cloud/reliability | Future work. | Not accepted or authorized. | Requires separately approved, measured deployment scope and cost approval. |
| Phase 12 live readiness | Future assessment only. | Default decision is **NO-GO**; no live implementation is approved. | An assessment cannot automatically authorize live operation. |

## Data, calendar, and database cautions

- The checked-in NSE cash calendar is a source-backed **candidate** with `CONFIGURED`
  status, not operationally confirmed. Current evidence and limitations are in
  [`EXCHANGE_CALENDAR_NSE_CASH_2025_2026_EVIDENCE.md`](./EXCHANGE_CALENDAR_NSE_CASH_2025_2026_EVIDENCE.md)
  and [`EXCHANGE_CALENDAR_CONFIGURATION.md`](./EXCHANGE_CALENDAR_CONFIGURATION.md).
- `VERIFIED_PERMITTED` is an auditable user declaration, not Aegis's determination
  of licensing or legal rights.
- The source-defined database schema constant is currently `SCHEMA_VERSION = 16` in
  [`src/aegis/database.py`](../src/aegis/database.py). This is a code fact, **not** a
  statement about the user's on-disk database version, contents, or migration state. Do
  not inspect or modify the normal database without explicit approval.
- A historical Phase 9 review reports that an unexpected normal-database migration
  was detected and that the then-current database/log were to be retained without
  recovery. Treat database contents as protected and unknown unless the user explicitly
  authorizes a safe, scoped inspection.
- Historical implementation/review reports record evidence as of their own task dates.
  Do not reuse their local database contents or test totals as current facts.

## Known acceptance and readiness gaps

1. Distinct-reviewer independent sign-off for technically ACCEPTED M2-D accounting,
   and focused independent review of the M2-B schema-provenance correction. M2-E has
   not started and is not authorized; overall M2 remains unaccepted.
2. Confirmed, adequately covered exchange calendars for the actual requested instruments
   and dates, including unresolved calendar-model limitations.
3. Independently verified source-use rights and suitable real historical data.
4. Phase 4/7 empirical validation using permitted, quality-gated data and a frozen
   research protocol.
5. AI-1C's bounded evidence-backed research loop and its independent acceptance.
6. All Phase 10 live-data, calendar, source, paper, risk, reliability, and observation
   prerequisites; no forward-paper operation is authorized by this status page.
7. Phase 11 deployment/recovery evidence and Phase 12 independent NO-GO/GO assessment.

See [`OPEN_DECISIONS.md`](./OPEN_DECISIONS.md) for unresolved choices and
[`DEVELOPMENT_ROADMAP.md`](./DEVELOPMENT_ROADMAP.md) for nonbinding strategic plans.
This snapshot includes implementation-run focused test evidence, recorded below.
No normal application database or operational workflows were accessed.


## 2026-11-16 targeted correction implementation evidence

This section preserves the earlier implementation report and test counts as historical
evidence. Those tests were not rerun during the 2026-11-17 documentation correction.

The authorized correction run changed only schema-version provenance, AI tool subprocess
transport, constructor/JSON closure validation parity, their regressions, and supporting
documentation. It did not change M1B simulation, strategy, sizing, risk policies, database
schema, calendar JSON, or operating mode. Tests used isolated temporary databases.

- M2 provenance now reads the maximum applied version from the existing read snapshot,
  rejects empty/invalid metadata, and preserves deterministic batch fingerprints.
- Tool transport receives a bounded JSON frame while the worker runs, with a monotonic
  deadline, bounded cleanup, malformed/oversized-message rejection, and process reaping.
  A reader thread prevents incomplete frames from blocking the parent past its deadline.
- Direct calendar construction now rejects holiday/exceptional-closure overlap, matching
  JSON validation. Valid special trading sessions retain precedence over holidays.
- Focused schema tests: 6 passed / 24 deselected. Focused calendar tests: 3 passed /
  62 deselected. Initial AI security/transport run: 31 passed.
- Final combined calendar/M2 regression: **200 passed, 0 failed, 0 skipped**.
- Final AI security and existing public-tool regression: **66 passed, 0 failed, 0 skipped**.
  This also covers Unicode transport and a deliberately blocked incomplete receive.
- Final Ruff check and format verification passed for all six changed Python files;
  focused mypy passed for all three changed production modules. The full suite was not run.

These were implementation-run results, not independent acceptance. At that report's
date, M2-B's correction required focused independent review, M2-C's separate read-only
acceptance audit was pending, and M2-D/M2-E and forward-paper/live operation were
unauthorized. Subsequent M2-C independent acceptance and M2-D technical ACCEPT are
recorded in the current status above; the M2-B correction caveat remains unresolved.
M2-E remains unstarted and unauthorized; forward-paper operational readiness and
live-trading authorization are not established.


## November 18 development checkpoint

See [the development checkpoint](reviews/PROJECT_CHECKPOINT_2026-11-18.md) for the verified INR 10,000 synthetic paper pilot and outstanding milestones.


## Validation checkpoint — 18 November 2026

### Confirmed results

- M2 portfolio test suite: 239 passed.
- Upstox universe test suite: 58 passed.
- Total focused tests: 297 passed.
- Ruff: passed.
- Git diff whitespace validation: passed.
- Database schema: v16.


### Static validation — 18 November 2026

**Status: PASSED**

#### M2 portfolio validation

- Test results: 239 passed.
- Database schema: v16.
- Outdated schema v15 assertions in portfolio
  qualification tests were corrected.
- No portfolio test failures remain in the focused suite.

#### Upstox universe validation

- Test results: 58 passed.
- Final regression run: PASSED (3.36 seconds).
- Two original typing errors in
  `src/aegis/providers/universe_sources.py`
  were corrected.
- The subsequent variable redeclaration error
  was also resolved.

#### Static analysis

- MyPy: PASSED — 45 source files, zero errors.
- Ruff: PASSED — all checks passed.
- Git diff --check: PASSED.
- Three informational `annotation-unchecked` notes
  remain in `paper_trading.py`.
- Git reported an LF/CRLF conversion warning for
  `tests/test_market_quality.py`. This did not
  cause validation failure.

#### Combined focused results

- M2 portfolio: 239 passed.
- Upstox universe: 58 passed.
- Total: 297 tests passed.
- MyPy: PASSED.
- Ruff: PASSED.
- Git diff check: PASSED.

#### Synthetic paper trading

P10-P3 previously completed successfully:

- Initial capital: INR 10,000.
- Final equity: INR 10,000.15.
- Realized net P&L: INR 0.15.
- BUY_FILLED: 1.
- SELL_FILLED: 1.
- Reconciliation: RECONCILED.
- Restart verification: PASSED.
- Broker: NONE.
- Live orders: FALSE.

The reported profit is from deterministic synthetic
data and does not establish real-market profitability.

#### Acceptance boundaries

Passing focused tests does not independently establish
acceptance of every M2 or Upstox milestone.

- M1B: previously accepted within its qualified scope.
- M2-B: previously accepted.
- M2-C: independent acceptance evidence must be
  confirmed before marking accepted.
- Upstox universe: focused validation passed.
- P10-P3: synthetic paper pilot passed.
- P10-P4: strategy-driven paper trading pending.
- P10-P5: continuous synthetic trading pending.

Project Aegis remains RESEARCH_ONLY.
No live trading or broker order placement is authorized.


MyPy initially reported two errors in
`src/aegis/providers/universe_sources.py`:

- Line 133: incompatible assignment (`Any | None` to `str`).
- Line 219: `len()` argument typed as `object`.

The initial corrections resolved the original type errors,
but a subsequent MyPy run identified a variable redeclaration
at line 133 (`no-redef`).

The final correction and successful MyPy rerun are pending.

### Synthetic paper trading

P10-P3 previously passed with:

- Initial cash: INR 10,000.
- Final cash: INR 10,000.15.
- Final equity: INR 10,000.15.
- Realized net P&L: INR 0.15.
- One filled BUY and one filled SELL.
- Reconciliation: RECONCILED.
- Restart verification: passed.
- Forward runtime regression tests: 75 passed.
- Broker: NONE.
- Live orders: false.

These results use deterministic synthetic market data.
The reported profit is not evidence of real-market
profitability.

### Development and acceptance status

- M1B: accepted within its qualified execution scope.
- M2-B: previously accepted.
- M2-C: acceptance status requires confirmation
  against the final independent audit.
- Upstox universe: focused tests passed; static
  validation remains pending.
- P10-P3: synthetic pilot passed.
- P10-P4: strategy-driven BUY/SELL/HOLD integration pending.
- P10-P5: continuous synthetic trading pending.

### Next milestones

1. Resolve the remaining MyPy error.
2. Revalidate the Upstox universe modules.
3. Review and commit M2 implementation and tests.
4. Review and commit Upstox universe implementation.
5. Synchronize remaining documentation.
6. Implement P10-P4 strategy-driven synthetic trading.

### Safety boundaries

- RESEARCH_ONLY.
- No live broker orders.
- No real-market execution.
- No credentials required for the synthetic pilot.
- Initial synthetic paper capital: INR 10,000.
- Existing execution, risk, evidence and authorization
  boundaries must remain enforced.
