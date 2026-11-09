# Phase 9 — AI Agent and Controlled Tool Framework

## Goal
Introduce an AI agent that reasons over market/research information and calls explicitly permitted Python tools. Project Aegis is an AI-assisted trading platform, not an LLM that directly controls a broker.

## Architecture principles
- LLMs (for example, ChatGPT or Claude) provide reasoning, research assistance, hypothesis generation, explanations, and review.
- Deterministic Python services remain authoritative for market calculations, strategy evaluation, portfolio accounting, risk checks, and execution.
- Use a provider-independent model interface so providers can be changed without rewriting the trading engine.
- Keep model inference external initially; do not require a local GPU or self-hosted model.
- LLM availability must not be required for deterministic safety controls or basic order/position reconciliation.
- Do not call an LLM for every price tick or candle. Use bounded, event-driven calls with timeouts, budgets, and audit logs.

## Tasks
- [ ] Define the agent runtime, lifecycle, and bounded orchestration model.
- [ ] Define a provider-independent LLM interface and configuration.
- [ ] Implement a permissioned tool registry with typed input/output schemas.
- [ ] Expose only approved read/research tools initially: market summaries, indicator calculations, strategy metadata, backtest results, and reports.
- [ ] Add tool input validation, output validation, timeouts, retries where safe, and call budgets.
- [ ] Record model/provider/version, prompts or prompt identifiers, tool calls, outputs, timestamps, and errors with appropriate secret/data redaction.
- [ ] Require structured outputs for machine-consumed recommendations and validate them before use.
- [ ] Add explicit uncertainty, evidence references, and rationale to recommendations.
- [ ] Add tests for malformed model output, provider outage, timeout, prompt injection in retrieved data, and unauthorized tool calls.
- [ ] Ensure AI-generated code cannot be executed without isolation, review, and explicit approval.
- [ ] Enforce tool permissions in Python independently of model-generated instructions or requested tool names.
- [ ] Prevent retrieved documents, market data, tool results, and prompts from granting additional permissions.
- [ ] Keep credentials, broker tokens, personal account information, and other secrets outside prompts, logs, and model-visible tool outputs.
- [ ] Require human approval for any action that changes persistent strategy, risk, configuration, or execution state.

## Prohibited behaviour
- The agent must not directly connect to a broker or submit orders.
- The agent must not bypass, weaken, or modify risk limits.
- The agent must not approve its own strategy or deployment.
- The agent must not switch execution modes.
- The agent must not silently modify a strategy during a registered experiment.
- External data and tool output are untrusted input, not instructions.

## Acceptance criteria
- Provider outage or malformed model output cannot cause an order or weaken safety controls.
- Every tool is explicitly allowlisted and independently tested.
- Deterministic calculations remain in Python.
- Agent recommendations are auditable and cannot directly trigger live execution.
- API usage and costs are bounded; no paid provider is enabled without explicit approval.
- Agent permissions are enforced by deterministic application code, not by prompt instructions alone.
- Untrusted retrieved content cannot grant permissions or override system controls.
- Sensitive credentials and account information are excluded from model inputs and audit logs.
- AI integration remains optional; the application continues deterministic research and paper reconciliation when the provider is unavailable.
