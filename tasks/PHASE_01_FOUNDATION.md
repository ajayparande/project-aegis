# Phase 1 — Foundation

## Goal
Create a secure, reproducible local Python project skeleton. No market connection or trading functionality.

## Tasks
- [ ] Create Python package and `pyproject.toml`.
- [ ] Add `.gitignore` for `.env`, credentials, tokens, datasets and local databases.
- [ ] Add `.env.example` containing names only, never real values.
- [ ] Add typed application configuration with safe defaults.
- [ ] Add structured logging with secret redaction.
- [ ] Add SQLite schema for sources, hypotheses, strategies, experiments and runs.
- [ ] Add a minimal Streamlit dashboard showing project mode and system status.
- [ ] Add pytest, formatting, linting and type-check commands.
- [ ] Add GitHub Actions CI for tests and static checks.
- [ ] Document clean installation and test execution.

## Acceptance criteria
- Clean environment can install the project using documented commands.
- CI passes on a fresh checkout.
- No broker credentials or order-placement code exists.
- Application starts in `RESEARCH_ONLY` mode.
- Secret scanning and `.gitignore` checks pass.
- No paid services are required for this phase.
