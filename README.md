# Project Aegis — Task Index

## Start here

This repository contains planning, implementation, review evidence, and completed
features; a task specification or report is not proof that its feature is accepted or
operationally ready. For a history-independent handover, use this order:

1. [`CLAUDE.md`](./CLAUDE.md) and [`AGENTS.md`](./AGENTS.md) — mandatory agent workflow
   and safety boundaries.
2. [`docs/PROJECT_CHARTER.md`](./docs/PROJECT_CHARTER.md) —
   product vision and non-goals.
3. [`docs/ARCHITECTURE_DECISIONS.md`](./docs/ARCHITECTURE_DECISIONS.md) —
   approved decisions and their authority.
4. [`docs/PROJECT_STATE.md`](./docs/PROJECT_STATE.md) — dated implementation,
   acceptance, and readiness snapshot.
5. [`docs/DEVELOPMENT_GUARDRAILS.md`](./docs/DEVELOPMENT_GUARDRAILS.md),
   [`docs/AI_RESEARCH_PROTOCOL.md`](./docs/AI_RESEARCH_PROTOCOL.md), and
   [`docs/OPEN_DECISIONS.md`](./docs/OPEN_DECISIONS.md).
6. The milestone specification under `tasks/` for the requested work.

The roadmap is strategic planning, not implementation authorization. Historical reports
are evidence bounded to their review date and scope. If sources conflict, stop and ask;
do not choose a convenient interpretation.

For the next-session proposal bundle, read the
[2026-11-18 handoff](./docs/PROJECT_HANDOFF_2026-11-18.md),
[architecture review](./docs/ARCHITECTURE_360_REVIEW_2026-11-17.md),
[ML/optional LLM plan](./docs/ML_LLM_AND_EVALUATION_PLAN.md),
[Decision Journal v1](./tasks/DECISION_JOURNAL_V1.md), and
[live-data paper MVP](./tasks/PHASE_10_LIVE_DATA_PAPER_MVP.md).
The journal and MVP are PLANNED / PENDING APPROVAL, not implemented or authorized.
LightGBM is the selected research candidate, not a validated trading model; Claude is
optional and excluded from the critical execution path.

## AI-1B status update

**AI-1B** is conditionally accepted only as a bounded historical-research tools
milestone after source review. Its read-only, source-use-aware tools are distinct from
**M1B**, which is recorded as independently accepted for its bounded qualified
one-instrument interface according to project-owner-supplied history; no dated
independent acceptance artifact is identified in the repository. Neither acceptance
establishes live-trading readiness, corporate-action validity, profitability, or complete
cost modeling.

Separate validation remains required for confirmed calendar coverage, source permission,
AI-1C run-level audit, and any future paper or live readiness work.

This folder contains milestone-specific implementation instructions and acceptance criteria. The roadmap and architecture documents elsewhere in the repository remain the broader product-level references.

## M2 portfolio accounting

The approved M2 v1 design is in
[`tasks/M2_PORTFOLIO_ACCOUNTING.md`](./tasks/M2_PORTFOLIO_ACCOUNTING.md). M2-A is
recorded as accepted according to project-owner-supplied history; no dated independent
acceptance artifact is identified in the repository. M2-B has owner-reported independent ACCEPT on 2026-11-16 for qualification-only
scope; the original audit artifact is not yet in the repository. Its schema-provenance
defect is corrected and requires focused independent review, not automatic reacceptance.
M2-C is independently accepted for qualified historical per-sleeve M1B composition.
M2-D is implemented and technically ACCEPTED; distinct-reviewer independent sign-off
remains pending. These M2-C/M2-D decisions are recorded from project-owner-supplied
acceptance history; see the evidence limitations in `docs/PROJECT_STATE.md`.
M2-E has not started and is not authorized. Overall M2 is not accepted; it remains
historical research/backtesting only. M2 does not use Phase 8.

## Strategic direction and current gate

The strategic product objective is an AI trading-research brain whose proposals are
measured against deterministic and validated ML baselines. AI researches and proposes;
Python independently validates, and Phase 8 controls simulated execution and risk.
`RESEARCH_ONLY` remains active and no live trading is authorized. This is the long-term
direction, not a claim that AI-1C or empirical performance validation is implemented.

Checkpoint 1 is committed locally. Checkpoint 2's two blockers are reported corrected,
with 305 focused tests reported passing; independent acceptance remains pending and
Checkpoint 2 is not committed. See the checkpoint record in
[`docs/PROJECT_STATE.md`](./docs/PROJECT_STATE.md).

The current M2 gate is distinct-reviewer independent sign-off for the implemented,
technically ACCEPTED M2-D accounting, alongside the unresolved focused review of the
M2-B provenance correction. M2-C has independent bounded ACCEPT; M2-E has not started
and is not authorized. See
[`docs/PROJECT_STATE.md`](./docs/PROJECT_STATE.md).

The strategic sequence below is not an authorization or a substitute for milestone
acceptance:

1. AI Trading-Research Brain (bounded research tools and evidence-based BUY/SELL/HOLD proposals)
2. Historical Strategy Competition (AI vs SMA vs ML vs random vs buy-and-hold under identical conditions)
3. Operational Data Infrastructure (calendar and provenance work in parallel)
4. Forward Paper Trading (planned; prerequisite-gated, not currently authorized)
5. Live Readiness Assessment (future NO-GO-by-default assessment; not live authorization)

## Intended execution order

This is the historical phase index, not a claim that phases are complete or the current
implementation queue. See [`docs/PROJECT_STATE.md`](./docs/PROJECT_STATE.md) for status.

| Order | Task file | Purpose |
|---:|---|---|
| 1 | `PHASE_01_FOUNDATION.md` | Local project foundation — completed |
| 2 | `PHASE_02_MARKET_DATA.md` | Instrument Master, Historical OHLCV Ingestion, Exchange Calendar and Market Data Quality |
| 3 | `PHASE_03_STRATEGY_LAB.md` | Strategy Lab |
| 4 | `PHASE_04_BACKTESTING.md` | Backtesting Engine |
| 5 | `PHASE_05_CANDLESTICK_PRICE_ACTION.md` | Candlestick Patterns and Price Action — accepted with documented research and operational limitations |
| 6 | `PHASE_06_TECHNICAL_INDICATORS.md` | Technical Indicators — accepted with documented limitations |
| 7 | `PHASE_07_ML_RESEARCH.md` | Optional, evidence-gated ML research |
| 8 | `PHASE_08_PAPER_TRADING.md` | Deterministic simulated broker and portfolio ledger |
| 9 | `PHASE_09_AI_AGENT_AND_TOOL_FRAMEWORK.md` | Provider-independent LLM integration and controlled tool calling |
| 10 | `PHASE_10_LIVE_DATA_FORWARD_PAPER_TRADING.md` | 30–60 trading days of live-data forward paper trading |
| 11 | `PHASE_11_CLOUD_DEPLOYMENT_AND_RELIABILITY.md` | VPS deployment, monitoring, backups and recovery testing |
| 12 | `PHASE_12_LIVE_READINESS.md` | Final independent go/no-go assessment; no automatic activation |

## Research vs operational gating

**`RESEARCH_ONLY` is the only currently available application mode.** The local paper
ledger is an explicit-input simulation API with a read-only dashboard report; it is not
an enabled application mode or an automated strategy runner. The application does not
connect to a broker and has no live order-placement path.

- `PAPER_TRADING` mode is not implemented or available. Phase 8's simulation foundation
does not change the configured application mode or authorize forward operation.
- `LIVE_TRADING` is not implemented and remains unavailable. Phase 12 is an assessment,
  not authorization or activation; any implementation would require a separate, explicit
  approval.
- Phase 10 live-data forward paper remains planned and prerequisite-gated. Synthetic
  forward-runtime work does not constitute operational forward-paper readiness or
  authorization.
- Offline AI research must not wait for full calendar automation if permitted historical data already exists.
- Calendar automation, source provenance and data quality remain parallel operational workstreams and must be tracked separately from research readiness.

No AI agent, automated workflow, deployment, or configuration update may switch the
application to paper or live execution.

## Cross-cutting rules

- Python services are authoritative for calculations, portfolio accounting, risk checks and execution controls.
- LLMs provide bounded reasoning, research assistance and explanations; they cannot bypass deterministic controls.
- Keep LLM providers interchangeable and external model inference optional.
- Do not assume market data, APIs, hosting, or other services are free or permitted; verify terms and costs first.
- Keep local Windows development supported and design for later cloud deployment without requiring cloud infrastructure now.
- Do not introduce unnecessary GPUs, Kubernetes, distributed services, or paid dependencies without a demonstrated need and explicit approval.
- Preserve reproducibility, provenance, auditability, strategy versioning and fail-closed behaviour.
- Never claim profitability based only on backtesting or a limited paper-trading sample.
- A task marked incomplete must not be represented as implemented. Update checkboxes only after implementation and verification.
- Do not commit or push unless explicitly authorised.
