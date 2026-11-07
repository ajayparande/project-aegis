# Phase 3 — Strategy Research Laboratory

## Goal
Create versioned, testable strategy hypotheses and deterministic signal generators. Do not assume that a strategy works or is profitable.

## Tasks
- [ ] Define a machine-readable, versioned strategy specification.
- [ ] Create an experiment registry and immutable run metadata.
- [ ] Implement transparent baseline strategies for research.
- [ ] Define instrument universe and historical membership limitations.
- [ ] Add deterministic signal-generation tests.
- [ ] Add AI-assisted hypothesis drafting with citations, evidence, and explicit uncertainty.
- [ ] Ensure generated code is isolated, reviewed, and tested before execution.
- [ ] Record strategy parameters, source version, data version, and configuration for every evaluation.
- [ ] Define a controlled process for proposing, reviewing, and versioning strategy changes.

## Acceptance criteria
- Each strategy has a rationale, rules, parameters, intended timeframe, and known limitations.
- Every run records code, data, and configuration versions.
- No strategy can access future bars or information unavailable at decision time.
- AI output cannot directly trigger execution or change risk limits.
- A tested strategy version is immutable for the duration of a registered experiment.
