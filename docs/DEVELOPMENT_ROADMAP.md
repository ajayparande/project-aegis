# Development Roadmap

This roadmap records the long-term objective of an AI trading-research brain that
investigates market evidence, proposes BUY / SELL / HOLD research outcomes, and is
evaluated against deterministic and ML baselines under a shared research pipeline. AI
researches and proposes; deterministic Python independently validates evidence,
eligibility, calculations, and risk. Phase 8 alone controls simulated execution and risk.
The application remains `RESEARCH_ONLY`; no live trading authorization exists. The
original infrastructure-first phase numbers remain for historical continuity. This is
strategic direction, not a declaration that these capabilities are implemented or
authorized.

For the near-term work gate, M2-B shared batch qualification received an independent
read-only ACCEPT on 2026-11-16 for its bounded scope, according to owner-reported
history; the original artifact is absent. The subsequent schema-provenance correction
requires focused independent review, not automatic reacceptance. M2-C is independently
accepted for qualified historical per-sleeve M1B composition. M2-D accounting is
implemented and technically ACCEPTED; distinct-reviewer independent sign-off remains
pending. These decisions are recorded from project-owner-supplied acceptance history.
M2-E has not started and is not authorized; overall M2 is not accepted. See
[`PROJECT_STATE.md`](./PROJECT_STATE.md) for the dated status snapshot and
[`ARCHITECTURE_DECISIONS.md`](./ARCHITECTURE_DECISIONS.md) for approved constraints.

The strategic priority order below is not the active implementation queue.

## Next-session handoff proposals

See the [2026-11-18 handoff](./PROJECT_HANDOFF_2026-11-18.md),
[architecture review](./ARCHITECTURE_360_REVIEW_2026-11-17.md),
[ML/optional LLM plan](./ML_LLM_AND_EVALUATION_PLAN.md),
[Decision Journal v1](../tasks/DECISION_JOURNAL_V1.md), and
[live-data paper MVP](../tasks/PHASE_10_LIVE_DATA_PAPER_MVP.md).
Checkpoint 1 is committed; Checkpoint 2 blockers are reported fixed with 305 focused
tests reported passing, but independent acceptance is pending and it is not committed.
The journal and MVP are PLANNED / PENDING APPROVAL, not implemented. LightGBM is the
selected research candidate, not a validated trading model. Claude remains optional
and outside the critical execution path. The proposed SMA-first MVP is a bounded
exercise, not completion of the existing Phase 10 task or a replacement for its gates.
No new mode, source access, training, schema migration or operational work is authorized.

## Strategic priority order

1. Priority 1 — AI Trading-Research Brain
   - AI investigates available, permitted historical market data using bounded tools.
   - AI discovers and evaluates candidate instruments subject to Python eligibility checks.
   - AI proposes BUY, SELL or HOLD research outcomes using traceable evidence; it does not
     authorize or execute trades.
   - AI can request bounded tools rather than rely only on handpicked inputs.
   - Research proposals and learning experiments are evaluated through controlled,
     reproducible iterations; AI does not autonomously change protected controls.
   - Python independently validates evidence, risk, and position sizing.
   - Phase 8 alone controls simulated execution and risk; no live trading is authorized.

2. Priority 2 — Historical Research and Strategy Competition
   - Build the evaluation harness for the AI proposer.
   - Compare AI against SMA, ML where validated, random baseline and buy-and-hold.
   - Use identical historical periods, costs and constraints.
   - Prevent look-ahead bias, survivorship bias and training/test leakage.
   - Record BUY, SELL and HOLD decisions, evidence, rejected proposals and subsequent outcomes.
   - Measure returns, drawdown, win rate, trade frequency and risk-adjusted performance.

3. Priority 3 — Operational Data Infrastructure (parallel workstream)
   - Automatic NSE calendar discovery and updates.
   - Market-data ingestion and provenance verification.
   - Data quality and calendar confirmation.
   - Calendar automation may proceed in parallel with offline research, but this does not
     waive source-use permission, data-quality, or evidence-verification gates. Research
     may use only historical data already verified as permitted for that use.

4. Priority 4 — Forward Paper Trading (planned; prerequisite-gated, not currently authorized)
   - Any future proposal to route validated research into paper simulation requires
     separate authorization and must pass Python evidence, data-quality, and risk gates
     through Phase 8. This priority does not authorize operation.
   - Build a 30–60 actual trading-day evidence period when operational prerequisites are met.

5. Priority 5 — Live Readiness Assessment
   - Assess evidence, reliability, risk controls and operational readiness.
   - Do not implement or enable live trading without separate explicit approval.

## AI-1B source review (15 Nov 2026)

Decision: CONDITIONALLY ACCEPTED as a bounded historical-research access milestone.

The implementation was inspected in the actual repository and verified against the current
source files and tests, including `src/aegis/historical_research.py`,
`src/aegis/market_data.py`, `src/aegis/market_quality.py`, `src/aegis/ai_agent/tools.py`,
`src/aegis/proposals.py`, `src/aegis/database.py`, and the relevant historical research,
market-quality, and AI-agent unit tests.

Verified evidence:
- Each historical retrieval response uses one read-only SQLite connection with a single
  explicit transaction for the page query, quality analysis, and cursor validation.
- Fingerprinting covers the material candle rows and provenance fields that can affect
  continuity across pages, with deterministic JSON ordering and an explicit 100,000-row
  fail-closed bound when the result set exceeds the verification window.
- Cursor invalidation correctly rejects deleted or changed candle rows, mismatched filters,
  and altered provenance.
- Source-use eligibility is kept separate from data completeness, and a dataset can be
  complete without being eligible for research use.
- The relevant regression tests pass in the project virtual environment.

Remaining limits and non-acceptance statements:
- This is still a bounded change-detection fingerprint, not a cryptographic evidence
  attestation or immutable database snapshot.
- The design is not a substitute for a validated corporate-action or cost model, and no
  live trading or broker authorization is implied.
- The checked-in NSE cash calendar candidate covers 2025-2026 and remains `CONFIGURED`,
  not operationally `CONFIRMED`. Coverage and evidence limitations remain, including
  uncovered 2027/other segments and the instrument-specific 2026 CAS schedule.
  `RESEARCH_ONLY` remains mandatory. See
  [`calendar evidence`](./EXCHANGE_CALENDAR_NSE_CASH_2025_2026_EVIDENCE.md).
- AI-1C, run-level audit, full forward-paper gating, and live-readiness remain future
  work. The project must not treat any bounded historical access layer as autonomous
  execution authority.

This milestone is conditionally accepted as an evidence-aware, read-only research
foundation for its bounded scope only. It is not M1B, does not complete Phase 9 or AI-1C,
and does not accept downstream autonomy, validation, or operational phases.

## Roadmap status corrections before AI-1C

The following repository-grounded status corrections are mandatory before any AI-1C work is
planned as a validated milestone:

- AI-1B is conditionally accepted only within its bounded historical-research scope. It is
  not a full operational data-readiness milestone and does not authorize live trading.
- Phase 2 operational data readiness remains incomplete until confirmed calendar coverage,
  permission declarations, and source-use verification are independently accepted.
- Phase 7 ML research remains not accepted for empirical performance claims. The repository
  evidence explicitly notes that Phase 7 acceptance remains pending until permitted,
  quality-gated data and untouched out-of-sample evaluation are demonstrated.
- Phase 8 remains the sole execution and risk boundary for any paper or forward simulation
  path; it does not create a live-trading mode or an autonomous trading brain.
- Phase 10 remains planned, not accepted.
- Live trading remains blocked and outside scope; no implementation may treat a future paper
  operation as proof of live readiness.

## Research protocol before AI-1C

The research protocol must be documented and frozen before AI-1C begins. It is a governance
requirement, not a marketing statement.

### Data zones and contamination controls

- Development data: for exploratory analysis, tool selection, feature generation and model
  iteration. This data may be used to test hypotheses but must not be treated as final proof.
- Validation data: for controlled model and strategy selection. It is used to compare
  candidate designs and reject weak alternatives.
- Locked holdout data: final evaluation only. It must not be used for repeated tuning,
  prompt iteration, parameter selection or tool reconfiguration after the protocol is frozen.
- Historical contamination controls must be documented before any result is described as
  out-of-sample. The project must record model training cutoffs when known, mask tickers and
  dates where practical, and treat any period inside the model's training window as lower
  confidence evidence until independent post-cutoff validation is produced.
- Prompt, model, tool version, parameter set, evaluation rule set and data window must be
  frozen before the locked holdout is examined.
- Holdout access must be logged with run ID, timestamp, operator, purpose, and scope. Access to
  the holdout must be limited to the final evaluation and must not be used for iterative tuning.
- Historical research must exclude look-ahead, leakage, and survivorship-bias assumptions.
  All feature creation, validation splits, and trade evaluation must be point-in-time and
  calendar-aware.

### Pre-registered evaluation protocol

Before any strategy is tested as a candidate for AI-1C, the project must define a pre-registered
protocol covering:

- A documented set of instruments and eligible data windows.
- A chosen primary performance metric and benchmark design.
- Baseline comparisons against the existing SMA strategy, a matched random strategy, and
  buy-and-hold / Nifty TRI where data permits.
- Fully consistent transaction costs, slippage and liquidity assumptions; these are not to be
  tuned after the fact.
- Walk-forward evaluation with explicit temporal splits.
- Select and justify appropriate statistical methods before evaluation; exact methods,
  confidence levels, and multiple-testing procedures remain open and require approval in
  a pre-registered protocol.
- Consider the proposed 1.5x–2x cost/slippage stress range as a robustness-test example;
  it is not an approved requirement or universal acceptance threshold. Any selected
  multiplier and pass/fail rule require explicit approval and pre-registration. Robustness
  analysis should also examine removal or influence of top-performing trades.
- Minimum sample requirements and stopping rules explicitly approved and pre-registered
  before results are examined; the values remain open.
- A requirement to freeze model, prompt, tool version and runtime budget before the locked
  holdout is used for final evaluation.

Numerical thresholds, statistical methods, stress multipliers, and pass/fail criteria remain
undecided pending explicit approval and evidence from the actual data and evaluation design.
The 1.5x–2x range above is a proposal only. Do not invent or treat it as approved.

### Baselines and performance evaluation

The baseline set for AI-1C comparisons must include:

- Existing SMA strategy.
- Matched random strategy.
- Buy-and-hold benchmark, and Nifty TRI where a suitable data series and governance state are
  available.
- Realistic Indian trading costs and slippage assumptions derived from the project's Phase 4
  and Phase 8 cost assumptions, with explicit documentation of how they are applied.
- Walk-forward evaluation using time-ordered splits and consistent exclusions.
- Select and explicitly approve statistical methods, confidence levels, and multiple-
  testing procedures in the pre-registered protocol before examining results; exact
  selections remain open.
- Robustness checks for non-trivial cost increases and removal of the best-performing trades.
  Assess dependence on a small number of trades or a narrow regime; any sample-size or
  pass/fail threshold remains open and must be approved before evaluation.

The project must not claim empirical edge from raw backtest return alone.

## AI-1C prerequisites and operating model

AI-1C, when built, must be a bounded researcher-first workflow. It must not execute orders or
modify the database. It must remain downstream of Python validation and Phase 8 execution
controls.

Required prerequisites:

- Discover eligible instruments from actual historical data and source-use status.
- Screen and investigate candidate opportunities using bounded tools and explicit budgets.
- Form structured, testable hypotheses with expected observable behaviour and invalidation
  conditions.
- Validate hypothesis logic deterministically in Python before any proposal is emitted.
- Record model/provider identity, prompt/template version, tool names and parameter sets,
  arguments, results, run IDs and evidence references.
- Re-execute evidence where deterministic and possible.
- Generate proposals only after validation and evidence checks.
- Keep the AI layer outside the execution and risk boundary; Phase 8 remains authoritative.

### AI-1C safety and reproducibility criteria; performance thresholds remain open

These roadmap-listed criteria describe safety and reproducibility expectations; they do
not approve statistical methods or performance thresholds. AI-1C must demonstrate the
applicable approved criteria before it is treated as a validated research workflow:

- provider failure and budget exhaustion are handled safely and reported as failed runs;
- invalid or unverifiable evidence is rejected;
- prompt injection and unauthorized tool use are blocked by Python validation and permission
  gates;
- placebo/shuffled data, perturbation, and ablation tests produce HOLD or rejection rather than
  unearned confidence;
- replay determinism is verifiable from recorded model responses and tool outputs;
- HOLD and rejection behaviours are explicit and do not silently imply a BUY signal;
- the tool and proposal workflow remains bounded, auditable, and non-executable.

## Legacy phase status map

Legacy phase numbering remains useful for continuity, but the strategic execution order is
no longer hardware/data-first. The project must clearly distinguish between implementation
quality, research validation status, operational prerequisites and live-readiness status.
This map does not mean that all phases 1–10 are complete.

### Portfolio milestone checkpoint

- **M1B:** recorded as independently accepted for bounded qualified one-instrument execution,
  according to project-owner-supplied history; no dated independent acceptance artifact is
  identified in the repository. This does not accept full Phase 4 or operational readiness.
- **M2-A:** immutable request contracts and deterministic paise allocation are recorded as
  accepted according to project-owner-supplied history; no dated independent acceptance
  artifact is identified in the repository.
- **M2-B:** shared qualification coordinator received an independent read-only
  **ACCEPT on 2026-11-16** for qualification-only scope. The review record has not yet
  been added to the repository; this is owner-reported history. The subsequent
  schema-provenance correction requires focused independent review.
- **M2-C:** independently accepted for qualified historical per-sleeve M1B composition,
  according to project-owner-supplied acceptance history. Earlier implementation and
  remediation test reports remain historical evidence.
- **M2-D:** accounting is implemented and technically ACCEPTED, according to
  project-owner-supplied history; distinct-reviewer independent sign-off remains pending.
  Technical ACCEPT is not independent acceptance.
- **M2-E:** not started and not authorized. See `PROJECT_STATE.md` for acceptance
  evidence limitations; the correction date is not an inferred acceptance date.
- **M2:** the v1 design is approved, but M2 as a whole is not accepted. It is historical
  research/backtesting only and does not use Phase 8.


| Legacy phase | Milestone | Current status | Strategic interpretation |
|---:|---|---|---|
| 1 | Foundation | **Complete.** Repository and local app foundation remain in place; `RESEARCH_ONLY` mode remains active. | Historical foundation only. |
| 2 | Instrument Master, Historical OHLCV Ingestion, Exchange Calendar and Market Data Quality | **Implemented; operational prerequisites remain incomplete.** Source-use verification and calendar confirmation are still outstanding operational tasks. | Parallel infrastructure workstream. |
| 3 | Strategy Lab | **Deterministic core implemented.** AI assistance remains deferred and not required for the initial research harness. | Supporting research foundation. |
| 4 | Backtesting Engine | **Engineering complete; acceptance pending operational evidence.** Synthetic correctness is established, but real-world approved data and verified calendar coverage remain required. | Useful benchmark engine for research and controlled evaluation. |
| 5 | Candlestick Patterns and Price Action | **Accepted as a bounded research library with documented limitations.** | Research utilities, not production trading evidence. |
| 6 | Technical Indicators | **Accepted as a bounded research library with documented limitations.** | Research utilities, not production trading evidence. |
| 7 | ML Research | **Research harness implemented; evidence not yet accepted.** | Candidate comparator for the AI research programme. |
| 8 | Paper Trading | **Simulation foundation implemented; application remains RESEARCH_ONLY.** Phase 8 is the execution and risk boundary only. | Execution boundary only; not an autonomous trading mode. |
| 9 | AI Agent and Tool Framework | **Core framework and AI-1B historical research tools implemented.** AI-1C autonomous research workflow remains deferred; no tool authorizes execution. | Bounded data-access foundation for the AI research programme. |
| 10 | Live Data Forward Paper Trading | **Planned; prerequisite-gated.** | Requires prior data, paper-trading and AI validation. |
| 11 | Cloud Deployment and Reliability | **Planned; prerequisite-gated.** | Operational staging only. |
| 12 | Live Readiness | **Planned and blocked on independent evidence and explicit approval; default decision is NO-GO.** | Assessment gate, not a launch trigger. |

## Required common Proposal contract

All proposers must implement a common, replaceable proposal contract. The AI proposer must
compete against deterministic and ML proposers using the same evaluation and validation
pipeline.

Required fields:
- `proposer_id`
- `proposer_version`
- `instrument`
- `decision`: `BUY` / `SELL` / `HOLD`
- `confidence`
- `rationale`
- `evidence_references`
- `proposed_entry`, `stop` and `target` where applicable
- `timestamp`
- `data_window`

The contract must be versioned and auditable. Results must be stored with the proposal,
its evidence references, and the subsequent realised outcome so that the project can measure
research quality rather than rely only on tests.

### AI-1B historical research access

The read-only research layer reuses stored historical candles, existing instrument and
import records, and the existing data-quality engine. The default AI tool registry exposes
four bounded tools: `discover_historical_instruments`,
`inspect_historical_coverage`, `retrieve_historical_candles`, and
`inspect_historical_data_quality`. Their arguments and results are documented in the
System Architecture. They have `READ_ONLY` permission and do not provide arbitrary SQL,
code, filesystem, network, broker, order, or database-write access.

Instrument discovery is based on actual stored history, not Instrument Master presence.
Quality and source-use eligibility are reported separately: only a complete quality report
with confirmed full calendar coverage can be `PASS`, and candle retrieval requires every
contributing import to have the user's `VERIFIED_PERMITTED` source-use declaration.
Unconfirmed quality remains `UNKNOWN`; the declaration is not an independent legal review.
Retrieved batches are bounded to 100 candles, timestamp queries to 3,660 days, and
intraday quality analysis to 92 days. Deterministic evidence references identify the
request, returned values, and source records but remain unverified (`UNKNOWN`).

### Remaining AI-1C scope

AI-1C is not part of this data-access milestone. It must build and independently validate
any bounded research/decision workflow on top of the proposal contract and these tools.
Evidence resolution/provenance verification and temporal evaluation safeguards must be
implemented before a workflow can claim verified evidence or evaluation results. Proposal
generation must remain separate from permission to execute; the existing Python risk and
execution boundary remains authoritative. AI-1B adds no autonomous decisions or execution.

## Historical research must not wait for calendar automation

The project must determine what historical data already exists and is already permitted for
offline research. If sufficient historical data is available, the AI proposer should begin
research using strict temporal boundaries and provenance controls. If it is insufficient,
then the missing data and permission gaps must be documented explicitly and treated as a
project gap, not as a reason to halt all AI architecture and offline research work.

The operational calendar and source-use work remains important, but it is a parallel
workstream and should not block the offline historical analysis and AI proposer evaluation
pipeline when the project has already available, permitted data.

## Dependency diagram and execution sequence

The corrected execution sequence is:

- AI Trading Brain (proposal engine + research tools + controlled validation)
  - depends on: available historical data, deterministic research utilities, existing Python validation controls
  - independent of: live broker connectivity and live trading authorization
- Historical Strategy Competition
  - compares AI, ML, SMA and random baselines on the same data windows, costs and constraints
  - depends on: common proposal contract, evaluation harness, benchmark definitions
- Operational Data Infrastructure (parallel)
  - exchange calendar discovery and confirmation
  - data provenance and permitted-use verification
  - quality gates and freshness checks
- Forward Paper Trading with AI proposer
  - planned and not currently authorized; any future operation requires separate approval
  - depends on data quality and risk validation gates
- Live Readiness Assessment
  - depends on forward-paper evidence, reliability and explicit approval

Project-wide rule: AI must never bypass Python validation or the Phase 8 risk boundary.

## First proposed implementation task for the AI research workflow

The first proposed implementation task is:

`Implement the common proposal contract and the historical evaluation harness for an AI proposer that consumes permitted historical data, emits BUY/SELL/HOLD proposals with evidence references, and compares those proposals against deterministic baselines using the same evaluation rules.`

This task is deliberately scoped to research, not execution. It establishes the required
contract and evaluation pipeline. Any later connection to paper simulation requires
separate authorization and remains subject to Python validation and Phase 8 controls.

## Proposed interfaces and dependencies

The future AI research workflow depends on:
- a versioned proposal contract
- a historical market-data access layer with provenance and time-window checks
- a strategy benchmark comparison harness
- deterministic validation and risk gates in Python
- evidence referencing for every rationale and decision
- existing Phase 8 execution boundary for any future paper/live route

The AI proposer must expose:
- a `generate_proposal()` interface
- a `validate_proposal()` path that returns structured pass/fail and evidence quality
- a `record_outcome()` hook for later learning and evaluation

The Python layer remains the final arbiter of risk, limits, position sizing and execution.

## AI-1C research evaluation; performance thresholds remain open

The project should judge the AI research workflow by performance and evidence quality, not
only by passing tests. These are evaluation dimensions, not approved performance
thresholds. Research-integrity requirements remain governed by the approved protocol.

- rule-based, ML and random baselines run on the same historical windows and costs
- no look-ahead bias, survivorship bias or leakage between training and validation
- proposal decisions remain traceable to evidence references and the underlying data window
- the AI proposer is measured on return, drawdown, win rate, trade frequency and risk-adjusted metrics
- evaluate whether the proposal engine outperforms or otherwise provides material evidence
  relative to the chosen comparator set under identical constraints; what qualifies as
  material validation remains open and requires explicit approval before a pass/fail decision
- the project records rejected proposals, evidence quality and subsequent realised outcomes
- no proposal can reach the paper-trading or execution boundary without Python validation and risk checks

These evaluation dimensions are not approved pass/fail thresholds and do not authorize
paper or live execution.

## Separation between AI research and operational prerequisites

Operational prerequisites remain necessary for Phase 4, forward paper trading and
live-readiness. Calendar automation need not block offline AI research when already
available data has verified permitted use and passes applicable quality and evidence
gates. This does not make source-use or evidence checks optional. The project must
explicitly separate:

- AI research readiness: historical data permission, proposal evaluation, benchmark comparison and evidence quality
- operational data readiness: exchange-calendar confirmation, data provenance, freshness and quality gates
- forward-paper evidence: paper-trading execution under validated conditions
- live readiness: separate explicit approval and independent assessment

## Planning estimates

This remains a staged effort. The strategic priority is to build and evaluate the AI
research workflow while calendar automation proceeds in parallel when appropriate.
Permitted source use and applicable data/evidence gates remain prerequisites for research
claims. Do not promise a live-trading date or assume that backtesting readiness alone is
sufficient for execution.
