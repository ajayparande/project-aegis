# Project Aegis — Task Index

This folder contains milestone-specific implementation instructions and acceptance criteria. The roadmap and architecture documents elsewhere in the repository remain the broader product-level references.

## Intended execution order

| Order | Task file | Purpose |
|---:|---|---|
| 1 | `PHASE_01_FOUNDATION.md` | Local project foundation — completed |
| 2 | `PHASE_02_MARKET_DATA.md` | Instrument Master, historical ingestion, exchange sessions and data quality |
| 3 | `PHASE_03_STRATEGY_LAB.md` | Versioned strategy research and deterministic signals |
| 4 | `PHASE_04_BACKTESTING.md` | Chronological simulation and performance measurement |
| 5 | `PHASE_05_ML_RESEARCH.md` | Optional, evidence-gated ML research |
| 6 | `PHASE_06_PAPER_TRADING.md` | Deterministic simulated broker and portfolio ledger |
| 7 | `PHASE_07_AI_AGENT_AND_TOOL_FRAMEWORK.md` | Provider-independent LLM integration and controlled tool calling |
| 8 | `PHASE_08_LIVE_DATA_FORWARD_PAPER_TRADING.md` | 30–60 trading days of live-data forward paper trading |
| 9 | `PHASE_09_CLOUD_DEPLOYMENT_AND_RELIABILITY.md` | VPS deployment, monitoring, backups and recovery testing |
| 10 | `PHASE_10_LIVE_READINESS.md` | Final independent go/no-go assessment; no automatic activation |

## Execution and safety modes

**`RESEARCH_ONLY` is the only currently available application mode.** The application
does not connect to a broker and has no live order-placement path.

- `PAPER_TRADING` is planned for a later phase and is not implemented or available.
- `LIVE_TRADING` is not implemented and remains unavailable. Phase 10 is an assessment,
  not authorization or activation; any implementation would require a separate, explicit
  approval.

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
