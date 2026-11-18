# Development Agent Policy

> This file is supporting workflow guidance. The mandatory repository-wide workflow,
> approval requirements, preservation rules, and status vocabulary are in
> [`../CLAUDE.md`](../CLAUDE.md) and
> [`DEVELOPMENT_GUARDRAILS.md`](./DEVELOPMENT_GUARDRAILS.md). In a conflict, stop and
> report it rather than selecting a convenient interpretation.


## Current collaboration model

Use bounded logical roles; separate products/models are not required for every role:

- **Technical reasoning and architecture:** ChatGPT planning/review support, with user approval for decisions
  that require authorization.
- **Implementation and local test execution:** Codex or GitHub Copilot in the user's local
  repository, following an implementation-ready task prompt.
- **Independent review:** Claude, read-only by default, examining supplied or explicitly
  connected source, tests, diffs, and execution evidence.
- **Owner:** the user approves open policy decisions, scope changes, sensitive actions,
  and milestone progression.

This is the current working arrangement, not a requirement to use paid services or
multiple agents. Add specialist agents only when a measured bottleneck exists and costs
remain within budget.

## Workflow

1. ChatGPT and the user establish scope, constraints, and acceptance criteria.
2. Codex/Copilot inspects the relevant local source and implements only the authorized scope.
3. Codex/Copilot runs the specifically requested tests, linting, and type checks, reporting
   exact commands and results.
4. Claude independently reviews the actual implementation and evidence. Claude must
   distinguish its own inspections/tests from results reported by the implementer.
5. The user decides acceptance and whether the next milestone may begin.
6. Update the relevant status, decision, and evidence documents after the milestone
   decision; do not record implementation as acceptance.

## Claude Desktop access and Skills

Claude Desktop can inspect only files supplied in its conversation/Project Knowledge or
made available through an explicitly configured and authorized local integration. Do
not assume that installing Desktop grants repository or terminal access. If source,
diffs, or command output are not available, state what is missing and do not claim a
complete audit. Custom Skills provide task procedures only; they do not override
`CLAUDE.md`, `AGENTS.md`, approved specifications, or user permissions, and they do not
grant filesystem, terminal, database, or write access.


## Supporting review checks
1. Planner creates a small task with acceptance criteria.
2. Implementer works in a feature branch or isolated workspace.
3. Automated tests, linting and type checks run.
4. QA reviews test coverage and edge cases.
5. Security reviewer checks secrets, dependencies and unsafe capabilities.
6. Human approval is required for merge of sensitive changes.
7. Update docs and experiment/change log.

## Agent permissions
- No access to broker trading credentials.
- No permission to place live orders.
- No ability to change protected risk limits.
- No permission to approve or merge its own sensitive changes.
- No autonomous purchases, subscriptions or cloud deployments.
- No destructive repository actions without approval.

## Framework selection
Start with GitHub, Python, standard tests and a simple task workflow. Evaluate a multi-agent coding framework only after checking its current maintenance, licence, security, compatibility, setup burden and API cost. Framework choice must not be hard-coded into the product architecture.
