# AI Research Protocol

**Purpose:** define the evidence and evaluation discipline required before AI research
claims are treated as validated. This is a governance specification, not evidence that
AI-1C or empirical strategy acceptance is implemented.

## Approved requirements and unapproved proposals

The requirements in this document are approved research-integrity constraints where
they are marked **Required**. Any proposed metric, minimum sample size, score threshold,
cost stress multiplier, or promotion rule is explicitly **PROPOSAL — NOT APPROVED**
until recorded as an approved decision. Do not choose a threshold after viewing results.
See [`OPEN_DECISIONS.md`](./OPEN_DECISIONS.md).

## Researcher-first AI

**Required:** the AI research workflow must discover and investigate eligible
instruments using bounded tools, formulate falsifiable hypotheses, and return a
traceable `BUY`, `SELL`, `HOLD`, or abstention proposal. It must not be restricted to
selecting from a hardcoded indicator checklist. HOLD or rejection because evidence is
insufficient, untrusted, stale, or contradictory is a valid result.

AI output is an untrusted proposal. Python independently resolves evidence, validates
instrument/data eligibility, enforces temporal rules, calculates metrics and costs, and
applies risk controls. The AI cannot execute, authorize, change risk limits, or bypass a
failed Python gate. Phase 8 remains the only execution/risk boundary; `RESEARCH_ONLY`
remains active.

## Evidence, provenance, and reproducibility

For each research run, retain a versioned, auditable record of:

- Instrument identity and point-in-time universe definition, including how eligibility
  was determined and what historical membership/survivorship information is unavailable.
- Data source identity, source-use declaration state, source file/import identifiers,
  date range, interval, timezone, calendar status/coverage, quality findings, and
  material data fingerprints.
- Hypothesis/proposal schema and version; prompt/template identifier and version or
  hash; provider/model identity where available; tool names, versions, normalized
  arguments, bounded results/evidence references, parameters, and evaluation code
  identity.
- Decision time, information cutoff, proposal outcome including HOLD/rejection, and
  later measured outcome with its exact horizon and calculation.
- Assumptions, exclusions, missing evidence, failures, and whether outputs are observed
  facts, deterministic calculations, estimates, or hypotheses.

Hashes support change detection but do not prove source rights, recover lost inputs,
attest the exact in-memory executable, or independently verify evidence. A source
permission declaration is not a legal determination. Keep credentials and sensitive
account data out of prompts and logs. Existing AI-agent controls may intentionally avoid
persisting raw prompts/responses; do not weaken privacy/security rules to make replay
easier. Define any richer retention policy through an approved decision.

Reproducibility means deterministic calculations and traceable inputs/configuration. For
non-deterministic external model calls, record enough authorized response/evidence
metadata to replay the recorded decision path where policy permits; do not claim that a
repeat API call will reproduce the same output.

## Temporal separation and bias controls

**Required:**

- Every feature and decision must use only data available at its decision timestamp.
  Use completed candles and explicit exchange-local calendar semantics; future outcomes
  may be labels only after the decision cutoff.
- Split data chronologically into development, validation, and locked holdout roles.
  Purge overlapping outcome/label windows at boundaries.
- Do not use the locked holdout for repeated prompt, model, tool, feature, or parameter
  tuning. Record holdout access with run ID, operator, purpose, and scope.
- Freeze the model/provider version where available, prompt/template, tools, parameters,
  universe, data window, costs, and evaluation rules before final holdout evaluation.
- Point-in-time instrument eligibility and membership are required to claim that
  survivorship bias was controlled. If historical membership is unavailable, state the
  limitation and do not imply that current-universe tests are survivorship-free.
- Data quality, source-use eligibility, evidence verification, and execution/risk
  validation are separate gates. Passing one does not satisfy the others.

## Baselines, costs, and statistical evaluation

Compare candidate AI decisions with deterministic baselines, an appropriately matched
random baseline, and buy-and-hold or a suitable index total-return series where data
and permissions permit. All comparators must use the same universe, decision times,
periods, costs, liquidity assumptions, and exclusions.

Calculate reported metrics in deterministic Python. Disclose fees, spread, slippage,
capacity, turnover, benchmark limitations, sample size, uncertainty, and missing data.
Use walk-forward evaluation, confidence/uncertainty analysis, and multiple-testing
controls where justified. The exact statistical methods, confidence levels, and
multiple-testing procedures remain open; select and approve them in the pre-registered
protocol before evaluation. Report absolute results and benchmark-relative results; do
not use raw returns alone as evidence of edge.

Test robustness with cost/slippage stress, removal or influence analysis of top trades,
parameter perturbations, ablation, placebo hypotheses, and shuffled or otherwise
nullified evidence. Invalid or uninformative evidence should yield HOLD/rejection rather
than increased confidence.

**No project-approved numerical thresholds are established here.** A 1.5x–2x
cost/slippage stress range is a proposed evaluation example only: it is neither an
approved requirement nor a universal acceptance threshold. Any chosen stress multiplier
and its pass/fail interpretation must be explicitly approved and pre-registered before
evaluation. Minimum sample sizes, confidence levels, statistical methods, drawdown
limits, promotion rules, and performance thresholds remain OPEN.

## Scope and acceptance boundary

Synthetic tests demonstrate implementation behavior, not predictive performance,
market-data readiness, or licensing. Phase 7's current harness is classifier research,
not a cost-aware trading-strategy evaluation. AI-1B's bounded historical tools do not
constitute an AI researcher or verify their evidence independently. AI-1C must satisfy
the applicable acceptance criteria in the roadmap and Phase 9 task before anyone claims
a validated AI research workflow.

See [`PROJECT_STATE.md`](./PROJECT_STATE.md) for current status,
[`DEVELOPMENT_ROADMAP.md`](./DEVELOPMENT_ROADMAP.md) for the planned AI-1C gate, and
[`OPEN_DECISIONS.md`](./OPEN_DECISIONS.md) for unresolved protocol values.
