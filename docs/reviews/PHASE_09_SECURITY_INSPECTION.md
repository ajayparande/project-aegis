# Phase 9 Security Inspection

**Inspection date:** 2026-11-14
**Scope:** Read-only review of the Phase 9 AI Agent and Tool Framework, its tests, application integration, and relevant worktree changes.

## Executive summary

No confirmed exploitable vulnerability was identified at the review's confidence
threshold. The implementation uses an explicit tool registry, Python-enforced
permissions, strict recursive argument validation, process-enforced tool timeouts,
bounded provider requests, redaction, and fail-closed provider handling. The default AI
registry does not expose paper-account details and contains no order-placement or broker
tool.

Prompt instructions and `<data_block>` framing are not security boundaries. Escaping
retrieved content is defense-in-depth; the effective boundary is the fixed set of
registered tools and deterministic permission checks. Remaining items below are
informational risks or test gaps, not confirmed vulnerabilities.

## Findings

| # | Severity | Evidence | Assessment |
|---|---|---|---|
| 1 | INFORMATIONAL | `src/aegis/ai_agent/security.py:120-151`; `src/aegis/ai_agent/tools.py:49-233` | External/tool content is redacted, escaped and framed as untrusted. A prompt injection may still influence model-generated prose, but the inspected path does not let content register tools, bypass schema checks, or grant permissions. Not a confirmed permissions bypass. |
| 2 | INFORMATIONAL | `src/aegis/ai_agent/tools.py:659-729`; `src/aegis/ai_agent/orchestrator.py:148-232` | Research outputs, including strategy/backtest summaries, can be sent to a configured external provider. The inspected records did not establish that these outputs contain credentials or protected personal data. Treat disclosure of research data to a configured provider as an explicit deployment/data-governance decision. |
| 3 | INFORMATIONAL | `src/aegis/ai_agent/orchestrator.py:148-232`; `src/aegis/ai_agent/providers.py:27-104` | Step, tool-call, output-token, response-size, and aggregate provider-reported token limits are bounded. Repeated over-budget tool-call responses may still consume bounded provider iterations; aggregate token accounting relies on provider usage metadata. This is a cost/reliability limitation, not an unbounded loop. |
| 4 | INFORMATIONAL | `src/aegis/ai_agent/orchestrator.py:343-389` | Citations contain a bounded preview of tool output and successful evidence-bearing results are assessed as “Limited,” not “High.” The preview is useful for context but may not independently identify the source import or data provenance. |
| 5 | INFORMATIONAL | `src/aegis/ai_agent/providers.py:181-629`; `tests/test_ai_agent_security_regressions.py:349-394` | Provider handling is validated with local fixtures; no live provider call was made or tested. There are no automatic retries, so failures return safely rather than retrying, but transient availability is not improved. |

No CRITICAL, HIGH, MEDIUM, or LOW confirmed defect was found.

## Control review and implementation evidence

### 1. Tool argument validation

`ToolRegistry._validate_arguments` first requires an object, rejects undeclared keys,
checks required parameters, and applies optional defaults before execution
(`src/aegis/ai_agent/tools.py:235-345`). Values are recursively checked against the
schema: booleans are excluded from integer/number inputs; numeric inputs must be finite
and within declared bounds; strings, arrays, items, and nested objects are bounded and
validated. Schema declarations also reject inconsistent constraints
(`src/aegis/ai_agent/models.py:50-249`). Provider adapters validate that tool arguments
are objects, and the orchestrator rejects malformed tool-call structures before calling
the registry (`src/aegis/ai_agent/providers.py:215-272, 366-405, 504-574`;
`src/aegis/ai_agent/orchestrator.py:148-196`). I found no path by which malformed
provider arguments reach a handler in the default flow.

### 2. Tool permissions and side effects

The registry is explicit: handlers are registered by application code, permissions are
checked at registration and again before execution, and only `READ_ONLY`/`RESEARCH`
permissions are allowed (`src/aegis/ai_agent/tools.py:75-118, 155-233`;
`src/aegis/ai_agent/security.py:139-171`). Prohibited mutation, order, broker, shell,
and execution-related name fragments are rejected. The default registry exposes six
research tools (`src/aegis/ai_agent/tools.py:733-966`); it contains no account-report,
order-placement, broker, mode-change, or risk-setting tool.

The inspected AI tools perform reads or deterministic calculations. The separate
dashboard paper-account panel is read-only and is not an AI tool
(`app.py:687-740, 741-843`). No real or simulated order path was found in the AI agent.
The application configuration supports `RESEARCH_ONLY`; the AI agent has no tool to
change execution mode or risk settings.

### 3. Execution timeouts

Handlers execute in a spawned worker process. The parent joins for a configured,
validated timeout, terminates/kills a timed-out worker, and bounds the result before
returning it (`src/aegis/ai_agent/tools.py:49-155`). This is enforceable termination,
not merely duration measurement. Provider adapters pass validated timeout values to
`urllib.request.urlopen` (`src/aegis/ai_agent/providers.py:205-272, 356-405, 494-574`).
Custom handlers must be importable/pickleable under Windows spawn; this is a documented
extension constraint, not a vulnerability in the fixed default registry.

### 4. Provider reliability and budgets

Provider response bodies are capped, JSON and nested response shapes are validated,
malformed tool arguments are rejected, and token-usage values are checked
(`src/aegis/ai_agent/providers.py:27-104, 215-272, 366-405, 504-574`). Provider output
tokens and request timeouts have constructor bounds. The orchestrator limits steps,
total tool calls, and aggregate provider-reported tokens
(`src/aegis/ai_agent/orchestrator.py:60-196`). Provider errors are converted to failed
research results with redacted messages; there are no retries. With no configured
Gemini key, the dashboard chooses the offline `MockLLMProvider`; provider availability
is optional (`app.py:741-843`).

### 5. Audit security

Tool arguments are present in `ToolCallAudit`, but are recursively redacted before
recording (`src/aegis/ai_agent/orchestrator.py:48-55, 255-263`). Tool output summaries
are bounded and originate from redacted tool output; provider/network errors are
redacted by the adapters. Audit records retain prompt hashes and metadata rather than
raw prompts or full provider responses (`src/aegis/ai_agent/models.py:299-330`;
`src/aegis/ai_agent/orchestrator.py:93-142, 196-289`). No credential value was printed
or used during this inspection.

### 6. Evidence and confidence

Evidence references contain up to 240 characters of tool output. Confidence is low when
there is no tool evidence, a tool error, or explicit empty/unverified/incomplete/stale/
unavailable evidence; otherwise it is limited and explicitly disclaims independent
verification of source rights, calendar completeness, and empirical validity
(`src/aegis/ai_agent/orchestrator.py:343-389`). Confidence cannot reach “High” merely
because a handler returned successfully. The citations are traceable to a named tool
and output preview, but are not a full provenance record.

## Existing tests and gaps

Existing AI tests cover query bounds, redaction, permission checks, argument validation,
output truncation, individual research tools, multi-step workflows, provider timeout
failure, tool-call budgets, and missing provider-key behavior
(`tests/test_ai_agent.py:200-366, 504-655`). The security-regression file covers
delimiter escaping, nested/typed bounds, paper-account exclusion, process timeout and
cleanup, output caps, audit redaction, insufficient-evidence confidence, malformed
provider tool calls, token budgets, provider argument parsing, and response bounds
(`tests/test_ai_agent_security_regressions.py:73-394`). Dashboard tests cover offline
provider selection and fake-key display without submitting a provider request
(`tests/test_dashboard.py:229-274`).

Recommended additional tests, without suggesting a current exploit:

- Exercise repeated over-budget tool-call responses and assert a strict upper bound on
  provider requests as well as tool executions.
- Assert prompt-injection content cannot change the deterministic permission decision
  across a complete mocked orchestration flow; delimiter escaping alone does not test
  model behavior.
- Exercise provider timeout and credential-redaction paths for each adapter using local
  fakes, including malformed response bodies and errors.
- Test that evidence citations remain useful for empty, incomplete, or unavailable
  results and include source identifiers where available.
- Keep any future provider compatibility checks mocked or separately approved; this
  inspection did not call external services.

No tests were run for this inspection.

## Phase 9 change inventory and baseline attribution

At inspection start the repository was already dirty on branch `main`, HEAD
`7bdd4cfe2c217e0628872affa74d25e4671db950`. The Phase 9-related tracked diffs visible
against that HEAD are:

| Tracked file | Change and assessment |
|---|---|
| `app.py` | Adds a read-only paper simulation panel (Phase 8) and the optional AI research panel (Phase 9). The AI panel runs only after form submission, uses the mock provider without a key, and exposes no order controls. The new AI-panel tests cover startup/provider labeling. |
| `src/aegis/__init__.py` | Re-exports `AIAgent`, `MockLLMProvider`, `ToolRegistry`, and the default registry factory. This is a small Phase 9 public-API addition; no execution control is added. |
| `docs/DEVELOPMENT_ROADMAP.md` | Updates Phase 9 status alongside Phase 4/8 status. The Phase 9 row describes a bounded advisory framework and links its task/report. |
| `docs/SYSTEM_ARCHITECTURE.md` | Adds Phase 9 architecture and controls alongside Phase 8 paper-execution documentation. |
| `tasks/PHASE_09_AI_AGENT_AND_TOOL_FRAMEWORK.md` | Marks Phase 9 implementation tasks complete and explicitly defers AI-generated code execution/review. The prompt-injection test checkbox should be read as permission-boundary coverage, not proof that model prose is immune to injection. |
| `tests/test_dashboard.py` | Adds Phase 8 read-only panel coverage and Phase 9 optional-provider startup/display tests; the fake key is not used for a network request. |

Other tracked dirty files concern README/Phase 8, validation/Phase 8, and Phases 2–4.
They are not attributed to Phase 9 by this inspection. The worktree also has untracked
Phase 8 paper-trading files/reports and integration tests.

The following Phase 9-associated files were untracked at inspection start:

- `src/aegis/ai_agent/__init__.py`, `models.py`, `orchestrator.py`, `providers.py`,
  `security.py`, and `tools.py`
- `tests/test_ai_agent.py` and `tests/test_ai_agent_security_regressions.py`
- `docs/reviews/PHASE_09_IMPLEMENTATION_REPORT.md` and
  `docs/reviews/PHASE_09_INDEPENDENT_REVIEW_AND_COMPLETION_REPORT.md`

These untracked files are present and were reviewed, but Git cannot establish when or
by whom they were created or their contents at the baseline commit. They are not
attributed as new in this audit. No unrelated dirty file was treated as Phase 9 work.

## Architectural boundaries

- The agent cannot submit simulated or real orders; no broker integration or order
  placement tool is registered.
- It cannot change execution mode, risk limits, or persistent strategy configuration.
- Deterministic Python remains authoritative for indicators, data quality, backtests,
  accounting, risk, and execution.
- The application remains in `RESEARCH_ONLY` mode and can use a mock provider offline.
- The AI agent is advisory; it does not establish market-data permission, confirmed
  calendar coverage, empirical profitability, or Phase 10 readiness.

## Prioritized actions

1. Keep the explicit tool allowlist, Python permission checks, and no-order boundary as
   the authoritative controls; do not treat prompt instructions or delimiters as a
   security guarantee.
2. Before enabling an external provider in a deployment, decide which research outputs
   may be sent externally and document the provider/data policy.
3. Add the bounded repeated-call and mocked prompt-injection tests listed above.
4. Preserve conservative confidence and evidence limitations; add source identifiers
   where the deterministic data APIs provide them.

## Recommendation and read-only confirmation

**Phase 9 recommendation: CONDITIONAL ACCEPT** for optional advisory research only,
subject to the documented external-provider data policy and test gaps. This is not
approval for paper/live order execution, live-data operation, or weakening any risk
control.

The inspection was strictly read-only: no source or test was edited, no tests were run,
no package/dependency was changed, no database was accessed or migrated, no external
API or provider credential was used, and no Git change was staged, committed, or pushed.
The sole file created by this task is this report.
