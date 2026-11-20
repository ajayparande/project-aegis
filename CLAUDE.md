# Project Aegis — CLAUDE.md
**Updated: 2026-11-18 | Status snapshot, not authorization**

This file is the mandatory first read for Claude and coding agents. Read `AGENTS.md`, applicable approved decisions, milestone contracts, and the current worktree before making changes. Preserve all existing uncommitted work.

## Current authority addendum — 2026-11-20

This addendum supersedes conflicting active-task and future-product descriptions in
the 2026-11-18 snapshot below; it does not rewrite historical implementation or
acceptance evidence.

- **Current implementation:** Existing code remains `RESEARCH_ONLY`, with bounded
  research and M-series implementations and only the scoped acceptances recorded in
  `docs/PROJECT_STATE.md` and the phase-gate register. Those records do not imply
  whole-phase acceptance or operational readiness.
- **Approved future product:** The finished product is AI/LLM-led: the agent originates
  evidence-backed proposals, supporting technical analysis and ML provide evidence,
  and deterministic Python independently controls validation, risk, authorization,
  routing, accounting, and reconciliation. Shared PAPER/LIVE gateways and a controlled
  transition are future-state requirements, not claims about current code or permission
  to trade.
- **Phase 1 CI closure:** GitHub Actions run
  [44764101110](https://github.com/ajayparande/project-aegis/actions/runs/44764101110)
  succeeded for `ci/phase1-foundation` at
  `97712ca3eedcce8f3671737f4e76a55030c04ace`, an isolated Phase 1 backport only.
  It does not validate local `main`, its other unpublished commits, or the dirty
  M2–M5, forward-runtime, and research worktree. See the phase-gate register for the
  exact acceptance boundary.
- **Current authorized work:** Phase 2 baseline reconciliation is documentation-only.
  It does not authorize new Phase 2 implementation or operating activity.
- **Current safety authority:** `RESEARCH_ONLY` remains mandatory. No broker
  connectivity, credentials, live orders, forward-paper operation, or live execution
  is authorized by the future product direction.
- **Next implementation:** A separate phase assignment is required, and the bounded
  CI result does not establish independent acceptance of the broader local baseline.
  The historical Decision Journal proposal below is not the active task or authorization.

## Authority and source hierarchy

1. Safety/process: `CLAUDE.md`, `AGENTS.md`.
2. Approved decisions: `docs/ARCHITECTURE_DECISIONS.md` (only explicitly APPROVED entries are binding).
3. Status: `docs/PROJECT_STATE.md`; historical snapshots do not authorize work.
4. Milestone contracts: `tasks/M2_PORTFOLIO_ACCOUNTING.md`, `tasks/DECISION_JOURNAL_V1.md`, `tasks/PHASE_10_LIVE_DATA_PAPER_MVP.md` and the relevant task file.
5. Research/safety/open decisions: `docs/AI_RESEARCH_PROTOCOL.md`, `docs/DEVELOPMENT_GUARDRAILS.md`, `docs/OPEN_DECISIONS.md`.
6. Architecture and handoff: `docs/ARCHITECTURE_360_REVIEW_2026-11-17.md`, `docs/PROJECT_HANDOFF_2026-11-18.md`, `docs/ML_LLM_AND_EVALUATION_PLAN.md`, `docs/PROJECT_CHARTER.md`, `docs/SYSTEM_ARCHITECTURE.md`.
7. Navigation: `README.md`, `docs/DEVELOPMENT_ROADMAP.md`.
8. Historical evidence: `docs/reviews/` and bounded implementation reports.

When authoritative sources conflict, identify the exact contradiction and stop before a consequential change. Do not invent approvals, acceptance evidence, or architecture decisions. Approved M2 contract details remain governed by the M2 task specification.

## Current milestone state (owner-supplied reports, 18 Nov 2026)

| Milestone | Status |
| --- | --- |
| Checkpoint 1 | Committed locally, `18ddc9da7fa5021b3709f4a0658e03eb0de611f6`. |
| Checkpoint 2 | Independently ACCEPTED for its **two original blockers** and committed locally, `5ec48067b8f8d60b408cccc6f46ec5e5e685a66d` (tree `21a6031a6eff9548aabb4e1d55bcf8cde85485cd`). **Not pushed.** |
| Checkpoint 2 evidence | 53 independent adversarial checks and 51 focused tests passed, zero failures; `git diff --check` passed. Acceptance limited to calendar provenance and Phase 8 API compatibility. |
| M1B | Independently accepted within bounded qualified single-instrument historical execution. |
| M2-A/B/C | Bounded acceptance reported; M2-C independently accepted. Preserve documented M2-B correction caveat. |
| M2-D | Implemented; technical acceptance reported; **independent acceptance pending**. |
| M2-E | Not started; overall M2 **not accepted**. |
| Decision Journal V1 | Specification and Codex implementation prompt prepared; **implementation result not yet supplied**. |
| Live-data paper MVP | PLANNED / PENDING APPROVAL; no demonstrated operational feed pilot. |
| ML | LightGBM selected as **local research candidate**, not trained, accepted, or proven profitable. Logistic regression baseline and deterministic SMA benchmark retained. |
| Claude / LLM | Optional asynchronous research/explanation only; excluded from the critical trading path. |
| Trading | `RESEARCH_ONLY`; **real broker orders prohibited**. |

**Checkpoint 2 defect closures:** (1) historical research services reload administrator-approved calendar evidence, so injected `CONFIRMED` confidence cannot promote approved `CONFIGURED` evidence; missing/malformed/unavailable calendar fails closed; (2) `aegis.ProposalValidationResult` retains original Phase 8 class identity and `valid=True` construction; research class is `aegis.ResearchProposalValidationResult`. Acceptance does not establish operational calendar/source readiness, source legal rights, M2 completion, profitability or broker integration.

**Checkpoint 2 committed files (exactly ten):** `src/aegis/__init__.py`, `src/aegis/ai_agent/models.py`, `src/aegis/ai_agent/tools.py`, `src/aegis/historical_research.py`, `src/aegis/market_data.py`, `src/aegis/proposals.py`, `tests/test_ai_agent.py`, `tests/test_ai_agent_security_regressions.py`, `tests/test_historical_research.py`, `tests/test_proposals.py`.

**Last reported Git state:** `main` at `5ec48067...`, ahead of `origin/main` by 3; clean real index; 11 modified tracked and 25 untracked files preserved. This is historical, not a substitute for `git status`. Remaining changes include M2 source/tests, `tests/test_market_quality.py`, documentation, and handoff materials. Do not silently include old `docs/AI_RESEARCH_PROTOCOL.zip`, `CODEX_DOCS_ONLY_PROMPT.md`, or `README_HANDOFF.md` in a commit.

## Development priorities and boundaries

**1. Decision Journal V1 (next authorized implementation task).** Follow `tasks/DECISION_JOURNAL_V1.md`. Append-only SQLite journal for BUY/SELL/HOLD/BLOCKED/NO_DECISION, immutable decision identity, qualified evidence and version fingerprints, risk-gate results, deterministic reason codes, stage latencies, idempotent restart/replay, secret redaction and bounded payloads. Store future observations/outcomes in **separate** append-only records; no lookahead or fabricated realized P&L. Journal failure must fail closed for **new paper entries**; protective exits retain safety priority. Integrate with existing forward runtime and Phase 8, **not a second execution engine**. An issued implementation prompt does not mean the code exists or passed tests.

**2. Synthetic end-to-end paper pipeline.** Validate completed-candle quality → signal/proposal → deterministic qualification and risk → Phase 8 paper execution → journal → reconciliation/restart. Test missing/stale data, duplicate delivery, failed writes, halts and restart safety with isolated fixtures.

**3. Live-data paper MVP.** Requires separate authorization, permitted market-data entitlements, approved calendar/source quality and operational gates. Initial scope: one instrument, completed one-minute candles, SMA baseline, local simulated fills. Tick-aggregated observed candles are not automatically authoritative historical exchange candles. Measure p50/p95/p99 latency rather than promising a number. **No broker orders.** The scoped MVP cannot waive the full Phase 10 contract.

**4. Historical M2:** independent M2-D review and M2-E valuation/hypothetical liquidation are separate from the initial one-instrument paper pilot. Do not weaken allocation, qualification, provenance, snapshots or valuation contracts.

**5. ML/LLM:** LightGBM offline research and shadow evaluation only after appropriate permitted data, baseline comparison, costs, leakage control, validation/locked holdout, drift and promotion gates. No Claude/LLM dependency for live decisions or execution. Phase 11 deployment/reliability and Phase 12 readiness remain future, default NO-GO without evidence.

## Mandatory working rules

- Always inspect HEAD, `git status --short --branch`, index, and directly relevant modified/untracked files before editing.
- Read the applicable task specification and existing source/tests. Implement the authorized design; do not repeatedly ask for brainstorming or broad audits when decisions are settled.
- Keep changes narrow; avoid unrelated refactors, new simulators, schema managers or alternate execution paths.
- Run focused synthetic tests and relevant Ruff/format/mypy/diff checks; respect task restrictions. Never claim tests passed if not run.
- Use isolated temporary SQLite databases. **Do not access, initialize, migrate, recreate or delete the normal application DB** without exact explicit authorization. Preserve research data.
- `RESEARCH_ONLY`: no live orders, broker credentials, broker connectivity, autonomous trading or unauthorized forward-paper operation.
- AI proposes; deterministic Python validates evidence, eligibility, risk and permitted paper execution. **Phase 8 remains the sole execution/risk boundary.** Preserve proposal binding, one-use authorizations, expiry, persisted quote checks, order-time qualification, halts and reconciliation.
- Do not spend money, add paid services, deploy cloud resources or access secrets without approval.
- Do not stage, commit, push, reset, restore, stash, clean, overwrite or discard pre-existing work without specific authorization. No blanket `git add .`.
- Record precise test evidence, files changed, schema migration, limitations, initial/final status, and bounded acceptance status. Stop at the requested milestone.
- Status words are distinct: PLANNED ≠ IMPLEMENTED ≠ TESTED ≠ INDEPENDENTLY ACCEPTED ≠ OPERATIONALLY READY ≠ EMPIRICALLY VALIDATED ≠ AUTHORIZED.

## Environment and resumption

Windows 11, PowerShell, Python 3.11 venv, SQLite, Streamlit, pytest, Ruff, mypy, Git. Repository: `D:\Code\Python\Aegis\project-aegis`. Follow `AGENTS.md`; do not require Docker/WSL/Kubernetes/PostgreSQL/Node.js.

On resumption, verify current Git state, read `docs/PROJECT_HANDOFF_2026-11-18.md`, `docs/PROJECT_STATE.md`, `docs/OPEN_DECISIONS.md`, and the active task contract. Check whether Decision Journal has since been implemented and tested. Do not mark planned work complete from a prompt alone.
