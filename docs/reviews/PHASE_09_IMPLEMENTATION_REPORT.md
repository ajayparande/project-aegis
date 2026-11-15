# Phase 9 Implementation Report — AI Agent and Controlled Tool Framework

**Review status:** Implemented; independently reviewed and hardened

**Execution mode:** `RESEARCH_ONLY`
**Scope:** Advisory research only; no broker integration, order placement, or Phase 10 work

## Summary

The Phase 9 AI framework provides an optional provider interface and a bounded research
orchestrator. Deterministic Python remains authoritative for market calculations, data
quality, risk controls, accounting, and execution. Provider output is untrusted; Python
permissions and the explicit tool registry remain authoritative.

The original implementation report overstated prompt-injection protection, account-data
privacy, token-budget enforcement, and evidence confidence. Those claims have been
corrected here after an independent security review. See the [independent review and
completion report](./PHASE_09_INDEPENDENT_REVIEW_AND_COMPLETION_REPORT.md) for findings,
regression tests, repository state, and final validation.

## Implemented components

| Module | Responsibility |
|---|---|
| `src/aegis/ai_agent/models.py` | Typed provider, tool, evidence, and audit models; bounded JSON-schema serialization |
| `src/aegis/ai_agent/security.py` | Query bounds, secret redaction, permission checks, escaped untrusted-data framing |
| `src/aegis/ai_agent/providers.py` | Mock, Gemini, OpenAI, and Anthropic provider interfaces and response validation |
| `src/aegis/ai_agent/tools.py` | Explicit allowlist, recursive argument checks, output bounds, isolated handler timeouts |
| `src/aegis/ai_agent/orchestrator.py` | Bounded interaction, provider/tool-call validation, redacted audit, conservative evidence assessment |
| `tests/test_ai_agent.py` | Core provider, tool, and orchestration tests |
| `tests/test_ai_agent_security_regressions.py` | Focused security and resource-bound regressions |

The default provider-visible registry contains six non-sensitive research tools:
instrument lookup, dataset quality, technical indicators, price-action patterns,
backtest summaries, and strategy experiment results. Paper-account reports and positions
are not exposed to the AI provider.

## Security and resource controls

- Tools must be explicitly registered and pass deterministic `READ_ONLY`/`RESEARCH`
  permission checks. Mutating, broker, order, and execution-related tool names are
  rejected. No shell, arbitrary Python, or SQL tool is registered.
- Tool argument objects reject unknown keys and enforce required fields, primitive
  types (including boolean-vs-number distinctions), finite numeric bounds, string
  lengths, array sizes/items, and nested object schemas.
- Registered handlers run in a spawned process with a configurable, bounded timeout;
  results are redacted and bounded before returning to the model. Custom handlers must
  be importable/pickleable.
- Provider requests use timeouts and output-token caps. Provider responses are capped at
  1 MB and validated for response structure, tool-call argument shape, and token usage.
- The orchestrator bounds steps, total tool calls, and provider-reported aggregate token
  usage. Confidence is not raised to “High” based on tool completion; empty, unverified,
  incomplete, stale, or unavailable evidence produces a low-confidence assessment.
- Tool arguments are recursively redacted in audit records. Raw prompts/responses are not
  persisted; records retain hashes, bounded tool summaries, usage, and sanitized errors.
- Retrieved data is escaped and framed as untrusted. This framing is defense-in-depth,
  not a security boundary; permissions and tool allowlists are enforced in Python.

## Limitations

- Provider adapters were validated with deterministic local tests, not live API calls.
  Live provider behavior, billing, and provider-specific schema acceptance remain
  unverified. External providers remain optional.
- Aggregate token usage depends on provider-reported usage metadata. Per-request output
  caps and fixed orchestration ceilings bound exposure, but there is no tokenizer-based
  estimate of prompt input tokens or guarantee of provider billing.
- A process timeout isolates handler execution but is not a general OS sandbox for
  arbitrary code. Only trusted, explicitly registered application handlers are allowed.
- Confidence is intentionally capped at “Limited” for successful evidence-bearing
  research. The agent does not establish source-use rights, confirmed calendar coverage,
  or empirical profitability.
- AI-assisted hypothesis drafting and generated-code execution/review remain deferred.

## Safety status

The AI agent is advisory and has no path to broker connectivity or order execution.
Paper-account data is not exposed by the default registry. No execution mode or risk
control was changed. The application remains `RESEARCH_ONLY`; Phase 2 operational
prerequisites remain outstanding, and Phase 10 was not started.
