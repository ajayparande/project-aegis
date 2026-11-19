# Read-only paper trading dashboard

Launch from the repository's existing Python environment:

```powershell
python -m aegis.trading_dashboard
```

The default URL is `http://127.0.0.1:8501`. Select an explicitly authorized,
existing SQLite file in the sidebar, or supply it when launching:

```powershell
python -m aegis.trading_dashboard --database "C:\path\to\isolated-paper.db" --port 8511
```

Both `--database` and `--port` are optional.

No application database is selected implicitly. Missing files and unsupported
schemas produce explanatory empty states. The dashboard never creates a database,
initializes a schema, migrates, repairs, submits proposals, executes orders, opens
a broker connection, or starts runtime recovery. It serves on loopback with
Streamlit usage telemetry disabled. `RESEARCH_ONLY` configuration is required.
The existing research application is unchanged.

## Views and scope

The eight tabs are Overview, Live Market, Open Positions, Trade History,
Performance Analytics, Strategy & AI Decisions, Risk & System Health, and
Execution Journal. The dedicated launcher applies a dark navy terminal theme
using official per-launch Streamlit theme flags. No global `.streamlit` settings
or theme configuration for the separate research application are changed.

| Page | Purpose |
| --- | --- |
| Overview | Simulated account KPIs, equity, daily P&L and recent transactions. |
| Live Market | Timestamped persisted quotes, provenance, freshness and feed evidence. |
| Open Positions | Reconciled current holdings and eligible valuation marks. |
| Trade History | Filterable simulated orders/fills and sanitized CSV export. |
| Performance Analytics | Verified period returns, drawdown and realized attribution. |
| Strategy & AI Decisions | Decisions, NO_DECISION outcomes, proposals and authenticated execution links. |
| Risk & System Health | Risk limits, halts, reconciliation and qualification status. |
| Execution Journal | Chronological records with stable evidence references and CSV export. |

Select a paper account explicitly. Financial summaries and positions cover that
account; the runtime selector scopes market observations, decisions, runtime
events and the corresponding verified equity series. Equity analytics require
exclusive runtime ownership of the account. Multiple runtimes sharing one account
produce UNAVAILABLE analytics rather than a synthetic allocation between sessions.
Account-wide orders remain account-wide and retain their runtime IDs.
Unrelated accounts and historical M2 portfolios are never merged into an equity
curve. Historical observations retain timestamps and provenance; synthetic
fixtures are labelled separately from real observations. A persisted RUNNING
state does not establish a currently connected feed.

Today, This Week (Monday start), This Month, This Year, Custom Date Range and All
Time use `Asia/Kolkata` boundaries. Custom ranges require both dates and include
the entire end date. Exact UTC microseconds preserve midnight boundary behavior.
Period filters apply to activity, realized P&L, fees, decisions, health events and
realized performance charts. Open positions remain current/as-of snapshots.

Refresh defaults to 15 seconds using a supported Streamlit fragment. Manual,
5, 15, 30 and 60 seconds are available. Rolling date boundaries are recomputed
on refresh. Tables page 100 rows at a time, through offset 10,000. Symbol,
action and status trade filters apply before pagination. CSV exports contain
the displayed page only, with secret redaction and spreadsheet-formula escaping.

## Terminal presentation (M4 Phase 3)

The presentation layer is in `trading_dashboard_ui.py`; accounting, read models,
schema 17, verification, source/calendar policy and execution logic are unchanged.
Launch through `python -m aegis.trading_dashboard` to apply the dashboard-local
theme. The palette uses navy `#0B1120`, sidebar `#111C30`, cards `#17243A`, borders
`#293B55`, blue/purple accents, green gains and red losses. Zero and unavailable
P&L remain neutral. There are no speculative change arrows or fabricated deltas.

The compact branded header identifies research-only simulated paper use, disabled
live orders, the selected portfolio/session, active IST reporting period, refresh
interval and latest persisted evidence time. That evidence timestamp is distinct
from the view's refresh clock. Native eight-page tabs have scoped icons and active
states. Account/session/date controls remain in the sidebar; database paths and
advanced trade filters/pagination are collapsible.

Overview emphasizes eight KPI cards and verified equity/daily P&L charts.
Performance adds the existing period-return and sampled-drawdown evidence, with a
red drawdown plot below zero. Chart tooltips show IST observations; temporal axes
are explicitly UTC. Currency axes and tables identify INR. Equity gaps use
unconnected points; missing daily observations remain missing. Drawdown plotting
is a display projection of the existing verified curve, gated by the existing
drawdown metric; it never overwrites analytics or becomes accounting authority.

Native tables retain sorting, record IDs, all evidence columns and the original
sanitized CSV downloads. Monetary grid cells use a separate numeric presentation
copy for alignment and sorting, displayed to two decimals; exports retain the
unchanged canonical source precision. BUY/SELL, P&L and evidence statuses have
consistent colors. Wide evidence tables scroll inside their own frames.

System indicators describe persisted facts. In particular, RUNNING remains a
neutral runtime record, STALE is amber, and absent data remains UNAVAILABLE.
CONFIGURED calendar evidence and UNVERIFIED source declarations are not promoted
to healthy or eligible states. Reconciliation/equity verification use green only
when the read model supplies the corresponding verified result.

CSS is static and scoped to Aegis components and Streamlit's supported container
keys, with semantic ARIA tab selectors. It never uses generated DOM class names.
Dynamic HTML values are escaped, CSS does not interpolate record values, and
Streamlit HTML JavaScript execution remains disabled. Responsive cards and native
column layouts support desktop, laptop and narrow windows. Visual verification
uses the installed Edge browser and isolated copied synthetic records.

UI-focused checks:

```powershell
python -m pytest -q tests/test_trading_dashboard.py tests/test_trading_dashboard_ui.py
python -m ruff check src/aegis/trading_dashboard.py src/aegis/trading_dashboard_ui.py tests/test_trading_dashboard_ui.py
python -m ruff format --check src/aegis/trading_dashboard.py src/aegis/trading_dashboard_ui.py tests/test_trading_dashboard_ui.py
python -m mypy src/aegis/trading_dashboard.py src/aegis/trading_dashboard_ui.py
git diff --check
```

## Accounting and evidence limits

The reader uses the existing pure Phase 8 reconciliation and cash-balance
calculations on a SQLite read-only transaction (`mode=ro`, `query_only=ON`). It
does not invoke the mutating reconciliation service. Failed reconciliation hides
authoritative position/equity claims. Completed-trade statistics aggregate
persisted realized P&L through reconciled completed position cycles; partially
closed cycles remain open.

Cash, initial capital, lifetime realized P&L, selected-period realized P&L and
modeled fees are supported. Position value and gross unrealized P&L require a
fresh, permitted persisted mark linked to an accepted quote for the same account,
instrument, import, timestamp and price. Otherwise valuation and current equity
are unavailable. Equity includes paid fees, before hypothetical exit costs.
No liquidation price is inferred.

Equity-based period P&L/return now requires verified persisted equity snapshots
with defensible opening and closing boundaries. The Overview and Performance
Analytics tabs consume the same session-scoped read model. The equity curve shows
irregular observed events in causal sequence; it does not assert continuous or
uniformly sampled prices. Daily net P&L requires actual confirmed session-close
checkpoints. Other activity charts remain explicitly labelled **realized trade
P&L**. Daily risk opening bases are not midnight or closing-equity evidence.
Missing strategy, model, stop or target evidence remains unavailable. No AI
origin or execution is inferred from a proposal. NO_DECISION remains intact.

Decision integrity checks authenticate displayed decisions and their audit hashes
using the existing journal reader. Execution links additionally require matching
consumed authorization and execution-snapshot evidence. The displayed integrity
status is page-scoped, not a full-run recovery or independent acceptance audit.
The chronological journal keeps source kinds and stable IDs; time proximity is
not causal proof. Reconciliation, halt and recovery events appear where persisted.
Current source/calendar qualification remains visible without promoting evidence
or preventing display of permitted synthetic research records.

## Data and query bounds

Sources are existing `paper_accounts`, `paper_orders`, `paper_fills`,
`paper_positions`, `paper_marks`, `paper_ledger_entries`, `paper_realized_pnl`,
`paper_daily_state`, `paper_order_events`, `paper_execution_snapshot_uses`,
`instruments`, `data_imports`, `forward_runtimes`, `forward_quotes`,
`forward_runtime_candles`, `forward_runtime_imports`, `forward_scheduler_events`,
`forward_trade_controls`, `forward_execution_authorizations`, and
`decision_journal_events/audits/health`, and `paper_equity_snapshots`.
The administrator-approved local calendar
is read independently of historical decision claims.

Full account financial verification is limited to 1,000 orders and 100 open
positions; market projections inspect at most 50 configured instruments. SQLite
work is interrupted after 2.5 million VM steps or a two-second deadline checked
by the progress handler. Oversized, inconsistent or unsupported evidence produces
an unavailable state rather than an unverified partial financial total. Equity
verification additionally limits a session to 4,000 snapshots and 1,000 fills.
The dashboard itself performs no migrations or index changes.

## Persisted equity evidence (schema 17)

The additive version 16-to-17 migration creates `paper_equity_snapshots` and its
scope/time index. Update, delete and replacement triggers enforce append-only
behavior. Existing accounts, fills, journals and historical records are retained.
The migration deliberately creates **no historical snapshots**. Normal and pilot
databases must not be migrated without explicit operation-specific authorization.

Each snapshot stores a stable SHA-256 identity, account ID, session ID, sequence,
event key/kind, UTC timestamp and integer microseconds, IST date, valuation and
reconciliation statuses, previous fingerprint, canonical JSON and its fingerprint.
The versioned payload includes cash, copied reconciled position quantities and
cost evidence, valuation marks and timestamps, market value, equity, cumulative
fees and realized P&L, gross/net unrealized P&L, gross/net trading P&L, accounting
policy, account-configuration fingerprint, financial-evidence IDs/digest, journal
references and any session-close calendar evidence. Monetary values are canonical
decimal text; binary floating point is used only for chart and numeric-grid presentation.

`paper_equity.py` is an evidence sidecar, not a financial ledger or execution
engine. It reads the existing Phase 8 pure reconciliation and cash functions.
Creation points are:

- Account creation and an unused forward account's runtime inception, within the
  existing transaction. Inception cannot be backfilled after financial activity.
- Completed fills, after consumed authorization and mandatory execution-journal
  evidence are written, in the same financial transaction.
- Accepted quote mark updates on open positions, in the quote/mark transaction.
- Explicit `record_equity_checkpoint` calls at the trusted accounting boundary.
  `session_close=True` additionally requires the exact final close from an
  administrator-approved CONFIRMED calendar for every runtime instrument.

Snapshot failure rolls back its own savepoint; it never leaves partial evidence
or blocks a protective financial exit. Failure of the enclosing accounting
transaction rolls back both financial records and snapshots. Restart/replay keys
prevent duplicates. A missing committed-fill snapshot is a coverage failure even
if a later mark/checkpoint exists. Recording failure does not authorize a repair,
backfill or financial replay. Analytics fail closed while Phase 8 retains its
existing F1/F2/F3 recovery and risk behavior.

Cash-only reconciled snapshots need no quote. Open positions require fresh,
positive, permitted marks with no future observation/receipt, bound to the
instrument, source import and an accepted quote or consumed Phase 8 execution
snapshot. Forward valuation currently supports the deterministic synthetic policy;
real Upstox observation cannot become synthetic or executable paper evidence.
Unknown/incompatible provenance and unqualified sources fail closed. Historical
marks are checked against durable referenced observations, not today's mutable
latest-mark projection. Stream import completion watermarks may advance without
retroactively invalidating earlier accepted quotes. No missing mark is replaced
by zero or an entry price.

## Metric definitions and boundaries

For each complete reconciled snapshot:

```text
equity = cash + eligible position market value
gross unrealized P&L = sum(quantity * (mark - average entry price))
net unrealized after paid entry fees = gross unrealized - remaining entry fees
net trading P&L = realized net P&L + net unrealized after paid entry fees
total equity change since inception = equity - initial capital = net trading P&L
gross trading P&L = net trading P&L + cumulative modeled fees
```

Fill prices already include the engine's spread/slippage/tick modeling. Those
costs must not be subtracted again. Realized net P&L retains Phase 8's entry-fee
allocation and sell-fee semantics. Decimal computations use 50-digit precision.
Only cross-checks involving repeating average-cost identities allow a maximum
INR 1e-38 representation residual; cash, fees, quotes, source records and
fingerprints remain exact. External deposits/withdrawals are not supported
by the existing paper ledger. Unexplained differences or an unsupported cash-flow
declaration fail verification; neither is inferred to be a deposit.

Period net P&L is closing equity minus opening equity; return is this change
divided by positive opening equity. Opening must be authenticated inception within
the period, an exact midnight checkpoint containing strictly prior financial
activity, or a previous confirmed session close with no missing intervening
trading session or subsequent pre-boundary activity. An arbitrary first event
inside the period is never substituted for opening equity.

A completed historical range requires actual confirmed closing checkpoints for
its required trading dates. A current interval uses the latest verified event
and explicitly reports its effective closing timestamp; open valuations must
remain fresh and match the latest persisted mark. All Time describes persisted
session coverage, not invented days after its last observation. A stale current
valuation, missing/incomplete opening or close, or required calendar gap makes period
returns unavailable. Existing sessions without snapshots remain unavailable until
new lifecycle evidence exists; a new checkpoint cannot manufacture inception or
missing fill history.

Daily net P&L/equity change use two successive confirmed session closes, or true
inception on that same session's first day. Weekends/holidays are skipped only
where confirmed calendar coverage proves closure. No missing trading day is padded
with a zero. An inception-day return is labelled partial-day and excluded from
Sharpe sampling.

Cumulative net return uses authenticated inception and closing equity. Maximum
drawdown in INR and percent measures peaks/troughs over complete observed event
snapshots in the selected interval (including its opening basis). It is a sampled
drawdown, not a claim about unobserved intraday extremes. Intermediate incomplete
valuations make drawdown unavailable; they do not invalidate P&L between two
independently verified endpoints with complete financial coverage. Sharpe requires at least
20 consecutive complete close-to-close daily returns, complete confirmed-calendar
sampling and nonzero sample variance; it uses a documented zero risk-free rate
and 252-session annualization. Irregular event snapshots are never Sharpe samples.

Read-only verification checks identity, hash chain, ordering, account/session
continuity, immutable configuration, source fill/snapshot/journal/authorization
references, cash/fee/realized aggregates, quantity and cost-basis identities,
valuation quotes and session-close evidence. Contradictory, orphaned or tampered
snapshots cannot contribute to verified metrics. Fingerprints and source-reference
checks detect inconsistency; they are not signatures against an attacker who can
rewrite the entire SQLite database and all its trust evidence.

## Current qualification limits and troubleshooting

M3 calendar/source qualification remains blocked; synthetic dashboard verification
does not establish real-market readiness. Missing boundary or valuation evidence
keeps affected metrics UNAVAILABLE. Sharpe requires the qualified daily series
described above; a successful dashboard render does not satisfy that requirement.

- **Missing database:** supply an authorized existing file through `--database` or
  the sidebar. The dashboard will not create, initialize or migrate it.
- **Port occupied:** choose another port, for example
  `python -m aegis.trading_dashboard --port 8514`.
- **Stale market data:** check the persisted quote timestamp and provenance.
  Refreshing the view does not fetch quotes or make old observations fresh.
- **Empty dashboard:** check the selected database, account/session, date range,
  trade filters and record page. Try All Time and page 1. Absent records remain
  empty; legacy sessions without equity snapshots cannot gain historical evidence
  through dashboard reads. Do not recreate or migrate protected databases to
  clear an empty state.

## Independently checked synthetic example

The existing isolated SMA/runtime fixture starts with INR 10,000, then buys one
unit at the Phase 8 modeled fill of INR 100.20 and sells at INR 99.80. Modeled fees
total INR 0.30. Expected final cash/equity is:

```text
10000 - 100.20 + 99.80 - 0.30 = 9999.30
gross trading P&L = -0.40
realized/net trading P&L = -0.70
net return = -0.007%
```

Actual fill snapshots, intermediate quote valuations, restart/replay, confirmed
synthetic close checkpoints and dashboard reads are tested against these totals.
This is synthetic software verification, not real-market performance evidence.

## Focused validation

```powershell
python -m pytest -q tests/test_paper_equity.py tests/test_trading_dashboard.py
python -m pytest -q tests/test_database.py tests/test_paper_trading.py
python -m ruff check src/aegis/paper_equity.py src/aegis/performance_analytics.py src/aegis/trading_dashboard.py src/aegis/trading_dashboard_data.py
python -m ruff format --check src/aegis/paper_equity.py src/aegis/performance_analytics.py src/aegis/trading_dashboard.py src/aegis/trading_dashboard_data.py
python -m mypy src/aegis/paper_equity.py src/aegis/performance_analytics.py src/aegis/trading_dashboard.py src/aegis/trading_dashboard_data.py
git diff --check
```

Tests use disposable existing synthetic-runtime fixtures and actual Phase 8
simulated fills, plus Streamlit AppTest rendering. They do not touch normal or
pilot databases, connect to brokers, install dependencies or run a full suite.
