# Project Aegis — Phase 4 Final F-06 Re-Audit

**Review date:** 2026-11-09
**Review type:** Strictly read-only independent technical re-audit
**Target:** Latest Phase 4 backtesting implementation and F-06 remediation
**Branch / HEAD:** `main` / `ecbd7dad9c91b48b996d55c51cc833bd1dc23b5b`
**`origin/main`:** `ecbd7dad9c91b48b996d55c51cc833bd1dc23b5b`
**Operating mode:** `RESEARCH_ONLY`
**Read-only declaration:** No application source, test, config, database, log, migration, or existing documentation file was modified. The only repository change created by this audit is this report.

## 1. Scope and audit method

This re-audit reviewed the final Phase 4 implementation in:

- `src/aegis/backtesting.py`
- `src/aegis/strategy_lab.py`
- relevant database schema/migration behavior
- `tests/test_backtesting.py`
- the previous acceptance audit and current worktree state

The review specifically checked:

- F-06 configuration integrity, snapshot validation, relational integrity, and legacy compatibility
- the prior accepted F-01 through F-05 findings remain intact
- the backtesting safety invariants and public regression paths
- the exact validation commands required by the Phase 4 acceptance criteria

## 2. Starting Git and protected-file baseline

### Git baseline

- Branch: `main`
- HEAD: `ecbd7dad9c91b48b996d55c51cc833bd1dc23b5b`
- `origin/main`: `ecbd7dad9c91b48b996d55c51cc833bd1dc23b5b`

### Starting `git status --short`

```text
 M AGENTS.md
 M docs/DEVELOPMENT_ROADMAP.md
 M docs/SYSTEM_ARCHITECTURE.md
 M docs/VALIDATION_AND_ACCEPTANCE.md
 M src/aegis/database.py
 M src/aegis/market_data.py
 M src/aegis/market_quality.py
 M src/aegis/strategy_lab.py
 M tasks/PHASE_04_BACKTESTING.md
 M tests/test_database.py
 M tests/test_market_data.py
 M tests/test_project_scaffold.py
 M tests/test_strategy_lab.py
?? docs/reviews/
?? src/aegis/backtesting.py
?? tests/test_backtesting.py
```

The pre-existing worktree changes were preserved. The earlier final acceptance report remained present at `docs/reviews/PHASE_04_FINAL_ACCEPTANCE_AUDIT.md`.

### Protected-file hashes at start of audit

- `data/aegis.db`: `0AF4CE61875B3B65DBB1D2F6C355787F39123F0C00E006070CC5145FA7AFD890`
- `logs/aegis.log`: `73E76DC2E1BB7688A03473FD6D81136F529FD06752452F5879085D7CA2C2E07C`

## 3. F-06 detailed findings

### Decision: F-06 = RESOLVED

The latest `list_backtests()` implementation in `src/aegis/backtesting.py` closes the earlier F-06 defect by validating the full registered-backtest record before returning it. The checks are not superficial: they validate the canonical configuration document, stored configuration SHA-256, required snapshot fields, nested strategy specification, specification hash, strategy configuration hash origin, relational references, and the referenced Strategy Lab experiment.

### A. Configuration integrity

The implementation now enforces the current contract on registered backtests:

- Registered backtest records are required to have a valid JSON object under `configuration_json`.
- `list_backtests()` parses the JSON and rejects malformed JSON with an experiment-specific `BacktestError`.
- Unsupported schema or unsupported schema version values raise an experiment-specific `BacktestError`.
- Missing or malformed configuration hash is rejected, including: stored hash is `None`, length != 64, non-hexadecimal contents, or a hash mismatch against the canonical JSON.
- The canonical representation is recomputed via `_canonical_hash(configuration)` and compared against the stored `configuration_sha256`.
- The mismatch path raises `BacktestError` naming the affected experiment ID.

This is directly exercised by tests such as:

- `test_list_backtests_reports_malformed_registered_records`
- `test_list_backtests_rejects_missing_malformed_or_incorrect_configuration_hash`

### B. Strategy snapshot validation

`list_backtests()` now validates the snapshot beyond the mere presence of keys. It checks:

- `strategy_experiment_id` is a positive integer and not a bool
- `strategy_id` is a positive integer and not a bool
- `hypothesis_id` is `None` or a positive integer and not a bool
- the nested `strategy_spec` is parseable and canonical
- `strategy_spec_sha256` is valid and matches the canonical `StrategySpec.to_dict()` hash
- `strategy_configuration_sha256` is valid 64-character lowercase hex
- `strategy_configuration_hash_origin` is a non-empty string
- required snapshot fields (`exchange`, `trading_symbol`, `interval`, `start_date`, `end_date`) are non-empty strings
- the snapshot is equal to the canonical snapshot produced by `_load_strategy_experiment()` for the referenced Strategy Lab experiment

This is directly covered by tests including:

- `test_list_backtests_rejects_invalid_snapshot_identifier_types`
- `test_list_backtests_rejects_missing_required_snapshot_value`
- `test_list_backtests_rejects_invalid_nested_strategy_specification`
- `test_list_backtests_rejects_noncanonical_nested_strategy_specification`
- `test_list_backtests_rejects_invalid_snapshot_metadata`
- `test_list_backtests_rejects_snapshot_identity_or_version_mismatch`

### C. Relational integrity

The code verifies the relational links and the snapshot-to-DB consistency:

- `_load_strategy_experiment()` fetches the linked Strategy Lab row and validates it with `_validate_experiment_configuration()` before building the snapshot.
- `list_backtests()` rejects a registered record when `strategy_id` or `hypothesis_id` are missing or mismatched with the snapshot.
- A snapshot that does not match the referenced Strategy Lab experiment raises an experiment-specific `BacktestError`.
- A missing referenced Strategy Lab record or invalid configuration is rejected as a backtest listing error rather than silently skipped.

See the relational-integrity tests:

- `test_list_backtests_rejects_nonexistent_strategy_experiment`
- `test_list_backtests_rejects_snapshot_identity_or_version_mismatch`
- `test_list_backtests_mixed_valid_and_malformed_records_fails_all_or_nothing`

### D. Legacy compatibility

The implementation preserves the documented legacy behavior without weakening the current integrity contract:

- Legitimate legacy rows with `status != 'BACKTEST_REGISTERED'` and `strategy_id IS NULL` continue to be listed only if they are valid legacy records or typed null-strategy records that intentionally pass the compatibility path.
- A malformed legacy record that cannot be parsed is skipped only when it is not a registered backtest and does not match the expected backtest document type.
- Registered backtests are not treated as legacy merely because they predate a hash-status contract; they are still required to satisfy the current SHA-256 and asset-snapshot validations.
- Unrelated null-strategy records remain excluded from the backtest list, as in the null-strategy handling tests.

This is explicitly covered by:

- `test_list_backtests_preserves_unrelated_null_strategy_record_handling`
- `test_list_backtests_preserves_legacy_typed_null_strategy_record`
- `test_list_backtests_mixed_valid_and_malformed_records_fails_all_or_nothing`

## 4. Legacy compatibility findings

The current implementation makes the compatibility boundary explicit and safe:

1. Registered backtests created under the current contract must pass the current configuration and snapshot validation.
2. Legacy rows created before the hash/status contract are not rejected solely because they predate the contract, provided their record remains structurally valid for the listing fallback path.
3. Unrelated null-strategy rows are still skipped unless they match the backtest document contract.
4. Malformed records fail closed and raise `BacktestError` when they are registered backtests or when they trigger the typed-backtest path.
5. Mixed valid and malformed sets do not silently hide a failure; the loop raises on the first malformed registered record and preserves deterministic ordering.

This matches the acceptance criteria for F-06 and the currently documented legacy policy.

## 5. Test-quality assessment

The test suite directly exercises the production listing path rather than a fake or bypassed mock path. The key coverage includes:

- valid canonical registration
- invalid strategy experiment ID type
- invalid hypothesis ID type
- invalid strategy name/version/types in nested spec
- missing required snapshot fields
- invalid strategy specification and spec hash
- invalid config hash, malformed hash, missing hash
- malformed JSON and unsupported schema
- mixed valid and invalid records
- legacy and unrelated null-strategy records
- relational mismatch between row values and snapshot values
- experiment-specific `BacktestError` assertions
- no database writes during listing

The tests are not superficial: they mutate the real SQLite rows in temporary databases and assert on the exact `BacktestError` messages and experiment IDs rather than only checking that an exception occurred.

## 6. F-01 through F-05 regression results

### F-01: insufficient SMA history no longer misleads the user

**Status:** REMAINS RESOLVED.

The public backtest path still blocks insufficient history before the simulation is treated as successful. The implementation relies on the strategy specification minimum-history requirement and rejects insufficient candle history with explicit diagnostics.

### F-02: Strategy Lab registration does not corrupt listing

**Status:** REMAINS RESOLVED.

The current code continues to keep backtest registrations separate from Strategy Lab experiment listings while preserving relational links and validating hash/snapshot integrity.

### F-03: exact instrument tick-size provenance remains preserved

**Status:** REMAINS RESOLVED.

The engine continues to require active instrument records and enforces tick-size-aware fill logic without inventing a different provenance model.

### F-04: source-use declaration and status enforcement remain intact

**Status:** REMAINS RESOLVED.

The review confirmed the code still enforces the documented status gates and does not treat a declaration as a blanket legal permission.

### F-05: Decimal context independence and precision regression remain resolved

**Status:** REMAINS RESOLVED.

The public backtest and market-quality code paths remain insensitive to caller Decimal context in the reviewed implementation, and the earlier precision regression is not present in the current code path.

## 7. Backtesting safety regression review

The current implementation remains consistent with the documented research-only execution model:

- signal generation is based on completed candle closes (`COMPLETED_CANDLE_CLOSE`)
- fill timing is next eligible candle open (`NEXT_ELIGIBLE_CANDLE_OPEN`)
- no look-ahead or future-candle leakage is introduced by the inputs or execution flow
- all-or-none fill behavior remains explicit
- open positions are not silently liquidated
- benchmark remains the same-instrument buy-and-hold model, not a claim about Nifty 50 or any benchmark index
- fee, tax, spread, slippage, tick rounding, and cash accounting remain deterministic and internally consistent under the reviewed tests

This is a regression check only; no redesign was introduced, and no execution or calculation logic was altered during this audit.

## 8. Exact validation results

The following commands were run using the existing project virtual environment and the repository state at the start of the audit.

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\ruff.exe format --check .
.\.venv\Scripts\mypy.exe src\aegis
git diff --check
```

### Actual results

- `pytest -q`: `254 passed in 31.50s` (exit code 0)
- `ruff check .`: `All checks passed!` (exit code 0)
- `ruff format --check .`: `43 files already formatted` (exit code 0)
- `mypy src\aegis`: `Success: no issues found in 9 source files` (exit code 0)
- `git diff --check`: no diff-check warnings beyond documented CRLF worktree warnings; no patch errors (exit code 0)

## 9. Final Git and protected-file state

### End-of-audit `git status --short`

The audit did not modify the working tree beyond creating this report. The worktree still includes the same pre-existing modified and untracked files. The only new report created by this audit is:

- `docs/reviews/PHASE_04_F06_FINAL_REAUDIT.md`

### Protected-file hashes at end of audit

- `data/aegis.db`: `0AF4CE61875B3B65DBB1D2F6C355787F39123F0C00E006070CC5145FA7AFD890`
- `logs/aegis.log`: `73E76DC2E1BB7688A03473FD6D81136F529FD06752452F5879085D7CA2C2E07C`

These match the values recorded at the start of the audit. Branch, HEAD, and `origin/main` remained unchanged.

## 10. Remaining limitations and conditions

This code-level review is positive, but the following operational conditions remain in force:

- The checked-in exchange calendar still has no verified official coverage claims.
- The local database may still be on an older schema relative to a production-ready operational setup.
- Source-use declarations and real-data provenance are still not sufficient to approve live-data backtesting.
- Real-data backtesting remains blocked pending verified calendar coverage, schema migration readiness, and source-use/data-quality requirements.

These are operational readiness constraints, not code defects in the current synthetic research-only implementation.

## 11. Acceptance decisions

### F-06

**RESOLVED**

### F-01 through F-05

- F-01: **RESOLVED and still valid**
- F-02: **RESOLVED and still valid**
- F-03: **RESOLVED and still valid**
- F-04: **RESOLVED and still valid**
- F-05: **RESOLVED and still valid**

### Phase 4 code-level acceptance

**APPROVE WITH CONDITIONS**

Condition: the implementation is approved for the current research-only Phase 4 code path and deterministic synthetic validation, with the explicit note that operational real-data backtesting remains blocked by the verified calendar, source-use, and data-quality requirements.

### Operational readiness

**NOT READY FOR APPROVED REAL-DATA BACKTESTING**

The current code and tests are not a substitute for a verified trading calendar, complete data provenance, and source-use declarations required for live or permitted real-data execution.
