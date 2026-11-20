# M3 real historical data qualification — 19 November 2026

Verdict: **PARTIALLY IMPLEMENTED**. Reporting is improved and tested. The real
historical SMA simulation remains blocked at the existing qualification boundary.
No calendar or source-use status was promoted, and no real strategy decisions or
financial outcomes are claimed.

## Calendar evidence

The application-selected `config/exchange_calendars.json` was reloaded through
`load_approved_calendar_registry`. Its NSE/NSE record covers 2025–2026, uses
Asia/Kolkata, and remains `CONFIGURED`. A configured file selected by the
application is not the same thing as a confirmed exchange schedule. File hashes
establish identity; they do not confer calendar authority.

Official content reviewed through the web reader:

- [NSE/CMTR/71775](https://nsearchives.nseindia.com/content/circulars/CMTR71775.pdf),
  published 12 December 2025, is the Capital Market 2026 holiday circular. The
  target date, 18 November, is not among its listed holidays. That observation
  alone cannot establish the absence of subsequent exchange updates.
- [NSE market timings](https://www.nseindia.in/static/market-data/market-timings)
  lists the general equity normal/odd-lot window as 09:15–15:30. It is not dated
  security-specific evidence that RELIANCE traded continuously throughout it.
- [NSE/CMTR/76941](https://nsearchives.nseindia.com/content/circulars/CMTR76941.pdf),
  published 19 October 2026, distinguishes CAS and non-CAS securities for the
  8 November special session. It specifies a 15:15 continuous-market close for
  CAS securities and a 15:30 close for non-CAS securities. The configured single
  segment/special-session record cannot represent that distinction.
- [NSE's CAS overview](https://www.nseindia.in/static/products-services/closing-auction-session)
  identifies the relevant operational circulars, including NSE/CMTR/74466. The
  dated CAS eligibility and applicable schedule for RELIANCE on 18 November still
  require reproducible security-specific evidence and administrative review.

The indexed official CAS history result indicated a RELIANCE record for the
sample date, but the opened dynamic page returned no records. This is an
indication requiring verification, not a confirmed dated security master.
Direct bounded downloads of the annual/special-session PDFs, CAS circular and
holiday-master response did not yield usable local responses during this review.
Previously recorded source-file hashes were not recomputed or represented as
freshly verified. The existing evidence document also records the subsequent
15 January closure, whose complete update chain remains part of calendar review.

There is no automatic evidence-to-confirmation workflow in the inspected source.
All 19 dates in the annual 2026 circular are represented in the configuration.
The additional configured closure is 15 January, attributed to the subsequent
holiday-master update. This set comparison does not establish that every later
exchange update is present; `calendar-comparison.json` records its exact scope.

`CONFIRMED` is an administrative assertion. Neither generic weekday assumptions
nor this implementation task supplies the missing approval or CAS representation.
The checked-in calendar was left byte-for-byte unchanged.

## Saved Upstox data

Only the isolated research database and collection artifacts from the verified
Upstox session were inspected. Connectivity was not repeated. The existing
collection-store verifier authenticated its normalized files and manifest.

- Provider/instrument: Upstox, RELIANCE, `NSE_EQ|INE002A01018`.
- Instrument registry: NSE cash EQUITY, segment NSE, ISIN INE002A01018, lot size 1,
  registered decimal tick size 0.1.
- Interval/range: one minute, 18 November 2026; 375 rows, 09:15–15:29 IST.
- Retrieval timestamp: 19 November 2026, 12:00:23.735609 UTC.
- Existing official V3 historical provider, saved invocation and HTTP evidence
  identify the one-day historical request. HTTP 200 was not treated as permission.
- Import 1 is `COMPLETED`. Its CSV checksum matches the saved CSV. Every imported
  timestamp, decimal OHLC value and integer volume matches the normalized batch.
- The saved canonical parsed candle-array fingerprint matches the import metadata.
  Original HTTP response bytes were not archived; the fingerprint is neither a
  raw-wire checksum nor publisher-signed proof.
- No invalid OHLC, negative/noninteger volume, duplicate identity, duplicate import
  row, rejected row, or missing minute relative to the provisional template was
  found. Fourteen provider-supplied candles have zero volume. They were preserved;
  their presence does not establish continuous-session eligibility during CAS.

## Eligibility and execution

`analyze_dataset` reports 375 provisional expected candles and 375 observed
candles. Confirmed expected coverage and completeness percentage remain null;
quality is `INSUFFICIENT_INFORMATION`, freshness is `INSUFFICIENT_DATA`. Historical
assessment uses an explicitly labelled 18 November end-of-day cutoff, not a claim
that these candles are fresh for live trading on 19 November.

Source-use status is `UNVERIFIED`, with zero declaration-history records. The
existing `declare_import_source_use_status` mechanism requires an explicit
operator/admin declaration, identity and rationale. M1B also validates the latest
matching declaration, timestamp, import status, checksum and contributing candle
bindings. Provider identity and technical integrity do not substitute for that
declaration or determine data-use rights.

The existing M1B qualification attempt returned these report codes:

- `CALENDAR_NOT_CONFIRMED`
- `HISTORICAL_QUALITY_NOT_COMPLETE`
- `DATA_FRESHNESS_NOT_ESTABLISHED`
- `SOURCE_USE_UNVERIFIED`

SMA decisions remain `NOT_EVALUATED`. Simulated orders, fills and rejected/unfilled
orders are zero. Starting capital is configured as ₹10,000; it remains unchanged.
No fees were incurred and no realized trading P&L was generated. The costs,
one-share sizing, 10% volume participation, next-bar execution, affordability,
tick validation and long-only rules were not changed.

## Implementation and verification

Changed `upstox_research_session.py` to expose existing analyzer findings: confirmed
versus provisional denominators, configuration hash, coverage, timestamps,
integrity counts, bounded reasons, assessment time, freshness and unfilled counts.
No qualification rules or execution engines were added or relaxed.

Extended `tests/integration/test_upstox_connectivity_session.py`. All **6 focused
tests passed**, including qualified synthetic next-bar financial reconciliation,
missing-candle refusal, and refusal to score a provisional calendar. Synthetic
test declarations apply only to disposable fixtures, never to the real import.
Ruff lint, formatting, focused MyPy and Git whitespace checks passed.

Sanitized local artifacts are under
`C:/Users/ajayp/AppData/Local/Temp/aegis-m3-qualification-cwn_jq44/`:
`dataset-qualification.json`, `official-nse-evidence.json` and
`qualification-pytest.xml`. The original research database and configured calendar
content hashes were checked unchanged after analysis.

Remaining boundary: obtain complete applicable calendar/CAS evidence and its
administrative confirmation, and an explicit legitimate permitted-use declaration
for the real import. Until then the real historical simulation is unavailable.
`RESEARCH_ONLY` is preserved. Zero real broker orders, no normal/pilot database
changes, no dependencies installed, and no commits or pushes.
