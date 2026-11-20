# M5-B1 synthetic portfolio allocation and pre-trade risk

**M5-B1 does not authorize paper or real orders.** It produces immutable research
reports for NSE:INFY, NSE:RELIANCE and NSE:TCS in canonical symbol order. The
application remains RESEARCH_ONLY. No broker, network, execution, scheduler binding
or decision-journal writing path is introduced. Implementation is not independent
acceptance or operational readiness.

## Inputs and outputs

The private `portfolio_pretrade` module accepts `_PortfolioPretradeRequest`:
evaluation ID/time, account/runtime IDs, the fixed universe, available cash, explicit
M2 sleeve budgets, exactly one typed BUY/SELL/HOLD intent per instrument, evidence
locators, and Phase 8 risk/cost contracts with a policy fingerprint. BUY may request
a positive integer quantity or `None` for bounded whole-share sizing. SELL requires
a positive integer quantity; HOLD requires zero. Floats, bool quantities, duplicate
intents, naive timestamps and mutable intent/budget containers are rejected.

Evaluation rebuilds the entire nested request before database access or allocation:
each intent, evidence locator, M2 budget, risk contract and cost contract re-enters its
authoritative constructor. Actions and identities must be exact strings; quantities
must be exact integers (not bool, float or Decimal). Altering a frozen object with
`object.__setattr__` does not preserve its validity. Valid nested values are detached
from the caller rather than shared across the evaluation boundary.

`_PortfolioPretradeResult` and its per-instrument decisions are frozen dataclasses
containing only immutable scalars, frozen contracts and tuples. Canonical JSON uses
sorted keys, normalized Decimal text and UTC timestamps. The report includes action,
requested/approved quantities, modeled price/notional/costs, hypothetical reserved
and remaining cash, explicit reason codes, evidence fingerprints and aggregate counts.
Its fingerprint is not an authorization token. There is no executable proposal or
order conversion method.

Before return, every instrument decision and the aggregate result are reconstructed
and validated again. Validation rejects mutable containers, unsupported actions/statuses,
noninteger quantities, non-Decimal/nonfinite money and invalid fingerprints. It checks
intent identity, modeled fee consistency, sleeve/portfolio cash conservation, counts,
notional/exposure totals, risk bounds and canonical result fingerprint consistency.
Malformed producer output raises an integrity error rather than returning an approval.

## Trusted evidence boundary

`_evaluate_synthetic_portfolio(database_path, request)` is the thin integration from
persisted M5-A evidence to the report. It never opens/advances an M5-A runtime. The
coordinator loads one SQLite read-only transaction for the entire portfolio; it never
calls execution-enabled proposal builders, public mutating reconciliation, runtime
recovery/readiness, order submission or protective monitoring.

Evidence references are locators only: runtime, instrument, accepted quote ID and
completed 1m candle time. No caller price, quality flag, source-use flag or supplied
evidence hash can grant eligibility. The loader independently verifies:

- Schema 18; runtime configuration/state checksums; exact fixed registry membership,
  active NSE cash equity, lot size one, synthetic scheduling-only mode and uncapped
  membership capacity.
- Persisted account identity, halt state, Phase 8 read-only reconciliation, matching
  risk/cost configuration and exact available cash.
- Both the original account-inception chain and the current runtime equity chain,
  using M4's existing read-only equity verifier on the same SQLite transaction.
  The small inception adapter permits the original account-only chain under its
  authenticated current runtime owner; it does not change the public M4 session
  verification boundary or implement separate financial accounting. Original funding,
  account proofs, snapshot hashes/sequences, complete reconciled inception records
  and exclusive session ownership must agree. Proof hashes enter report evidence.
- Latest accepted quote/cursor/last-price agreement, healthy feed, timestamps and
  freshness. Old references cannot silently select an earlier healthy observation.
- Exact synthetic import identity/hash, instrument/interval, metadata and permitted
  synthetic declaration. This does not establish permission for real market data.
- The existing F2 completed-candle provenance/observation checks and exact
  PENDING/UNBOUND scheduler event. Gap/late completion evidence remains unavailable.
- The administrator-selected calendar, CONFIRMED coverage and session eligibility.
  The calendar is reloaded after evaluation to detect configuration changes. No
  caller registry or status override is accepted; no calendar is promoted.

Missing, stale, future, mismatched or unsupported evidence returns
UNAVAILABLE_EVIDENCE for the affected intent. Peers can still receive bounded
reports because this milestone supports only an authenticated flat account; it
never invents a price for an existing position. Portfolio-wide context failures
make all intents unavailable.

## M2 allocation adapter

`PortfolioPolicy`, `SleeveCashAllocation`, `CapitalAllocation` and the existing exact
paise converters define the cash contract. The thin adapter splits integer paise
in canonical frozen symbol order; `CapitalAllocation` independently validates the
equal split and remainder. INR 10,000 yields 3333.34 / 3333.33 / 3333.33.

Idle cash stays in its sleeve. Failed or HOLD intents do not lend their budgets to
peers. There is no borrowing, margin, short selling, fractional shares or implicit
rebalance. This adapter reuses M2's allocation contract only: it does not construct
historical daily StrategySpecs, manufacture a qualified M2 batch, invoke M2-C or
claim historical qualification establishes forward eligibility. M2 historical
portfolio-wide missing-price rules remain unchanged.

## Risk and modeled costs

The report uses the persisted account's existing `RiskLimits` and `CostAssumptions`.
A caller cannot weaken either by supplying a new fingerprint. Pure Phase 8/M1B
price-impact/tick-rounding and fee functions provide the conservative modeled price
and charges. These are estimates, never observed execution prices or fills. No fee
schedule is claimed official; zero costs require explicit persisted zero assumptions.
Missing or mismatched costs fail closed.

Auto sizing uses whole shares, fee-inclusive sleeve affordability, order value,
position quantity, instrument exposure and remaining portfolio exposure. Explicit
oversized quantities are blocked rather than partially approved. Daily-loss checks
include aggregate modeled charges and adverse price impact, including tick rounding;
exposure checks conservatively use modeled BUY notional. SELL cannot exceed trusted
holdings. HOLD reserves zero cash and approves zero quantity.

When automatic sizing reaches zero, its reason identifies the actual binding cap.
Simultaneous zero caps use Phase 8's deterministic priority: order notional, position
quantity, sleeve affordability, instrument exposure, then portfolio exposure. Daily-loss
checks retain their existing subsequent position in screening. Adequate sleeve cash
does not become a cash-shortage diagnostic when an exposure/order cap binds instead.
Phase 8 requires a strictly positive maximum position quantity: in this flat-account
scope a position-only zero cap is invalid configuration, rejected before database
access. Valid positive position caps continue to size whole shares normally.

After canonical evaluation, approved BUY notional plus costs equals total hypothetical
reservation. Reservations never exceed sleeve budgets or available portfolio cash.
Remaining portfolio cash equals available cash minus reservations. Blocked intents may
show the requested cost estimate, but their approved quantity and reservation are zero;
aggregate totals include only approved hypothetical BUYs.

## Bounded account-state limitation

This first allocation evaluation requires a reconciled, unused, flat synthetic account.
Accounts with orders, positions, ledger/P&L history or daily-state records return
FORWARD_SLEEVE_STATE_UNAVAILABLE (or a reconciliation failure). The current schema
does not authenticate forward per-sleeve cash after trading. Dividing a used account's
remaining aggregate cash equally would silently redistribute frozen sleeves, so it is
not supported. Thus trusted holdings in the accepted scope are zero and SELL intents
are blocked. This is not a general portfolio position manager.

Matching a modified `paper_accounts.initial_cash` with modified request cash/budgets
cannot replace the immutable inception proof. Missing, inconsistent, future or
contradictory inception/equity evidence produces BLOCKED decisions with
`ACCOUNT_INCEPTION_INTEGRITY`; no automatic repair occurs. Cash mismatch and other
unavailable context retain their existing fail-closed reason codes. All verification
uses the original read-only connection, without opening a second account snapshot.

## Example and demonstration

With INR 10,000, synthetic observed prices 300/100/200, persisted repository default
cost assumptions, BUY 10 INFY, BUY 10 RELIANCE and HOLD TCS:

| Instrument | Sleeve cash | Quantity | Modeled price | BUY notional | Costs | Reservation | Status |
|---|---:|---:|---:|---:|---:|---:|---|
| INFY | 3333.34 | 10 | 300.20 | 3002.00 | 4.63 | 3006.63 | APPROVED_HYPOTHETICAL |
| RELIANCE | 3333.33 | 10 | 100.10 | 1001.00 | 1.54 | 1002.54 | APPROVED_HYPOTHETICAL |
| TCS | 3333.33 | 0 | 200.00 | 0 | 0 | 0 | HOLD_NO_ACTION |

Accepted: 2; blocked: 0; HOLD: 1. Total BUY notional: 4003.00; costs: 6.17;
hypothetical reservation: 4009.17; unreserved portfolio cash: 5990.83.

Tests additionally demonstrate oversized BUY rejection, fee-inclusive affordability,
instrument/portfolio/order/quantity/daily-loss violations, missing/stale evidence,
SELL exceeding holdings, peer isolation, changed-evidence invalidation and zero
side effects. Fixtures block socket connections and use temporary database/log/calendar
paths. Whole-database dump comparisons protect scheduler, journal, paper-account,
equity-snapshot, order/fill and authorization state.

## Restart and replay

Each evaluation reloads authoritative evidence; there is no approval cache or persisted
reservation. An identical request/evidence snapshot returns identical canonical output.
Duplicate evaluation is idempotent reporting, not an additional cash reservation.
Changed evidence is revalidated and changes or blocks the result. This supplies no
durable execution deduplication capability; future execution must implement its own
audited evaluation/proposal binding and one-use execution authorization.

Tests compare fresh databases, M5-A restart between observations, reordered peer delivery,
reordered input intents and a separate fresh Python interpreter. Registry administrative
wall times and legacy REAL tick values do not participate in financial identity;
exact decimal ticks do. Recovery's event creation time/result marker and restart-health
records do not enter the market-evidence fingerprint. Authoritative quote/candle
identity, values, source metadata and event status/binding remain bound.

Run the duplicate-name unit/integration files with importlib collection:

```powershell
.\.venv\Scripts\python.exe -m pytest --import-mode=importlib -q tests/test_portfolio_pretrade.py tests/integration/test_portfolio_pretrade.py
```

## M5-B2 prerequisites

M5-B2 requires separate authorization and review, fresh execution-time data/risk
validation, trusted forward sleeve/position state, durable deduplication, Phase 8
proposal/evidence binding and existing one-use authorizations. M3 real-data qualification
remains separately blocked. These reports cannot waive calendar, source-use, risk,
reconciliation or execution gates. No schema migration, dependencies or execution
authority changes are introduced by M5-B1.

## Implementation validation

Focused new pre-trade unit/integration selection: 60 passed, including the separate
Python interpreter comparison. M2 allocation contracts: 55 passed. Phase 8
risk/scheduler authorization selection: 14 passed. M5-A unit/integration (including
schema-18 migration and identity immutability): 28 passed. Total: 157 focused tests
passed. The known unrelated legacy importlib issue was avoided by running the Phase 8
selection in default import mode. No full suite was run.

Ruff check and format verification passed for the three new Python files; focused
MyPy passed for the new production module; `git diff --check` passed. No normal
application database, broker connection, real order, commit or push was used.

## Independent-audit remediation

The three audit corrections above are implemented, pending a new independent
acceptance review. New regressions alter nested frozen contracts after construction,
inject malformed result fields, modify both persisted and requested funding, remove
or corrupt inception evidence, introduce ownership/sequence contradictions, seed
pre-existing financial activity and exercise zero-size diagnostic precedence.
All corruption is confined to disposable fixtures. Full database dumps are compared
before and after evaluation, including scheduler, journal, account, equity and orders.
No paper or real execution capability is added; M5-B2 remains unstarted.

Remediation validation: 119 pre-trade unit/integration tests passed, followed by
21 focused boundary/fingerprint/example checks after the final validation updates.
The M4 equity file passed all 55 tests in default pytest import mode; M5-A/schema-18
unit/integration passed 28 tests; focused M2 allocation and Phase 8 risk/authorization
checks passed 22 tests. Ruff and formatting passed for the four relevant Python
files, focused MyPy passed for both changed production modules, and
`git diff --check` passed. The full project suite was not run.
