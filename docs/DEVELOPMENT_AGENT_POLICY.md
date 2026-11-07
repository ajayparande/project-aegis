# Development Agent Policy

## Initial team
Use one coding model with bounded logical roles:
- Architect/planner.
- Implementer.
- QA/tester.
- Security reviewer.

Do not deploy 50 agents. Add specialist agents only when a measured bottleneck exists and costs remain within budget.

## Workflow
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
