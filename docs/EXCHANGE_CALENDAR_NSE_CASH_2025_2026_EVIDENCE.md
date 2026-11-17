# NSE Cash-Market Calendar Evidence: 2025-2026

## Status and scope

This is the evidence record for the NSE Capital Market (Equities) calendar candidate in
`config/exchange_calendars.json`. The record is deliberately `CONFIGURED`, not `CONFIRMED`.
It is a candidate awaiting independent review; this document does not establish operational
calendar acceptance, market-data availability, or provider permissions.

Coverage is inclusive from 2025-01-01 through 2026-12-31. The calendar is for `exchange=NSE`,
`segment=NSE` only. It does not cover 2027, BSE, or derivatives. Weekend dates listed by NSE
remain in the holiday evidence; regular sessions still apply only Monday through Friday, except
where an explicitly configured special session overrides that rule.

All source materials below were retrieved on **2026-11-15**. SHA-256 values were computed from
the downloaded response/file bytes after validating the circular downloads as PDFs. They identify
the retrieved copies, not a publisher-signed digest.

## Annual holiday circulars

| Calendar year | NSE circular and reference | Publication date | Official PDF | Retrieved file SHA-256 |
|---|---|---|---|---|
| 2025 | `NSE/CMTR/65587`, Circular Ref. 170/2024 | 2024-12-13 | [CMTR65587.pdf](https://nsearchives.nseindia.com/content/circulars/CMTR65587.pdf) | `1e0f85615fbbcde9243dd6cafbbad4d9b581b8d2467fd9e27c0a4fe42a720c7d` |
| 2026 | `NSE/CMTR/71775`, Circular Ref. 172/2025 | 2025-12-12 | [CMTR71775.pdf](https://nsearchives.nseindia.com/content/circulars/CMTR71775.pdf) | `aa97e0afc0ce394097f2fc62631c68e3c2e4c7c23541ed35f21d9fc06b0dcacb` |

These annual circulars are retained as the original yearly schedules. Later sources and updates
are recorded separately below rather than substituted for the annual circulars.

## Subsequent official updates and session evidence

| Evidence | Publication / update date | Official source | Retrieved evidence SHA-256 |
|---|---|---|---|
| 2025 NSE holiday-master response, using only its `CM` rows | Retrieved 2026-11-15; the API row does not state a publication date or circular ID | [Trading holidays API, 2025](https://www.nseindia.com/api/holiday-master?type=trading&year=2025) | `39836991fe57220b1329452b8e5f3b264d4563238cea7939460529c328661189` |
| 2026 NSE holiday-master response, using only its `CM` rows | Retrieved 2026-11-15; the API row does not state a publication date or circular ID | [Trading holidays API, 2026](https://www.nseindia.com/api/holiday-master?type=trading&year=2026) | `ce67d605c3b638c37d85c1e021a0302161f7fa12e7971c1111edee59b8eeff16` |
| NSE holidays page, which loads the holiday data dynamically | Page marked updated 2026-11-11; retrieved 2026-11-15 | [Exchange Communication - Holidays](https://www.nseindia.com/resources/exchange-communication-holidays) | `4c85b5016a845692f291eaa2c63b0ba3d11b1d20aee39f762fdcbc72b7d77da3` |
| NSE current Equity normal/odd-lot market schedule | Page marked updated 2026-11-11; retrieved 2026-11-15 | [Market Timings](https://www.nseindia.com/market-data/market-timings) | `a5dec65614b6cd9f1b5e046a911141f849efa7c9cf804beb619895bd0d79e6ba` |
| 2025 cash-segment Muhurat special session | `NSE/CMTR/70319`, Circular Ref. 124/2025; published 2025-09-22 | [CMTR70319.pdf](https://nsearchives.nseindia.com/content/circulars/CMTR70319.pdf) | `19b7d88003cb05eb8c6abdc8d1bfad5df821a17e4f9ae36f75e327a68a48d258` |
| 2026 cash-segment Muhurat special session | `NSE/CMTR/76941`, Circular Ref. 149/2026; published 2026-10-19 | [CMTR76941.pdf](https://nsearchives.nseindia.com/content/circulars/CMTR76941.pdf) | `ce94ee1c54d98eccdf3c30fa0673ae2b6a49ea4ed4a72fb83d826f4359f71d58` |

The 2026 `CM` holiday-master rows include **2026-01-15, “Municipal Corporation Election -
Maharashtra.”** It is included as a subsequent closure in addition to the annual circular. The
retrieved API row supplies neither a separate circular identifier nor a publication date; neither
is inferred here. The API response contains multiple segments; only `CM` rows were used for this
cash-market calendar.

The current Market Timings page lists the Equity normal/odd-lot market open at **09:15** and
close at **15:30** IST. The regular schedule in the candidate uses only that interval; it does not
include pre-open, auction, closing-session, or other distinct market phases.

## Special sessions

- **2025-10-21:** Annual circular `NSE/CMTR/65587` lists the Diwali Laxmi Pujan date; subsequent
  cash circular `NSE/CMTR/70319` specifies the Normal Market session as 13:45-14:45 IST. The
  date remains in the holiday list and is separately represented as a special-session override.
- **2026-11-08:** Annual circular `NSE/CMTR/71775` lists the Diwali Laxmi Pujan date; subsequent
  cash circular `NSE/CMTR/76941` specifies the Normal Market window represented here as
  14:20-15:30 IST. That circular also distinguishes CAS securities, which have a separate
  schedule. The current schema has no instrument-specific CAS schedule, so this single-segment
  candidate cannot describe every security's session. This limitation is a blocker to operational
  acceptance.

The supplied [FAOP76940.pdf](https://archives.nseindia.com/content/circulars/FAOP76940.pdf) is an
F&O circular and is **not** used to set cash-session timings. Cash-session timing above comes
from the separate `NSE/CMTR/76941` circular.

## Evidence and acceptance limitations

- All listed NSE files/pages were retrieved and hashed; no source-file hash is fabricated. The
  hashes are reproducibility aids for those retrieved bytes and do not prove source completeness.
- The calendar remains `CONFIGURED`: source review and cross-checking do not establish completeness
  or certify the calendar for operational decisions.
- The NSE holiday page is dynamic. Its retrieved HTML did not itself contain the holiday rows; the
  separately retrieved official holiday-master response was inspected and hashed.
- The 2026 CAS distinction cannot be represented per instrument in the current calendar record.
- No 2027 dates are included or inferred. Requests outside the configured range remain uncovered
  and unavailable.
- This evidence does not establish market-data source permissions, data availability, or legal
  rights to use data.
