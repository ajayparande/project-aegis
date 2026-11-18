# Development Guardrails

These constraints apply to every development agent and task. The workflow is further
specified in [`CLAUDE.md`](../CLAUDE.md) and [`AGENTS.md`](../AGENTS.md).

## Scope and approval

- Inspect the worktree, linked specifications, relevant source, and tests before editing.
- State the intended scope and files before substantial changes. Modify only the
  explicitly requested/approved files and behavior.
- Stop and ask if approved documents conflict, a required behavior is undecided, or a
  change would expand scope, alter an approved contract, remove existing behavior, or
  cross a safety boundary.
- Roadmaps and unchecked task items are plans, not implementation authorization.
- Do not stage, commit, push, reset, stash, clean, discard, or restore user worktree
  changes unless explicitly authorized.
- Preserve all modified, untracked, and user data. Inspect final diffs; never silently
  overwrite another task's work.

## Database and data preservation

- Normal application databases must never be deleted or recreated as part of routine
  development or testing. Tests must use isolated disposable databases.
- Do not access, initialize, migrate, or modify the normal application database without
  explicit approval for the exact operation. Destructive operations are never implied by
  general approval. Any exceptional destructive operation requires separate explicit
  approval identifying the exact database and operation.
- Do not run application workflows against it under documentation-only or read-only
  scope. Do not infer its contents or schema from code constants or old reports.
- Schema changes require separate explicit approval, transactional review, migration
  history preservation, and isolated temporary-database tests.
- Use parameterized SQL. Preserve all existing records. New historical OHLC prices use
  canonical decimal text in `historical_candles`, not SQLite `REAL`.
- Synthetic tests must use temporary databases, logs, and calendar files. A synthetic
  permission declaration is test-fixture data, not legal proof.

## Execution, risk, and AI boundaries

- Keep application mode `RESEARCH_ONLY`; no live-trading authorization exists.
- Do not connect a broker, access credentials, place live orders, run unauthorized
  forward-paper execution, or expose a path that lets an AI place orders.
- Phase 8 remains the sole execution/risk boundary. AI cannot authorize trades, bypass
  Python validation, change risk limits, or silently change execution mode.
- M2 is historical research/backtesting only. Preserve every constraint in
  [`tasks/M2_PORTFOLIO_ACCOUNTING.md`](../tasks/M2_PORTFOLIO_ACCOUNTING.md), including
  shared snapshot/calendar qualification, fixed sleeves, provenance, stale-mark rules,
  and hypothetical-only liquidation.
- Never weaken source-use, calendar, provenance, quality, or qualification gates merely
  to make tests pass.
- Never claim profitability or guaranteed returns.

## Environment, secrets, and cost

- Supported development environment: Windows/PowerShell, Python 3.11, VS Code, the
  existing virtual environment, local SQLite, and Streamlit.
- Do not require Docker, WSL, Node.js, Kubernetes, PostgreSQL, or cloud infrastructure
  without an explicitly approved need.
- Never hardcode or expose secrets. Keep `.env.example` free of real credentials; do not
  put credentials, account data, or sensitive material in prompts, logs, tests, or
  documents.
- No paid data, model, hosting, or other service purchase/subscription without explicit
  user approval. Follow [`BUDGET_AND_COST_CONTROLS.md`](./BUDGET_AND_COST_CONTROLS.md).

## Quality and acceptance

- Add focused deterministic tests for new behavior and regressions, using isolated
  fixtures. Do not run tests, benchmarks, workflows, or full suites prohibited by the
  task.
- Task-specific validation restrictions override general quality-check instructions. If
  the full test suite is prohibited, do not run it. Report which checks were and were
  not executed.
- Use project linters, formatters, and type checks as appropriate. Report only checks
  actually run and their exact results.
- Review changes against the milestone's written acceptance criteria; inspect the final
  diff and report changed files, limitations, risks, and status.
- Tests passing means tested, not independently reviewed or accepted. Independent
  review, acceptance, operational readiness, empirical validation, and authorization
  are separate gates.
- Never claim completion or acceptance without the required evidence. If task
  restrictions prevent required checks, report the limitation and do not claim
  completion or acceptance.
- Stop at the requested milestone. M2-B received an independent read-only ACCEPT on
  2026-11-16 for its bounded qualification scope. The M2-B ACCEPT is owner-reported;
  its original artifact is absent. Its schema-provenance correction requires focused
  independent review; no automatic reacceptance is claimed.
- M2-C is independently accepted for qualified historical per-sleeve M1B composition.
  M2-D accounting is implemented and technically ACCEPTED; distinct-reviewer independent
  sign-off remains pending. These decisions are recorded from project-owner-supplied
  acceptance history; see `PROJECT_STATE.md` for the evidence limitations. Technical
  verification/ACCEPT is distinct from independent acceptance.
- M2-E has not started and is not authorized. Overall M2 is not accepted. No milestone
  acceptance authorizes later work, forward-paper operation, or live trading.

See [`VALIDATION_AND_ACCEPTANCE.md`](./VALIDATION_AND_ACCEPTANCE.md) and
[`DEVELOPMENT_AGENT_POLICY.md`](./DEVELOPMENT_AGENT_POLICY.md) for supporting procedures.
