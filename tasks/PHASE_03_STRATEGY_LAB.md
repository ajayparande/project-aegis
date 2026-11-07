# Phase 3 — Strategy Research Laboratory

## Goal
Create versioned, testable strategy hypotheses; do not assume they work.

## Tasks
- [ ] Define a machine-readable strategy specification.
- [ ] Create experiment registry and immutable run metadata.
- [ ] Implement simple transparent baseline strategies for research.
- [ ] Define instrument universe and historical membership limitations.
- [ ] Add deterministic signal-generation tests.
- [ ] Add AI-assisted hypothesis drafting with citations and explicit uncertainty.
- [ ] Ensure generated code is isolated and reviewed before execution.

## Acceptance criteria
- Each strategy has rationale, rules, parameters and limitations.
- Every run records code, data and configuration versions.
- No strategy can access future bars.
- AI output cannot directly trigger execution.
