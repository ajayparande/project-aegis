# Exchange Calendar Configuration

The checked-in `config/exchange_calendars.json` contains a `CONFIGURED` NSE cash-market
calendar candidate covering 2025-01-01 through 2026-12-31. It is not independently reviewed or
operationally accepted; 2027 and other exchanges/segments remain uncovered. Source URLs, circular
references, retrieval dates, verified hashes, and limitations are recorded in
[`EXCHANGE_CALENDAR_NSE_CASH_2025_2026_EVIDENCE.md`](EXCHANGE_CALENDAR_NSE_CASH_2025_2026_EVIDENCE.md).
Until a calendar is verified for its full requested range, session-based completeness is
provisional or unavailable and cannot authorize forward-paper entries.

The candidate uses official NSE Capital Market (Equities) evidence and 09:15-15:30 IST normal
market hours. Its 2026 Muhurat window does not model the instrument-specific Closing Auction
Session (CAS) schedule. It does not configure F&O, BSE, or inferred 2027 dates.

## Maintaining local calendars

Add one calendar record per exchange and market segment to the JSON file. A record contains:

- `exchange` and `segment`; use `*` only as an explicit exchange-specific fallback.
- `timezone`, defaulting to `Asia/Kolkata`.
- `confidence`: `CONFIGURED` for rules that have not been verified across their full range,
  or `CONFIRMED` only when the administrator has verified the source and completeness.
- Inclusive `coverage_start` and `coverage_end` dates in `YYYY-MM-DD` form.
- `source`, which should identify the official circular/archive or locally verified source and
  the administrator responsible for maintaining the record.
- `weekdays` using Python weekday numbering (Monday `0` through Sunday `6`).
- Regular `sessions`, each with 24-hour `open` and `close` values in `HH:MM` form.
- `holidays`, each containing a date and a human-readable reason/source note.
- Optional `special_sessions`, each replacing the regular schedule for its date. Set
  `closed: true` for a verified exceptional closure, or provide replacement sessions for an
  exceptional opening, break schedule, or early close.

This is the record shape; the angle-bracket values are placeholders and must be replaced with
verified local facts before the entry can be loaded:

```json
{
  "exchange": "<NSE-or-BSE>",
  "segment": "<verified-segment-or-asterisk>",
  "timezone": "Asia/Kolkata",
  "confidence": "CONFIGURED",
  "coverage_start": "<YYYY-MM-DD>",
  "coverage_end": "<YYYY-MM-DD>",
  "source": "<source and administrator/update responsibility>",
  "weekdays": [0, 1, 2, 3, 4],
  "sessions": [{"open": "<verified HH:MM>", "close": "<verified HH:MM>"}],
  "holidays": [],
  "special_sessions": []
}
```

Calendar definitions are exchange- and segment-specific. Entries do not fall through from NSE
to BSE. A wildcard segment only applies within the same exchange. The local JSON is transparent
and version-controlled; review updates like code and retain the cited source and covered date
range. Do not mark a record `CONFIRMED` based only on weekday rules or an incomplete holiday list.

The file root uses `schema_version: 1` and may configure `staleness_policy` with
`potential_after_missing` and `stale_after_missing`. These thresholds count completed expected
candles, not elapsed wall-clock time. Defaults are 1 and 3 respectively: fewer than the potential
threshold is `FRESH`; from the potential threshold up to (but not including) the stale threshold
is `POTENTIALLY_STALE`; at or above the stale threshold is `STALE`. Holidays and closed hours add
no expected slots.

Qualified direct and registered backtest execution, Strategy Lab runs, and forward-paper
qualification only load the path selected by application configuration
(`AEGIS_CALENDAR_CONFIG_PATH`, defaulting to `config/exchange_calendars.json`). Callers may pass
registry objects for API compatibility, but those objects are never authoritative: qualified
workflows reload the configured file and reject a registry from another path or with different
content. Execution callers cannot designate another JSON file as authoritative. The configured
location is an administrative selection boundary; the application rejects alternate request
paths, traversal, missing files, and paths that resolve through symlinks. SHA-256 identifies the
selected file's contents and detects changes; it does not establish approval. `CONFIRMED` remains
an administrative assertion, and neither it nor the hash proves official exchange-calendar
authority. The application trusts administrators controlling its configuration and filesystem.
The deployment must protect `AEGIS_CALENDAR_CONFIG_PATH` from untrusted process launchers; it is
read from the process environment and is not an authorization boundary against code that can
change that environment. Python cannot defend against malicious code already running with
equivalent process privileges.

## Timestamp and candle conventions

Imported candle timestamps remain UTC ISO text and denote the candle start. Intraday expected
starts retain Stage 3's 09:15 Asia/Kolkata alignment for 1m, 5m, 15m, and 1h intervals. Session
closes are exclusive. An interval that reaches or passes an early close is clipped to that close;
that final partial candle is complete only at the session close. Daily candles use the Stage 3
midnight Asia/Kolkata timestamp and complete at the last session close of that date. In-progress
candles are excluded from missing and freshness calculations. Intraday sessions before 09:15 are
not representable under the current Stage 3 timestamp validator and are rejected by analysis.

Session wall times are localized strictly. Ambiguous or nonexistent local times are rejected;
the engine does not guess daylight-saving folds. Multiple non-overlapping sessions represent
breaks in a trading date.

## Quality and scoring rules

The analyzer is read-only and does not rewrite candles or import provenance. It checks stored
decimal OHLC text and integer volume, scheduled starts, missing completed starts, unexpected
starts, identity duplicates, previously recorded duplicate/rejected import rows, and first/last
valid candle times. Rejected import rows with parseable timestamps are filtered to the requested
date range; rows without usable timestamps are counted separately because they cannot be assigned
to a date range.

For a fully covered, `CONFIRMED` calendar, completeness is the percentage of completed expected
candle starts that have a valid stored candle, rounded to two decimal places using decimal
arithmetic. No percentage is produced when the expected denominator is zero. A calendar that is
`CONFIGURED`, partially covered, unavailable, or not verified for the full requested range cannot
produce a confirmed completeness score. A partially covered `CONFIRMED` calendar can report
confirmed findings only on covered dates; uncovered dates are not called missing. Unverified
calendar findings are labeled provisional where rules are available; otherwise the report says
insufficient information. A requested range extending beyond `as_of` also receives no overall
completeness score for the unelapsed dates.

Overall status is deterministic: `ERROR` for invalid stored candles, confirmed unexpected
candles, or duplicate stored identities; `INCOMPLETE` for confirmed missing candles; `STALE`
when the completed-candle gap reaches the configured stale threshold; `COMPLETE` when the
confirmed denominator is nonzero and no such condition applies; otherwise
`INSUFFICIENT_INFORMATION`. Freshness uses the caller-supplied timezone-aware `as_of` instant and
the configured exchange schedule. Core logic never reads the machine clock.

This calendar layer does not validate exchange trading permissions, market-data licensing,
corporate-action adjustments, or official calendar completeness by itself. Source verification
and periodic updates remain the administrator's responsibility. Stage 4 adds no live data source
or broker connection.
