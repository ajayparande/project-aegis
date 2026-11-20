# P10-P5 — Continuous Synthetic Paper-Trading Pilot

## Scope
Research-only, deterministic, offline synthetic execution.
Initial simulated capital: INR 10,000.
No live orders, broker credentials, external APIs or paid services.

## P10-P5A — Durable bounded runner
- Use a dedicated pilot SQLite database.
- Preserve the existing one-off synthetic pilot.
- Support bounded quote batches and explicit resume.
- Persist deterministic provider progress through the runtime cursor.
- Never silently recreate an existing runtime or account.
- Fail closed on configuration mismatch, corruption or halt.

## P10-P5B — Continuous decisions
- Generate completed candles through the existing runtime.
- Use authenticated SMA proposals after sufficient warm-up.
- Route proposals through the existing execution boundary.
- Record HOLD, FILLED and REJECTED outcomes separately.
- Preserve chronological event processing.

## P10-P5C — Recovery
- Restart between batches.
- Verify no duplicated orders or fills.
- Verify scheduler recovery and persisted cursor continuity.
- Reconcile the account before and after restart.

## P10-P5D — Extended sessions
- Use explicit synthetic calendar coverage.
- Do not fabricate confirmed real-market calendars.
- Verify session boundaries and rejected out-of-session quotes.
- Ensure synthetic timestamps and source-import timestamps remain eligible.

## P10-P5E — Acceptance
- Deterministic replay produces equivalent financial results.
- Reconciliation is RECONCILED.
- No duplicate fills or unauthorized orders.
- No silent recovery from integrity halts.
- Final report records all checks and failures.
- P10-P4 conditional acceptance remains separately tracked.
