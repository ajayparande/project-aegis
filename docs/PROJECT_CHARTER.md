# Project Aegis — Project Charter

**Purpose:** establish the product vision and non-negotiable product boundaries.
For current implementation and acceptance status, see
[`PROJECT_STATE.md`](./PROJECT_STATE.md).

## Vision

Project Aegis is intended to become an independently researching AI trading brain for
Indian markets: a transparent research laboratory that discovers and investigates
eligible instruments, generates evidence-grounded hypotheses and `BUY` / `SELL` /
`HOLD` proposals, and measures its research quality against deterministic and
appropriately validated ML baselines.

The objective is not merely to add more hardcoded indicators, nor to promise a profitable
bot. Indicators, deterministic strategies, ML, and AI proposals are distinct research
participants. They should be compared under the same point-in-time evidence, instruments,
dates, costs, and constraints.

## Researcher-first product principles

- The AI research workflow must discover and investigate eligible instruments using
  bounded, explicitly permitted research tools; it must not depend only on a handpicked
  instrument or a fixed indicator recipe.
- AI output is a hypothesis or proposal, not trusted evidence and not an order.
- `BUY`, `SELL`, and `HOLD` are all valid outputs. HOLD, abstention, and rejection for
  insufficient or unverifiable evidence are successful safety outcomes.
- Explanations must distinguish observations, calculations, assumptions, hypotheses, and
  unknowns. Numerical metrics are calculated by deterministic code and linked to
  reproducible evidence.
- Research seeks falsification and robust out-of-sample evidence, not confirmation of a
  preferred strategy.

## Hybrid AI and deterministic architecture

AI may assist with research, candidate investigation, hypothesis formation, proposal
explanation, and analysis of experiment results. Deterministic Python independently
validates inputs and evidence, point-in-time eligibility, data quality, financial
calculations, risk limits, position sizing, and any permitted execution. Python is the
authority for these checks; AI cannot authorize a trade, change protected controls, or
bypass a failed check.

Phase 8 remains the sole execution and risk boundary. Its presence does not change the
configured application mode, which remains `RESEARCH_ONLY`, and does not authorize
forward-paper or live operation. See
[`ARCHITECTURE_DECISIONS.md`](./ARCHITECTURE_DECISIONS.md) and
[`DEVELOPMENT_GUARDRAILS.md`](./DEVELOPMENT_GUARDRAILS.md).

## Long-term objectives

1. Discover eligible historical instruments through bounded, auditable research tools.
2. Form versioned, falsifiable hypotheses with evidence, expected behavior, and
   invalidation conditions.
3. Validate proposals and calculations independently in Python.
4. Compare AI research with deterministic, ML, random, and buy-and-hold baselines under
   identical point-in-time windows and explicit cost/liquidity assumptions.
5. Preserve experiment identity, data and source provenance, model/prompt/tool versions,
   parameters, decisions, abstentions, and outcomes sufficiently for audit and replay.
6. Evaluate temporal generalization, robustness, uncertainty, costs, and operational
   limits before considering later supervised paper activity.

These are product objectives, not claims that the corresponding end-to-end capabilities
are implemented or accepted. The authoritative status is
[`PROJECT_STATE.md`](./PROJECT_STATE.md).

## Explicit non-goals and exclusions

- No live trading authorization exists. No AI, workflow, application setting, or future
  assessment can silently enable it.
- No AI-controlled broker access, live order placement, autonomous strategy deployment,
  or autonomous risk-limit changes.
- No broker credentials in AI prompts or repository files.
- No claim of profitability, predictive edge, or guaranteed returns based on synthetic
  tests, a backtest alone, or a limited paper sample.
- No derivatives, leverage, shorting, high-frequency execution, paid data, paid hosting,
  or additional paid model services without separately approved scope.
- No assumption that a source-use declaration proves legal rights, or that a configured
  calendar is confirmed.
- No cloud deployment is required for local development.

## Related authority

- Product-level approved constraints: [`ARCHITECTURE_DECISIONS.md`](./ARCHITECTURE_DECISIONS.md)
- Current status: [`PROJECT_STATE.md`](./PROJECT_STATE.md)
- Research method: [`AI_RESEARCH_PROTOCOL.md`](./AI_RESEARCH_PROTOCOL.md)
- Unresolved decisions: [`OPEN_DECISIONS.md`](./OPEN_DECISIONS.md)
- Existing concise background: [`PROJECT_VISION.md`](./PROJECT_VISION.md)
