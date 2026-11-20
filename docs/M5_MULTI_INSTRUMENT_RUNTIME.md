# M5-A multi-instrument synthetic scheduling

Implementation scope: synthetic market data and authenticated scheduling only.
No milestone acceptance or operational readiness is asserted.

`_MultiInstrumentCoordinator` resolves a fixed, unique tuple of NSE cash-equity
symbols through the instrument registry and verifies the exact persisted runtime
membership. Create the existing `ForwardRuntimeService` with `scheduling_only=True`
and a new isolated synthetic account/database. M5-A requires
`max_candidates >= len(configured_instruments)` so peer arrival order cannot change
capped selection during recovery. Construction and coordinator `open` reject smaller
caps before ingestion or recovery writes. Resume through coordinator `open`.
No normal or continuous-pilot database is authorized for this demonstration.

The coordinator accepts at most 1000 observations per batch, in causal delivery
order. It delegates all quote validation, duplicate identity, sequence/time cursors,
calendar checks, aggregation and scheduler writes to the existing runtime. It does
not sort away late deliveries or implement another candle or scheduler engine.
Provider observations are rejected; provenance is DETERMINISTIC_SYNTHETIC /
SYNTHETIC_STREAM. Test calendar confirmation applies only to isolated fixtures.

Each instrument retains independent 1m, 5m and 15m candle state. Only elapsed
observed buckets complete; absent buckets are never synthesized. Existing F2 gap
eligibility can retain an observed completed candle while refusing its scheduler
event. A peer gap does not invalidate healthy instruments. The 12-minute example
completes 1m and 5m candles, leaving its final bucket unfinished; it does not
complete a 15m candle.

Scheduler identity remains runtime + instrument + interval + candle timestamp.
Existing exact one-use DB insertion and binding authorizations are preserved.
Persisted scheduler work remains PENDING/UNBOUND: it is evidence of an eligible
opportunity, never a strategy decision. No proposal builder or router is invoked.
On restart existing F1/F2/F3 authentication and recovery verify cursors and recover
missing eligible scheduler records without routing or inventing decisions.

The immutable, hashed `scheduling_only` configuration blocks public execution and
decision-evidence paths, skips protective monitoring and creates no paper marks.
Existing single-instrument execution and Upstox observation modes retain their
default behavior. Phase 8 remains the execution authority. No new execution engine,
dependencies, broker connection or credential access is added. Schema 18 adds a
null-safe identity-update guard for scheduler event ID, runtime ID, instrument ID,
interval, candle timestamp, decision timestamp, proposal ID and creation time.
Status/result updates and authorized proposal binding remain permitted. Existing
records are preserved; normal application databases are not migrated by this task.

The SMA builder enforces the existing execution-runtime restriction before history
evaluation or warm-up recording, including after restart. Scheduling-only calls
cannot return SMA proposals, resolve pending events or append decision records.

M4 reads the same persisted registry, quotes, candles, import provenance and
scheduler records. Its existing market rows identify each symbol, SYNTHETIC data,
latest completed 1m candle and freshness; no dashboard UI was redesigned. The
existing market panel does not show a per-interval candle/scheduler matrix.
No positions or trading P&L are fabricated for these instruments.

## Bounded evidence

Run with the existing virtual environment:

```powershell
.\.venv\Scripts\python.exe -m pytest --import-mode=importlib -q tests/test_multi_instrument_runtime.py tests/integration/test_multi_instrument_runtime.py
```

Importlib collection is needed because the requested unit and integration filenames
are identical. All fixtures use temporary database, calendar and log paths and
block socket connections. The example includes RELIANCE, TCS and INFY, an INFY
missing minute, duplicate and late delivery, restart and final reconciliation.

| Instrument | Accepted quotes | Completed 1m | Completed 5m | 1m events | 5m events |
|---|---:|---:|---:|---:|---:|
| RELIANCE | 12 | 11 | 2 | 11 | 2 |
| TCS | 12 | 11 | 2 | 11 | 2 |
| INFY | 11 | 10 | 1 | 9 | 1 |

The INFY gap retains observed evidence but blocks incomplete scheduling coverage.
Recovery tests interrupt before/after scheduler persistence and restart between
observations; replay must preserve identical candle/event evidence. Authorization
and single-instrument recovery regressions remain in `test_forward_runtime.py`.
No multi-stock strategy, allocation, paper execution or real-market use is included.

## Validation evidence

Implementation-run focused regression: 158 passed across forward runtime,
continuous pilot/recovery and Upstox stream tests. This includes existing exact
one-use scheduler authorization, binding and F1/F2/F3 checks. New M5-A selection:
10 passed in 34.15 seconds, including exact gap counts, price isolation and restored
cursors. Ruff lint and format checks passed for the four
Python files; focused MyPy passed for both production modules. The full suite was
not run. No normal database, live connection, credentials, commit or push was used.

## Audit remediation validation

All three audit corrections are implemented, pending separate independent acceptance.
The M5-A unit/integration selection passed 28 tests. Recovery comparisons include
quotes, completed candles, event identities/statuses/bindings, decision journal rows,
non-restart health evidence and instrument cursors. Only event creation wall time,
the recovery marker and restart-only health records are excluded from comparisons.
Changing peer ranks at caps 3 and 5 and interruptions before/after scheduler writes
match uninterrupted controls. Caps 1 and 2 are rejected with unchanged database dumps.

The strengthened schema-17 upgrade/rollback check passed separately, comparing all
pre-existing table records and enforcing identity immutability after upgrade.
The expanded database/runtime/equity/recovery/dashboard selection had 229 passes
and one existing legacy-import failure under importlib mode. Both F2/F3 equity
checks passed when rerun with default pytest import mode (2 passed). Importlib mode
is necessary only for the duplicate M5-A unit/integration filenames above.
Focused Ruff/format checks passed for six Python files; MyPy passed for the three
production modules; `git diff --check` passed. No full suite was run.

M5-A fixtures retain zero paper orders/fills and blocked network connections.
Execution-enabled regression fixtures separately exercise existing synthetic Phase 8
orders and fills. No normal database or broker was accessed. M5-B remains unstarted;
this remediation report does not grant independent acceptance or operational approval.
