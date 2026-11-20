# Phase 1 Foundation — Validation Report

**Date:** 2026-11-20

**Scope:** Phase 1 foundation only

**Report type:** Implementer validation evidence; not an independent audit or acceptance
**Recommendation:** `BLOCKED`

## 1. Baseline

Captured before this validation's edits:

- Branch: `main`
- HEAD: `0d6c65a69b94345fea170017128c632d52b5f9e9`
- Remote status: `main...origin/main [ahead 25]`
- Index: no staged changes.
- Python: 3.11.9. Pylance selected the existing project interpreter at
  `.venv\Scripts\python.exe`.
- Installed project: `project-aegis 0.1.0`, editable from this repository.
- Installed validation tools: pytest 9.2.0, Ruff 0.17.4, MyPy 2.4.1, Streamlit
  1.67.0.
- Master requirements: `docs/PROJECT_AEGIS_MASTER_PRODUCT_AND_SYSTEM_REQUIREMENTS.md`
  (untracked at baseline).
- Source schema constant: 18 in `src/aegis/database.py`. This is source-code state,
  **not** evidence of any normal/on-disk database version.

Pre-existing dirty tracked files:

- `src/aegis/database.py`
- `src/aegis/forward_runtime.py`
- `src/aegis/paper_equity.py`
- `tests/test_paper_equity.py`

Pre-existing untracked files:

- `docs/M5_MULTI_INSTRUMENT_RUNTIME.md`
- `docs/M5_PORTFOLIO_PRETRADE.md`
- `docs/PROJECT_AEGIS_MASTER_PRODUCT_AND_SYSTEM_REQUIREMENTS.md`
- `src/aegis/multi_instrument_runtime.py`
- `src/aegis/portfolio_pretrade.py`
- `tasks/M5_B2_PORTFOLIO_PAPER_EXECUTION.md`
- `tests/integration/test_multi_instrument_runtime.py`
- `tests/integration/test_portfolio_pretrade.py`
- `tests/test_multi_instrument_runtime.py`
- `tests/test_portfolio_pretrade.py`

All pre-existing changes were preserved. No normal database, broker, credential,
external endpoint, paid service, or live mode was accessed.

## 2. Phase 1 criteria and evidence

The controlling Phase 1 criteria are in
[`PHASE_01_FOUNDATION.md`](../../tasks/PHASE_01_FOUNDATION.md). Checkboxes below
describe this validation, not independent acceptance.

| Criterion | Result | Evidence / limitation |
|---|---|---|
| Valid Python package layout | **PASS** | `src/aegis/`, `src/aegis/__init__.py`, and setuptools `src` layout are present; the package is installed editable in `.venv`. |
| Python 3.11 compatibility | **PASS** | `.venv` is Python 3.11.9; `pyproject.toml` requires Python `>=3.11`; Ruff targets `py311`. |
| Project installs in local Python environment | **PASS, existing environment only** | `project-aegis 0.1.0` is installed editable in the existing `.venv`; `pip check` passes. A fresh install was not executed. |
| Reproducible dependencies | **FAIL** | `pyproject.toml` uses open lower bounds (for example `pytest>=8`, `ruff>=0.6`, `mypy>=1.11`); no lock/constraints file was found. The master’s G08 requires reproducible environment lockfiles. The installed environment works, but another install can resolve different versions. |
| Documented installation and startup | **PASS** | Added a Windows/Python 3.11 install/startup section to `README.md`. It documents that startup does not initialize or migrate a database. |
| No unintended import/startup database side effect | **PASS** | `app.py` obtains status via `get_database_status`; missing DB is reported as unavailable. New AppTest confirms the configured missing DB remains absent. Logging is directed to a temporary path in that test. |
| Typed configuration | **PASS** | `Settings` is a frozen, slotted dataclass with typed fields in `src/aegis/config.py`. |
| Safe default mode and invalid-mode rejection | **PASS** | `AppMode` contains only `RESEARCH_ONLY`; tests now cover the unset default and reject empty, `PAPER`, and `LIVE`. This is the current safety restriction, not the final product design. |
| Path handling | **PASS, bounded** | Relative paths resolve from `PROJECT_ROOT`; empty and traversal calendar paths are rejected. Database/log paths intentionally remain explicitly configurable, so validation always overrides them to temporary locations. |
| No accidental broker-order capability | **PASS** | No mode other than `RESEARCH_ONLY` exists in Phase 1 config; app startup renders research status and has no broker connection/order submission path. No network call was made. |
| No credential values in config output/errors | **PASS after correction** | A repro showed invalid `AEGIS_LOG_LEVEL=password=PHASE1_FAKE_SECRET` echoed the value. The error now names allowed levels without echoing input; a regression test covers this. A synthetic credential environment value is also verified absent from `repr(Settings)`. |
| Structured logging and common secret redaction | **PASS, bounded patterns** | `StructuredFormatter` emits JSON and redacts common API-key, secret, token, password and bearer patterns from messages and formatted exceptions. Added an exception-redaction regression test. This does not claim detection of every arbitrary secret format. |
| Sensitive-file tracking and ignore behavior | **PASS** | Read-only `git check-ignore --no-index` probes passed for `.env`, `.venv`, local DB/log, `secrets/`, `credentials/`, and downloaded market data; `.env.example` is not ignored. The only matching tracked environment file is `.env.example`, whose credential values are empty placeholders. |
| Disposable DB initialization/schema ordering | **PASS** | `test_fresh_database_initialization` and the focused suite initialize only `tmp_path` databases. Source schema is 18 with migration keys 1–17; runtime inspection confirmed contiguous keys and the fresh-init test verifies metadata versions 1–18. |
| Migration rollback and preservation | **PASS for exercised tests** | Existing disposable tests cover DDL/metadata rollback, retained Phase 1 records, repeat initialization and future-version rejection. The focused database module passed. No migration source was changed. |
| No normal DB access / no read-only inspection migration | **PASS** | No normal database was opened, initialized or migrated. App status inspection uses a read-only connection; the missing-DB AppTest confirms no implicit initialization. |
| Truthful startup/error status | **PASS for covered paths** | AppTest verifies `RESEARCH_ONLY`, unavailable/missing database status and `Not initialized` schema status without creating the DB. Unexpected status exceptions are logged and surfaced as unavailable. |
| Pytest configuration | **PASS** | `pyproject.toml` declares `testpaths = ["tests"]`. |
| Ruff lint | **PASS** | Repository-wide `ruff check .` passed. |
| Ruff formatting | **FAIL repository-wide; PASS for Phase 1 edits** | `ruff format --check .` reports six files outside this Phase 1 change set would be reformatted: the untracked master requirements document, `src/aegis/forward_sma.py`, the untracked M5-B2 task, `tests/test_forward_causal_order.py`, `tests/test_forward_sma.py`, and `tests/test_forward_sma_runtime.py`. Targeted format check for the four modified Python files passed. These files were not edited because they are outside scope. |
| MyPy | **PASS with defaults** | `mypy src` succeeded for 61 source files. There is no explicit `[tool.mypy]` section; the CI command uses MyPy defaults. |
| CI workflow | **UNVERIFIED** | `.github/workflows/ci.yml` was inspected and contains tests, lint, formatting, MyPy, Gitleaks and ignore checks. The workflow was not run. The local repository-wide formatter failure means the current checkout does not satisfy its format step with Ruff 0.17.4. |
| Secret scan | **UNVERIFIED** | CI config invokes `gitleaks/gitleaks-action@v3`, but Gitleaks is not installed locally and no actual secret scan or GitHub Actions run was performed. |
| No paid services | **PASS for this validation** | No paid service, API or dependency installation was used. The existing offline/mock dashboard tests were used. |

## 3. Tests executed

Safety review before running the requested modules found:

- `tests/test_database.py` constructs its DB fixtures under `tmp_path`.
- `tests/test_dashboard.py` overrides DB and log paths in its isolation fixture.
- The AI panel creates a provider only after form submission. The fake-key test does
  not submit the form and does not call the provider.
- No test in the selected modules calls a broker or external service.

Baseline, before the correction:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_project_scaffold.py tests/test_logging_config.py tests/test_database.py tests/test_dashboard.py
```

**Result:** exit 0, 57 passed in 23.22s.

After the Phase 1 correction and regression tests, with `AEGIS_MODE=RESEARCH_ONLY`,
database/log environment paths pointed under `%TEMP%`, and bytecode/cache writes
disabled:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_project_scaffold.py tests/test_logging_config.py tests/test_database.py tests/test_dashboard.py
```

**Result:** exit 0, 67 passed in 16.01s.

The new tests cover unsupported/empty modes, invalid log-level input redaction,
empty/traversal calendar paths, credential-free settings representation, exception
redaction, and missing/corrupt-database startup without creation or replacement. The
full test suite was deliberately not run because it includes unrelated later-phase
work.

## 4. Static checks and isolation

| Command | Result |
|---|---|
| `.\.venv\Scripts\python.exe -m ruff check .` | Exit 0 — All checks passed. |
| `.\.venv\Scripts\python.exe -m ruff format --check .` | Exit 1 — six out-of-scope files listed above; 182 other files already formatted. |
| `.\.venv\Scripts\python.exe -m ruff format --check src\aegis\config.py tests\test_project_scaffold.py tests\test_logging_config.py tests\test_dashboard.py` | Exit 0 — four Phase 1 Python files already formatted. |
| `.\.venv\Scripts\python.exe -m mypy src` | Exit 0 — success in 61 source files; MyPy emitted its standard note that untyped function bodies are not checked by default. |
| `.\.venv\Scripts\python.exe -m pip check` | Exit 0 — no broken requirements. |
| `git diff --check` | Exit 0 — no whitespace errors; Git printed line-ending conversion warnings for existing working-copy files. |

`git check-ignore` probes were run without creating any probe files. The corrupt-DB
startup test verified the existing temporary file's bytes were preserved. All other
test DBs/logs were temporary or were not created. Source schema inspection did not
query an application DB. No full CI, clean-environment installation, Gitleaks scan, or
standalone Streamlit server was run.

## 5. Defect and corrections

**Confirmed defect:** invalid `AEGIS_LOG_LEVEL` values were copied into the raised
`ValueError`, allowing arbitrary configured content—including a synthetic credential
string—to appear in configuration errors.

**Correction:** `src/aegis/config.py` now reports the accepted log levels without
echoing the supplied value. A regression test reproduces the previous leak and verifies
the secret string is absent. The focused Phase 1 suite passed after the change.

No schema, migration, broker, AI, execution or later-phase implementation was changed.

## 6. Files changed by this validation

- `src/aegis/config.py`
- `tests/test_project_scaffold.py`
- `tests/test_logging_config.py`
- `tests/test_dashboard.py`
- `README.md`
- `docs/reviews/PHASE_01_FOUNDATION_VALIDATION_2026-11-20.md` (this report)
- `docs/AEGIS_PHASE_GATE_REGISTER.md` (created during resumed validation)

No pre-existing M5 or other user changes were modified.

## 7. Unresolved issues

1. No dependency lockfile/constraints file exists, while the master requires reproducible
   environment lockfiles. The package is installed in the existing `.venv`, but a clean
   reproducible installation was not demonstrated. No lock format or dependency
   resolution was introduced without an approved mechanism.
2. The required repository-wide Ruff format check fails on six non-Phase-1 files. They
   were left untouched to preserve scope and user work.
3. GitHub Actions and Gitleaks were not executed; their actual results remain
   unverified.
4. No distinct independent reviewer has examined this implementation evidence.

## 8. Compatibility and status

The only behavior correction is the invalid-log-level error text; settings types,
function signatures, accepted modes, database APIs and schema are unchanged. The new
tests use synthetic values and temporary paths. `RESEARCH_ONLY` remains the current
operating restriction; the future AI-first PAPER/LIVE product requirements are
preserved and were not implemented or altered here.

**Implementation validation recommendation: `BLOCKED`.** The Phase 1 source defect is
corrected and its focused tests pass, but reproducible dependency installation and
repository-wide formatting/CI evidence remain unresolved. This report does not declare
independent `ACCEPT`; a distinct audit is required after the blockers are resolved.
No Phase 2 work is authorized by this report.

## 9. Resumed validation and register update — 2026-11-20

The resumed task began at the same `main` HEAD
(`0d6c65a69b94345fea170017128c632d52b5f9e9`). All pre-existing changes and the Phase 1
changes above were present and preserved. The v4 **“BUILD GUIDE FOR AI CODE
ASSISTANTS”** was not found by repository filename or content search; its instructions
are not inferred. No phase-register file existed at the start of this resumed task.

Created [`AEGIS_PHASE_GATE_REGISTER.md`](../AEGIS_PHASE_GATE_REGISTER.md) at the
master-specified path. It records `ACTIVE_PHASE: PHASE-01-FOUNDATION`, status
`BLOCKED`, no successor unlocked, and bounded statuses/limitations already present in
the repository audit trail. The register does not claim independent acceptance or
authorize broker access, credentials, forward-paper operation, or later-phase work.

The focused suite was rerun on this worktree:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_project_scaffold.py tests/test_logging_config.py tests/test_database.py tests/test_dashboard.py
```

**Result:** exit 0, 67 passed in 15.91s.

Ruff lint, MyPy, `pip check`, and `git diff --check` also passed on this resumed
worktree. Repository-wide `ruff format --check .` still reports the same six
out-of-scope files and exits 1 (184 other files already formatted). No application
source or tests were changed in the resumed follow-up; no database, broker, API,
credential, or paid service was accessed.

## 10. Completion addendum — 2026-11-20

This addendum records the later owner/Project Manager direction and the implementation
work performed after the historical sections above. Earlier status statements remain
historical evidence for their original run; this addendum is the current validation
result. It is not an independent acceptance.

### 10.1 Authority and scope

- `PHASE_01_FOUNDATION` remains the only active implementation gate.
- The approved future product is AI/LLM-led, with deterministic Python retaining
  independent authority over evidence validation, risk, authorization, routing,
  accounting, and reconciliation. Unified PAPER/LIVE gateways are future requirements,
  not current code or authority to trade.
- Current operation remains `RESEARCH_ONLY`. No broker connectivity, credentials,
  orders, forward-paper operation, schema migration, or later-phase implementation was
  authorized or performed.
- `CLAUDE.md`, `docs/PROJECT_STATE.md`, and
  `docs/ARCHITECTURE_DECISIONS.md` now contain dated addenda. Historical milestone
  statuses and acceptance evidence were retained, not rewritten. ADR-010 records the
  future product direction without changing active safety constraints.

### 10.2 Git baseline and preservation

At the start of this run:

- Branch/HEAD: `main` / `0d6c65a69b94345fea170017128c632d52b5f9e9`, ahead of
  `origin/main` by 25 commits.
- Index: no staged changes.
- Pre-existing modified tracked files: `README.md`, `src/aegis/config.py`,
  `src/aegis/database.py`, `src/aegis/forward_runtime.py`,
  `src/aegis/paper_equity.py`, `tests/test_dashboard.py`,
  `tests/test_logging_config.py`, `tests/test_paper_equity.py`, and
  `tests/test_project_scaffold.py`.
- Pre-existing untracked files: `docs/AEGIS_PHASE_GATE_REGISTER.md`,
  `docs/M5_MULTI_INSTRUMENT_RUNTIME.md`, `docs/M5_PORTFOLIO_PRETRADE.md`,
  `docs/PROJECT_AEGIS_MASTER_PRODUCT_AND_SYSTEM_REQUIREMENTS.md`,
  this validation report, `src/aegis/multi_instrument_runtime.py`,
  `src/aegis/portfolio_pretrade.py`, `tasks/M5_B2_PORTFOLIO_PAPER_EXECUTION.md`,
  `tests/integration/test_multi_instrument_runtime.py`,
  `tests/integration/test_portfolio_pretrade.py`,
  `tests/test_multi_instrument_runtime.py`, and
  `tests/test_portfolio_pretrade.py`.

All pre-existing changes were preserved. The Phase 1 task intentionally modified the
existing README, dashboard fixture, gate register, and validation report; formatting
was applied only to the six explicitly authorized files. No user file was reset,
deleted, staged, committed, or pushed.

Final Git state after implementation and checks:

```text
## main...origin/main [ahead 25]
 M .github/workflows/ci.yml
 M CLAUDE.md
 M README.md
 M docs/ARCHITECTURE_DECISIONS.md
 M docs/PROJECT_STATE.md
 M pyproject.toml
 M src/aegis/config.py
 M src/aegis/database.py
 M src/aegis/forward_runtime.py
 M src/aegis/forward_sma.py
 M src/aegis/paper_equity.py
 M tests/test_dashboard.py
 M tests/test_forward_causal_order.py
 M tests/test_forward_sma.py
 M tests/test_forward_sma_runtime.py
 M tests/test_logging_config.py
 M tests/test_paper_equity.py
 M tests/test_project_scaffold.py
?? .gitleaks.toml
?? docs/AEGIS_PHASE_GATE_REGISTER.md
?? docs/M5_MULTI_INSTRUMENT_RUNTIME.md
?? docs/M5_PORTFOLIO_PRETRADE.md
?? docs/PROJECT_AEGIS_MASTER_PRODUCT_AND_SYSTEM_REQUIREMENTS.md
?? docs/reviews/PHASE_01_FOUNDATION_VALIDATION_2026-11-20.md
?? requirements.lock.txt
?? src/aegis/multi_instrument_runtime.py
?? src/aegis/portfolio_pretrade.py
?? tasks/M5_B2_PORTFOLIO_PAPER_EXECUTION.md
?? tests/integration/__init__.py
?? tests/integration/test_multi_instrument_runtime.py
?? tests/integration/test_portfolio_pretrade.py
?? tests/test_multi_instrument_runtime.py
?? tests/test_portfolio_pretrade.py
```

HEAD remains `0d6c65a69b94345fea170017128c632d52b5f9e9`; the index remains empty.

### 10.3 Changes performed

- `pyproject.toml`: added `pip-tools` to the development extra with a Python/pip
  compatible version range.
- `requirements.lock.txt`: generated pinned Python 3.11 dependencies for the project,
  development, dashboard, research, and build requirements.
- `README.md`: documented locked installation, editable project installation, and the
  exact lock regeneration command.
- `.github/workflows/ci.yml`: installs the lock and editable project, keys pip cache on
  the lockfile, runs `pip check`, uses the Gitleaks configuration, checks the actual
  commit/PR diff for whitespace, and allows 30 minutes for the full job.
- `.gitleaks.toml`: narrowly allowlists two verified non-secret false positives by both
  exact path and line pattern; it does not exclude either file.
- `tests/integration/__init__.py`: gives integration tests a package namespace distinct
  from root test modules. This corrected pytest collection errors for duplicate module
  basenames without changing application or M-series runtime behavior.
- `tests/test_dashboard.py`: makes the existing isolated dashboard fixture copy the
  checked-in calendar JSON to `tmp_path` and use it explicitly, eliminating dependence
  on ambient environment state.
- `src/aegis/forward_sma.py`, `tests/test_forward_causal_order.py`,
  `tests/test_forward_sma.py`, `tests/test_forward_sma_runtime.py`,
  `docs/PROJECT_AEGIS_MASTER_PRODUCT_AND_SYSTEM_REQUIREMENTS.md`, and
  `tasks/M5_B2_PORTFOLIO_PAPER_EXECUTION.md`: mechanical Ruff formatting only.
- `CLAUDE.md`, `docs/PROJECT_STATE.md`, `docs/ARCHITECTURE_DECISIONS.md`, and
  `docs/AEGIS_PHASE_GATE_REGISTER.md`: dated policy/status addenda distinguish current
  implementation, approved future product, active Phase 1 gate, and current safety
  authority. Historical records remain intact.

No trading algorithm, financial calculation, risk rule, database schema, or migration
was changed by this task.

### 10.4 Formatting and semantic checks

Before formatting, `ruff format --check .` identified exactly the six files listed in
the assignment. Ruff diffs were inspected before applying formatting. The four Python
AST fingerprints (`ast.dump(..., include_attributes=False)`, SHA-256) were identical
before and after:

| File | AST SHA-256 |
|---|---|
| `src/aegis/forward_sma.py` | `a2dd3f76b6976f2a037a08aa6d463494eeaa2996e2840e529152c460ef7c78bc` |
| `tests/test_forward_causal_order.py` | `58d24de1e9184ff4d92021bc3633d5f1edba3ec03d10661e19df02c9e91555c4` |
| `tests/test_forward_sma.py` | `914689f626afe9a7dbd413c9504f20fbcf3ba18089726774b4bcffec7c4d9476` |
| `tests/test_forward_sma_runtime.py` | `ec99e76d5623140abdc6a57c63a2ea8856807ec21190b4f38f76981ba391e4cb` |

For the Markdown files, the formatter diffs changed only line wrapping/spacing in
Python code fences. AST comparisons of the changed master-requirements example,
portfolio orchestration stubs, and adapter signature were equivalent. No prose,
requirements, headings, tables, or normative content was reformatted.

Final repository-wide `ruff format --check .`: **PASS**, 191 files already formatted.

### 10.5 Dependency lock and fresh installation

Lock generation used pip-tools 7.6.2 in a temporary Python 3.11 environment:

```powershell
python -m piptools compile --resolver=backtracking --strip-extras --allow-unsafe --extra dev --extra dashboard --extra research --output-file requirements.lock.txt pyproject.toml
```

The generated lock pins direct/transitive and build/install tooling. It intentionally
contains no hashes: cross-platform hash coverage was not verified, and no such coverage
is claimed. Re-running the compile command produced an identical SHA-256 lockfile hash:
`B1B75240D105E81E1E2D209AD11798F30875723D2885DB43BBCE5E42C1C567DD`.

A new disposable Python 3.11.9 environment installed successfully using:

```powershell
python -m pip install -r requirements.lock.txt
python -m pip install --no-build-isolation --no-deps -e .
python -m pip check
```

Result: installation succeeded; editable `project-aegis 0.1.0` installed; imports of
`aegis`, NumPy, pandas, pytest, Ruff, MyPy, and Streamlit succeeded; `pip check`
reported no broken requirements. The Phase 1 focused tests also passed in this fresh
environment (see section 10.6).

An attempt to align the existing `.venv` to the lock was blocked because its
`.venv\Scripts\ruff.exe` was held open (Windows `WinError 5`, process ID 20544). I did
not terminate the process or replace the environment. The existing environment reports
no broken requirements, and its standalone Ruff executable still responds as version
0.17.4; however, `python -m ruff` is unavailable there after the interrupted install.
Quality checks below therefore use the fully installed temporary environment. This
existing-environment limitation remains outstanding.

### 10.6 Tests and quality verification

All tests used temporary databases/logs and `AEGIS_MODE=RESEARCH_ONLY`. The full suite
was run with external DNS/network connections blocked; no tests were excluded.

| Selection / check | Result |
|---|---|
| Phase 1: `tests/test_project_scaffold.py tests/test_logging_config.py tests/test_database.py tests/test_dashboard.py` | **67 passed**, 0 failed, 0 skipped. |
| Dashboard module after explicit temporary calendar isolation | **16 passed**, 0 failed, 0 skipped. |
| Formatted `tests/test_forward_causal_order.py tests/test_forward_sma.py` | **22 passed**, 0 failed, 0 skipped. |
| Full safe suite, before the dashboard fixture correction | **1,938 passed, 1 skipped, 13 failed** in 915.44 seconds. |
| `ruff check .` | **PASS** — all checks passed. |
| `ruff format --check .` | **PASS** — 191 files already formatted. |
| `mypy src` | **PASS** — 61 source files; standard note about untyped function bodies. |
| `pip check` in the fresh locked environment | **PASS** — no broken requirements. |
| `git diff --check` | **PASS** — Git reported only existing line-ending conversion warnings. |
| `.gitignore` probes | **PASS** for `.env`, `.venv`, local database/log paths; `.env.example` is not ignored. |

The full suite identified one dashboard test that depended on an ambient calendar path.
The fixture now uses a temporary copy of the checked-in calendar file, and the complete
dashboard module was rerun successfully (16 passed). The full suite was not rerun after
that narrowly scoped test-fixture correction.

Twelve full-suite failures remain in pre-existing later-phase work and were not altered:

1. Eleven `tests/test_forward_sma_runtime.py` cases fail before their intended validation
   because the pre-existing dirty `src/aegis/forward_runtime.py` now consults persisted
   `scheduling_only` configuration first. The invalid-input tests construct an
   uninitialized service; the missing-event test creates no `forward_runtimes` table.
   The relevant `forward_runtime.py` changes were already present at initial status and
   are outside the authorized Phase 1 behavior scope. Formatting the affected test file
   preserved its AST exactly.
2. `tests/test_portfolio_qualification.py::test_latest_schema_version_is_recorded_deterministically_and_readonly`
   expects schema versions 1–16, while the pre-existing dirty database source initializes
   versions through schema 18. No schema or later-phase test expectation was changed.

These are real full-suite/CI blockers in the current dirty worktree. They were not
silently skipped or weakened. The full-suite result is not a Phase 1 pass.

### 10.7 CI, secret scan, and protected-resource evidence

- CI configuration uses Python 3.11, installs `requirements.lock.txt` and the editable
  project without dependency re-resolution, and runs tests, Ruff lint/format, MyPy,
  `pip check`, Gitleaks, and `.gitignore` validation. `Tests and Code Quality` is the
  workflow job/check name.
- The workflow diff was manually inspected. A separate local YAML parse attempt could
  not run because PyYAML is not installed in the disposable validation environment;
  no standalone workflow-parser result is claimed.
- Hosted GitHub Actions was not run or observed. Changes remain uncommitted, and this
  task forbids pushing; no hosted pass is claimed.
- Local Gitleaks 8.30.1 was downloaded from the official release and its SHA-256 matched
  the official checksum file. The scan used a temporary snapshot of non-ignored
  repository files, excluding `.env`, local databases/logs, `.venv`, and Git internals.
  The final configured scan covered 198 files (about 4.13 MB) and reported zero
  findings after two narrow false-positive allowlists. This does not claim a scan of
  all Git history or a hosted action run.
- Tests ran with outbound DNS/network disabled; no broker endpoint or paid model
  provider was contacted. No real credentials were supplied to the tests.
- No normal/protected database was opened, initialized, copied, migrated, or modified.
  Database tests used temporary fixtures only. No schema migration was added.
- No paid service or broker operation occurred.

### 10.8 Final implementation status and next action

Formatting, dependency locking, fresh installation, Phase 1 focused tests, static checks,
local secret scanning, and governance reconciliation are implemented and verified.
However, the required broad CI/test gate is not green because of the twelve pre-existing
later-phase failures, the existing `.venv` refresh could not complete, and hosted CI is
unobserved. The phase register therefore remains:

```text
ACTIVE_PHASE: PHASE_01_FOUNDATION
STATUS: BLOCKED
```

No independent Phase 1 ACCEPT is claimed. Do not start Phase 2. The remaining code
failures require a separately authorized resolution of the existing forward-runtime and
schema-version test conflicts; after that, rerun the full suite, refresh the existing
environment once its Ruff process releases the executable, run hosted `Tests and Code
Quality`, and request a distinct read-only Phase 1 audit.
