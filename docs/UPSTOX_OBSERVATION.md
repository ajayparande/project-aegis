# Bounded Upstox observation and historical simulation

`python -m aegis.upstox_observation` uses the existing V3 transport and persisted
observe-only forward runtime. The default is `OFFLINE_TEST`. `RESEARCH_ONLY` is
mandatory; `PAPER_RESEARCH` remains rejected. This command exposes no order,
portfolio, funds, holdings, login, or token-refresh API.

The initial real connection is limited to **60 seconds**, one explicitly mapped
NSE cash equity, 300 accepted observations, 10,000 frames, and at most three
reconnects. Duration uses a monotonic deadline with socket cancellation. Each
connection obtains a fresh single-use authorized URL and resubscribes to LTPC.
Ctrl+C and CLI termination close the transport and preserve a sanitized report.

Official protocol references reviewed during implementation:

- [Market Data Feed V3](https://upstox.com/developer/api-documentation/v3/get-market-data-feed/)
- [Market Data Feed Authorize V3](https://upstox.com/developer/api-documentation/get-market-data-feed-authorize-v3/)

## Local use

Use the existing virtual environment. Choose a **new, nonexistent directory** for
every run. Existing roots, symlinks/junctions and the continuous-pilot directory
are rejected; databases are never reused or repaired by this command.

```powershell
.\.venv\Scripts\python.exe -m aegis.upstox_observation --root "$env:TEMP\aegis-observe-offline-001"
```

The offline fixture reuses the existing synthetic quote generator, calendar and
forward aggregation. Its calendar is explicitly `CONFIRMED_TEST_ONLY`; it proves
no real connection or market-data eligibility.

A real session requires a local JSON instrument specification with these fields:
`instrument_key`, `exchange`, `trading_symbol`, `instrument_name`,
`instrument_type`, `segment`, `isin`, `tick_size`. Supply the operator-verified
values; the key must be `NSE_EQ|<ISIN>`, match the registered ISIN, and identify an
active cash equity. `tick_size` must be exact decimal **text**, never a JSON float.
There is no automatic instrument lookup. Local credentials load from the root
`.env` only when an explicitly approved real invocation requests them.

After reviewing that mapping and the approved application-selected calendar:

```powershell
.\.venv\Scripts\python.exe -m aegis.upstox_observation --mode OBSERVE_ONLY --root "$env:TEMP\aegis-observe-real-001" --instrument-config .\local-instrument.json --approve-market-data-only
```

This uses `UPSTOX_ACCESS_TOKEN` from root `.env`, with process environment taking
precedence. No Upstox API key or secret is needed. There is no interactive token
prompt: missing or malformed credentials produce a sanitized blocker. Never paste
credentials into chat or arguments. An explicit `--token-env <variable-name>`
selects a process variable instead; its presence grants no approval. Approval is
never persisted. Root `.env` is Git-ignored, never included in reports, and never
copied into the virtual environment. Literal assignments with optional quotes
and comments are supported; shell expansion/interpolation is not performed.
`--approve-market-data-only` is the preferred equivalent spelling for an already
authorized invocation. It skips confirmation phrases and grants market-data
access only. `--approve-observe-only` remains a compatibility alias. Neither
flag enables ingestion, strategy execution, order placement, or permanent approval.
Noninteractive `--confirm` without explicit approval returns
`BLOCKED_FOR_AUTHORIZATION` and makes no provider call.

`BrokerCredentials` accepts `ZERODHA_API_KEY` and `ZERODHA_API_SECRET` as optional
local configuration. A Zerodha access token is not required to initialize it.
Zerodha connectivity stays disabled; this module has no Zerodha client or login
automation. Future activation requires a valid session obtained through the
[official Kite Connect login and token-exchange flow](https://kite.trade/docs/connect/v3/user/)
and its own connectivity authorization. Arbitrary environment tokens cannot
activate Zerodha. `RESEARCH_ONLY` and disabled live-order settings are validated.

Default real connectivity is diagnostic only: it decodes market status, initial
snapshots and subsequent LTPC messages, including closed-market observations,
without ingesting them as qualified quotes. Snapshots are never live evidence.
The current checked-in `CONFIGURED` calendar stays provisional. Optional
`--ingest-observations` requires an administrator-approved `CONFIRMED` calendar
and the existing normal-open, freshness, tick and runtime validation gates.
Ingested observations remain `UNVERIFIED`, with no strategy or paper execution.

## Historical retrieval and qualification

For the same instrument, optionally supply `--historical-from YYYY-MM-DD`,
`--historical-to YYYY-MM-DD`, and `--data-use-reviewed` after reviewing account
permissions. The date range must span one to seven calendar days. The existing
Upstox historical HTTP client and verified-resume collection store handle the
single bounded request. Provider authorization or entitlement failure is reported
without response bodies. No historical request occurs without the separate
data-use-review flag.

Normalized source files and the provider candle-array SHA-256 are preserved under
`historical/`, alongside the CSV import projection. `research.db` is a separate
new database. Canonical Decimal prices, import checksums and source fingerprints
remain linked. Duplicates are rejected by the existing provider validator;
missing completed candles remain missing and are reported by the existing
quality analyzer. No candle is fabricated or silently repaired.

Every import stays `UNVERIFIED`. A successful request, token, or data-use-review
flag never promotes it to `VERIFIED_PERMITTED`. The command attempts qualification
and reports blockers with zero simulated fills. It provides no automatic rights
declaration or calendar-promotion option.

Historical execution delegates to the existing **M1B qualified historical
executor**, using causal next-bar semantics, SMA(2,3), one share, 10% volume
participation, starting cash `10000`, and existing default research costs. Results
are always `HISTORICAL_SIMULATION`. The Phase 8 forward freshness and authorization
boundary is preserved: old historical quotes are not passed into forward paper
execution. The synthetic SMA integration independently exercises Phase 8.

## Evidence and interpretation

`report.json` is atomically created. It contains bounded lifecycle counters,
bucketed latency summaries, calendar confidence, final disconnect reason,
zero-execution verification, and separate authentication, handshake, observation,
data-quality and trading-readiness statuses. It excludes tokens, authorized URLs,
raw frames, instrument/account identifiers and unnecessary filesystem paths.

Every stream checkpoint checks the existing observation-only configuration,
empty scheduler/authorization/order/fill/mark/position/ledger tables and reconciled
unchanged cash. A violation terminates the session and preserves the evidence.
Historical simulation results are distinct from this stream invariant.

Injected transports report `INJECTED_TRANSPORT`; offline reports cannot establish
real authorization, a real WebSocket handshake or real LTPC delivery. A successful
handshake without subsequent LTPC messages establishes connectivity only. Latency
quantiles are fixed histogram bucket upper bounds, not exact sample quantiles.

The integration test retrieves 30 synthetic candles through the mocked existing
HTTP client, preserves an unverified blocked attempt, then explicitly audits only
that disposable fixture. The existing executor produces one BUY and one SELL at
the following bars: two fills, fees `0.30`, ending cash `9999.30`, realized P&L
`-0.70`, no open position, three warm-up `NO_DECISION` observations and 25 HOLDs
after evidence is available. This is synthetic verification, not real market
performance. A missing bar still blocks execution after source qualification.

Actual Upstox authentication, handshake, entitlements and delivery require the
operator-approved invocation and locally supplied credential/mapping. No real
provider connection is implied by the offline evidence.
