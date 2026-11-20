# M3 final calendar and source review — 19 November 2026

Verdict: **PARTIALLY IMPLEMENTED**. The review packages are prepared. No
administrative calendar confirmation or permitted-use declaration was submitted.
The real M1B historical simulation remains blocked. This report does not grant
approval and is not a replacement calendar trust system.

## Exact dataset and evidence binding

The saved import contains 375 RELIANCE one-minute candles for **18 November 2026
only**, from 09:15 through 15:29 Asia/Kolkata. Identity is NSE cash EQUITY,
`NSE_EQ|INE002A01018`, registered segment NSE. Source import 1 is `UNVERIFIED`;
its declaration count is zero, and no latest declaration exists.

The imported CSV SHA-256 is
`e6a62440a6605688a24d1bc41b8d3c378982bdefd682f266c9128da555e91b38`.
Provider metadata links the canonical parsed candle-array fingerprint
`54badb688138eefbe72de4e8ccd8b9fc754a71ba73e32f3784424de35b486592`.
The previous collection-store, CSV and SQL comparison remains recorded in
`M3_DATA_QUALIFICATION_2026-11-19.md`. These identities establish which data is
under review; they do not establish permission to use it.

## Calendar applicability and administrative decision

The existing application-selected calendar remains `CONFIGURED`, for NSE/NSE,
Asia/Kolkata, covering 2025-01-01 through 2026-12-31. Its generic normal window is
09:15–15:30. The preceding review matched all 19 annual 2026 holiday dates and
identified the additional configured 15 January closure. The target date is not
an annual listed holiday or configured special session; completeness of later
updates must still be established.

Official evidence reviewed:

- [NSE/CMTR/76941](https://nsearchives.nseindia.com/content/circulars/CMTR76941.pdf)
  applies to **8 November**, not 18 November. Its CAS and non-CAS closing times
  differ. It cannot by itself authorize the imported day's schedule.
- [NSE CAS FAQ, updated 3 September 2026](https://nsearchives.nseindia.com/web/mediaattachment/2026-10/FAQs_on_Closing_Auction_Session_CAS_in_the_CM_and_changes_in_FO_Segment_ver3_20261023220345.pdf)
  describes the regime effective from 3 August 2026, identifies CAS eligibility
  through exchange security-master information, and gives the CAS window as
  15:15–15:35. It does not say all cash securities have identical closing rules.
- [NSE CAS 2.0 FAQ](https://nsearchives.nseindia.com/web/mediaattachment/2026-11/FAQs_for_CM_and_FO_FOR_CAS_2.0_20261109152618.pdf)
  expressly applies from **7 December 2026**. Its later continuous-session rules
  must not be applied retrospectively to 18 November.
- [NSE CAS history](https://www.nseindia.in/historical-data/cas) has an indexed
  RELIANCE row for 18 November, but the opened dynamic page did not reproduce
  that row. The indexed result is corroboration, not a retained dated security
  classification or a complete update chain.

The correct class-specific assessment must distinguish a CAS continuous close
from a non-CAS continuous close. The current generic NSE record and registered
segment do not make that selection. Existing calendar machinery can distinguish
explicit segment mappings, but no authenticated, date-specific RELIANCE mapping
or administrative approval is supplied by this review. Provider bars during the
auction also need an approved treatment under the existing M1B schedule and fill
semantics; matching 375 generic minute slots is insufficient.

The inspected repository has an administrator-controlled configuration loader,
not an authenticated evidence-approval service. Its content hash is an identity
check, not administrative consent. No administrator identity or approval time was
invented. Confirmation of the entire 2025–2026 registry cannot be inferred from
reviewing one imported day.

## Source-use terms and declaration draft

Public provider evidence supports the proposed kind of use:

- [Current Upstox Historical Candle V3 documentation](https://upstox.com/developer/api-documentation/v3/get-historical-candle-data/)
  describes historical OHLC analysis and the official request format used here.
- [Upstox historical API product guidance](https://upstox.com/market-talk/introducing-uplink-api-version-2/)
  expressly describes historical APIs for backtesting. This is older product
  guidance, not this account's current accepted agreement.
- [Upstox general terms](https://upstox.com/terms-of-use-and-privacy-policy/)
  include use, user-responsibility and reproduction conditions. They do not
  supply this user's accepted API terms, retention permission or exchange-use
  entitlement record.

The intended use is local private SMA(2,3) research with ₹10,000 nominal cash,
without redistribution or live orders. Public product descriptions and the
previously authorized account access are relevant evidence, but they are not the
explicit audited declaration required by Aegis. No account-specific terms
acceptance or named permitted-use attestation was found in the saved records.
This review neither determines legal rights nor asserts that the use is forbidden.

`source-use-declaration-draft.json` names the existing
`declare_import_source_use_status` contract and binds the intended use to import
1 and the checksum above. The proposed future status is `VERIFIED_PERMITTED`,
but the package is **DRAFT_NOT_SUBMITTED**, not ready for submission. The operator
identity and approval time are null; the rationale is explicitly a draft requiring
permission review. No consent, timestamps or declaration-history rows were made up.

## Implementation, qualification and validation

`upstox_research_session.py` now reports contributing import identities, CSV
hashes, declaration counts and latest declaration status. Its source-evidence
query is restricted to the requested dates. An unrelated unverified import must
not be presented as a source for the requested historical snapshot. The existing
M1B boundary still performs all authoritative declaration and execution checks.

The real qualified-boundary attempt remains `BLOCKED`: calendar not confirmed,
quality/freshness not established under a confirmed applicable schedule, and
source use unverified. It generated no BUY, SELL, HOLD or NO_DECISION observations,
orders, fills, open positions or trading P&L. Nominal capital remains ₹10,000;
no fees were incurred. No synthetic results substitute for this real-data result.

All **19 focused tests passed**. Coverage includes explicit CAS/non-CAS synthetic
calendar mappings, provisional calendars and special sessions, required auditable
source declarations, latest matching declaration history, and M1B blocked and
qualified paths. Synthetic fixture approvals apply only to disposable tests.
Ruff, formatting, focused MyPy and Git whitespace checks passed.

Review-only artifacts are under
`C:/Users/ajayp/AppData/Local/Temp/aegis-m3-final-evidence-dgu2jd72/`:

- `calendar-confirmation-review.json`
- `source-use-declaration-draft.json`
- `real-qualification-result.json`
- `focused-pytest.xml`

The original research database and configured calendar content hashes remain
unchanged. No broker connectivity was repeated, no credentials were read or
included, and no normal/pilot database was changed. `RESEARCH_ONLY` and zero real
broker orders are preserved. No dependencies, commits or pushes were introduced.
