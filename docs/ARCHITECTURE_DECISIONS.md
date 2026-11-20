# Project Aegis — Architecture Decision Register

This register records decisions and their authority, not every implementation idea.
Dates are given only where a repository record provides them. “Date not recorded”
means the approval date is unknown; the documentation date is not substituted for it.
Open choices belong in [`OPEN_DECISIONS.md`](./OPEN_DECISIONS.md), not in the approved
decision list.

## Decision statuses

- **APPROVED** — explicitly approved by the project owner or approved milestone
  specification; binding within the recorded scope.
- **ACTIVE SAFETY CONSTRAINT** — a current mandatory project rule.
- **DEFERRED** — explicitly out of current scope; not an approved future design.
- **OPEN** — requires an explicit decision; no option is selected.

## Approved decisions and active constraints

### AEGIS-ADR-001 — Researcher-first AI product

- **Status:** APPROVED project direction; approval date not recorded.
- **Decision:** Build an evidence-driven AI research brain that can discover and
  investigate eligible instruments using bounded tools and form testable
  `BUY` / `SELL` / `HOLD` proposals. The product is not merely a fixed collection of
  indicators. HOLD and rejection for insufficient evidence are valid outcomes.
- **Rationale:** Measure research quality and robustness rather than presume a strategy
  works.
- **Consequences:** AI research is distinct from deterministic validation and execution.
  The project charter defines the target; current implementation is tracked separately
  in [`PROJECT_STATE.md`](./PROJECT_STATE.md).
- **Source:** Project direction supplied for the documentation handover; related
  requirements are in [`DEVELOPMENT_ROADMAP.md`](./DEVELOPMENT_ROADMAP.md) and
  [`tasks/PHASE_09_AI_AGENT_AND_TOOL_FRAMEWORK.md`](../tasks/PHASE_09_AI_AGENT_AND_TOOL_FRAMEWORK.md).
- **Supersession:** None recorded.

### AEGIS-ADR-002 — Python remains authoritative; AI cannot execute or bypass controls

- **Status:** ACTIVE SAFETY CONSTRAINT.
- **Decision:** AI output is untrusted. Deterministic Python independently validates
  evidence, eligibility, temporal/data-quality constraints, financial calculations,
  risk, sizing, and any permitted execution. AI cannot authorize or execute trades or
  weaken/bypass Python controls.
- **Rationale:** Separate probabilistic research from deterministic financial and safety
  controls.
- **Consequences:** AI proposals must pass independent gates; no prompt or model response
  can grant permissions.
- **Source:** [`AGENTS.md`](../AGENTS.md),
  [`tasks/PHASE_09_AI_AGENT_AND_TOOL_FRAMEWORK.md`](../tasks/PHASE_09_AI_AGENT_AND_TOOL_FRAMEWORK.md),
  and project direction supplied for this handover.
- **Supersession:** None recorded.

### AEGIS-ADR-003 — RESEARCH_ONLY and Phase 8 execution/risk boundary

- **Status:** ACTIVE SAFETY CONSTRAINT.
- **Decision:** Keep the application in `RESEARCH_ONLY`. Phase 8 is the sole execution
  and risk boundary. No live trading is authorized; no broker or forward-paper operation
  may be added or enabled without explicit, separately scoped approval.
- **Rationale:** Simulation infrastructure is not permission to operate.
- **Consequences:** Phase 8 implementation does not itself switch application mode.
  Phase 10 is planned and prerequisite-gated; Phase 11 and Phase 12 are future scope.
  Phase 12 defaults to NO-GO and cannot automatically activate trading.
- **Source:** `src/aegis/config.py`, `tasks/PHASE_08_PAPER_TRADING.md`,
  `tasks/PHASE_10_LIVE_DATA_FORWARD_PAPER_TRADING.md`,
  `tasks/PHASE_12_LIVE_READINESS.md`, and project direction supplied for this handover.
- **Supersession:** None recorded.

### AEGIS-ADR-004 — Preserve the local research database and migration history

- **Status:** ACTIVE SAFETY CONSTRAINT.
- **Decision:** Normal application databases must never be deleted or recreated as part
  of routine development or testing; tests use isolated disposable databases. Access,
  initialization, migration, and modification of the normal database require explicit
  approval for the exact operation. Destructive operations are never inferred from
  general approval. An exceptional destructive operation requires separate explicit
  approval identifying the exact database and operation. Preserve existing records and
  migration history.
- **Rationale:** Existing research data is user data; a code-defined schema version is
  not proof of an on-disk database's state.
- **Consequences:** Use isolated temporary databases for authorized tests. Do not infer
  current local data contents from historical reports.
- **Source:** `AGENTS.md`, the Phase 9 review's record of an unexpected normal-database
  migration, and project direction supplied for this handover.
- **Supersession:** None recorded.

### AEGIS-ADR-005 — Calendar and source-use evidence are distinct trust boundaries

- **Status:** APPROVED project constraint; approval date not recorded.
- **Decision:** A calendar marked `CONFIGURED` is not `CONFIRMED`; incomplete coverage
  cannot establish confirmed completeness. Historical imports default to
  `UNVERIFIED`; research/backtests require the applicable explicit
  `VERIFIED_PERMITTED` declaration. That declaration records a user assertion and does
  not establish legal rights.
- **Rationale:** Evidence availability, quality, calendar confidence, permission
  declaration, and legal authorization are separate facts.
- **Consequences:** Fail closed when required evidence is absent or unconfirmed.
  Synthetic fixtures establish code behavior only, never operational readiness or
  real-source permission.
- **Source:** `docs/EXCHANGE_CALENDAR_CONFIGURATION.md`,
  `tasks/PHASE_02_MARKET_DATA.md`, `tasks/PHASE_04_BACKTESTING.md`, and the approved
  M2 specification.
- **Supersession:** None recorded.

### AEGIS-ADR-006 — M1B is a bounded one-instrument interface

- **Status:** Project-owner-supplied history records independent acceptance for the
  bounded scope; no dated independent acceptance artifact is identified in the
  repository.
- **Decision:** M1B's accepted scope is qualified historical execution for one
  instrument through the existing M1B path. It is not multi-instrument portfolio
  accounting, operational data readiness, profitability evidence, Phase 8 execution,
  or live readiness.
- **Rationale:** Preserve the reviewed qualification, fill, cost, and tick-rounding
  boundary as the only supported one-instrument simulator.
- **Consequences:** M2 must compose/reuse M1B; it must not create a second simulator or
  bypass qualification. Do not confuse **M1B** with the separate **AI-1B** historical
  research-tools milestone.
- **Source:** Project-owner status supplied for this handover and the M1B interface
  description in `tasks/PHASE_04_BACKTESTING.md`.
- **Supersession:** None recorded.

### AEGIS-ADR-007 — Approved M2 v1 portfolio contract

- **Status:** APPROVED M2 v1 design; approval date not recorded.
- **Decision:** The complete controlling contract is
  [`tasks/M2_PORTFOLIO_ACCOUNTING.md`](../tasks/M2_PORTFOLIO_ACCOUNTING.md). Its
  approved constraints include:
  - Equal fixed cash sleeves across a frozen, ordered universe; allocate in integer
    paise and give remainder paise to earliest members in frozen order.
  - Idle cash remains in its sleeve; no borrowing, transfers, or reallocation.
  - Active NSE cash EQUITY only; daily candles only; shared cost and volume-participation
    assumptions; fixed quantity per instrument that must be lot-valid after trusted
    resolution.
  - One common `as_of`, one approved calendar instance, and one read-only SQLite
    snapshot for all sleeve qualification. Do not replace this with repeated public
    `execute_one_instrument()` calls.
  - Missing prices on expected sessions block the portfolio. Only calendar-confirmed
    non-trading days may carry the last eligible mark, with an explicit stale flag.
  - Reuse M1B's qualification, execution, fill, fee, slippage, and tick-rounding logic.
    No second simulator; no scale-ins or partial fills in M2 v1.
  - Terminal liquidation is hypothetical, separate, and never mutates actual fills,
    cash, positions, or equity.
  - No new portfolio persistence schema in v1; return immutable results. M2 is
    historical research/backtesting only and never uses Phase 8.
- **Rationale:** Fixed, independently reconciled sleeves compose reviewed single-
  instrument behavior while preserving deterministic accounting and trust boundaries.
- **Consequences:** M2-A through M2-G remain staged gates. M2-A acceptance does not
  accept M2-B or later stages. M2-A is recorded as accepted according to
  project-owner-supplied history; no dated independent acceptance artifact is
  identified in the repository. See the status in
  [`PROJECT_STATE.md`](./PROJECT_STATE.md).
- **Source:** Approved `tasks/M2_PORTFOLIO_ACCOUNTING.md` and project-owner status
  supplied for this handover.
- **Supersession:** None recorded. Any change requires explicit approval and an update
  to the controlling M2 specification.

### AEGIS-ADR-008 — Local, typed, exact-decimal research foundation

- **Status:** ACTIVE ARCHITECTURE CONSTRAINT.
- **Decision:** Preserve the supported local Windows/PowerShell, Python 3.11, SQLite,
  and Streamlit development environment. Use the existing package architecture and
  exact decimal text for new historical OHLC prices; do not use SQLite `REAL` for new
  financial prices.
- **Rationale:** Reproducibility, financial precision, and protection of legacy data.
- **Consequences:** Do not add Docker, WSL, Kubernetes, PostgreSQL, Node.js, or
  unnecessary dependencies to satisfy a task.
- **Source:** `AGENTS.md`, `pyproject.toml`, `src/aegis/database.py`, and
  `docs/SYSTEM_ARCHITECTURE.md`.
- **Supersession:** None recorded.

### AEGIS-ADR-009 — Future empirical claims require a pre-registered research protocol

- **Status:** APPROVED requirement before AI-1C acceptance; evaluation values remain
  open.
- **Decision:** Separate development, validation, and locked holdout data; prevent
  temporal leakage and survivorship bias; freeze model/prompt/tool/parameter/evaluation
  versions before final holdout use; compare against defined baselines under common,
  disclosed costs; use robustness and statistical evaluation.
- **Rationale:** Avoid post-hoc tuning and unsubstantiated performance claims.
- **Consequences:** The protocol and open numerical thresholds are recorded separately
  in [`AI_RESEARCH_PROTOCOL.md`](./AI_RESEARCH_PROTOCOL.md) and
  [`OPEN_DECISIONS.md`](./OPEN_DECISIONS.md). No threshold is approved by this entry.
- **Source:** `docs/DEVELOPMENT_ROADMAP.md`, `docs/VALIDATION_AND_ACCEPTANCE.md`, and
  project direction supplied for this handover.
- **Supersession:** None recorded.

### AEGIS-ADR-010 — AI-first future product and phase-gate authority

- **Status:** APPROVED future product requirement; owner/Project Manager direction
  supplied 2026-11-20. This does not authorize operation or claim implementation.
- **Decision:** The finished product is AI/LLM-led: the agent originates
  evidence-backed `BUY` / `SELL` / `HOLD` / `NO_DECISION` proposals; technical
  analysis, deterministic strategies, and lightweight ML support evidence and research;
  deterministic Python remains authoritative for data validation, sizing, financial
  risk, authorization, routing, accounting, and reconciliation. A shared application
  must support PAPER and LIVE gateways only after their separate acceptance gates and
  deliberate authorization.
- **Rationale:** Make the future product's decision-making role explicit while
  preserving independent deterministic financial and safety controls.
- **Consequences:** This is a future-state target, not a description of existing code.
  `RESEARCH_ONLY` remains the current mode; no broker connection, credentials, live
  orders, or live execution are authorized. Phase 1 Foundation closure is the only
  active development gate. The next phase requires independent Phase 1 acceptance and
  a separate Project Manager assignment.
- **Source:** Project-owner and Project Manager direction supplied 2026-11-20;
  implementation status and gate evidence remain in
  [`PROJECT_STATE.md`](./PROJECT_STATE.md) and
  [`AEGIS_PHASE_GATE_REGISTER.md`](./AEGIS_PHASE_GATE_REGISTER.md).
- **Supersession:** Resolves earlier future-product framing only; it does not supersede
  active safety constraints, milestone contracts, or historical acceptance evidence.

## Deferred and open matters

Deferred capabilities and unresolved choices are not approvals. See
[`OPEN_DECISIONS.md`](./OPEN_DECISIONS.md) for calendar readiness, source rights,
research acceptance thresholds, M2 review gates, forward-paper prerequisites, cloud
deployment, and live readiness.
