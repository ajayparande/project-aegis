# Phase 6 — Technical Indicators

## Status

**ACCEPTED WITH DOCUMENTED LIMITATIONS.** The original catalog below contains
candidate families and unresolved proposals. The authorized Phase 6 implementation
scope is the subset explicitly approved in the Phase 6 execution request and recorded
under “Approved v1 scope and conventions” below. Unselected catalog proposals remain
deferred; they are not implicit acceptance requirements for this release.
Validation evidence is recorded in
[`../docs/reviews/PHASE_06_IMPLEMENTATION_REPORT.md`](../docs/reviews/PHASE_06_IMPLEMENTATION_REPORT.md).

## Goal and boundary

Provide deterministic, causal, documented technical-indicator calculations over
historical OHLCV candles. Indicators are descriptive research features. This phase does
not generate strategies, BUY/SELL signals, ML predictions, LLM interpretations, broker
integrations, or live execution. It does not change `RESEARCH_ONLY` mode or authorize
using an indicator as a trade decision.

## Approved v1 scope and conventions

The following is the approved implementation inventory: SMA, EMA, WMA, RSI, MACD,
Stochastic Oscillator, ROC, ATR, Bollinger Bands, session VWAP, OBV, volume moving
average, typical price, absolute returns, percentage returns, and percentage change.
Percentage returns and percentage change expose separate descriptive API names for
the same simple-return formula. The remaining candidate rows below—including ADX,
Supertrend, Ichimoku, anchored VWAP, and price-level families—are deferred and are
not implemented by this phase release.

Public functions accept the existing completed `Candle` values, or the same validated
input forms accepted by `prepare_candles`. Inputs must be strictly chronological by
UTC instant by default; `normalize=True` explicitly opts into sorting. Results are
immutable `IndicatorResult` objects with aligned timestamp tuples, named Decimal
columns, `None` during warm-up, and versioned JSON-safe serialization. Public functions
and result types are exported from `aegis`.

| Indicator | V1 convention and default | First valid result and edge behavior |
|---|---|---|
| SMA | Trailing arithmetic mean of close; period 20; caller may select OHLC field. | At the `period`th supplied candle; earlier positions are `None`. |
| EMA | Alpha `2/(n+1)`; initial seed is SMA of first `n` observations; period 20. | At the `period`th observation; then recursive EMA. |
| WMA | Linear weights 1 through `n`, newest has weight `n`; period 20. | At the `period`th observation; earlier positions are `None`. |
| RSI | Wilder-smoothed gains/losses seeded with arithmetic means of first 14 changes. | First value after 14 changes. Flat=50, gain-only=100, loss-only=0. |
| MACD | EMA(12)-EMA(26); signal is EMA(9) over valid MACD values; histogram=MACD-signal. | MACD starts when slow EMA is seeded; signal/histogram start after 9 MACD values. |
| Stochastic | Raw `%K=100*(C-lowestLow)/(highestHigh-lowestLow)` over 14 bars; `%K` and `%D` each use SMA(3). | Requires full lookback and smoothing. A zero high-low range maps raw `%K` to 50. |
| ROC | `100*(current/reference - 1)`; period 12; caller may select OHLC field. | At the `period`th later observation; source prices are validated positive. |
| ATR | True range includes gaps from prior close; first bar uses high-low; SMA seed then Wilder smoothing; period 14. | At the 14th true range. Output is non-negative price units. |
| Bollinger Bands | Trailing close mean ± 2 population standard deviations (`ddof=0`); period 20. | At the `period`th observation. Zero variance gives equal bands. |
| VWAP | Typical-price proxy `(H+L+C)/3`, weighted by completed-bar volume; requires `SessionContext`; timestamp labels must be within `[session_open, session_close)` in its timezone; resets by local date. | Zero cumulative volume is `None`. Metadata records whether the first supplied bar for each date equals configured open; this is not a full-session completeness claim. |
| OBV | Seed zero; add volume on rising close, subtract on falling close, unchanged on equal close. | First bar is zero; zero volume is valid. |
| Volume moving average | Trailing arithmetic mean of supplied volume; period 20. | At the `period`th observation; zero volume remains zero. |
| Typical price | `(H+L+C)/3`. | Available for each validated candle. |
| Absolute returns | Current selected price minus prior selected price. | First bar is `None`. |
| Percentage returns / percentage change | `100*(current/prior - 1)` on selected close by default. | First bar is `None`; prior price is positive by Candle validation. |

Calculations use a private Decimal context with precision 50 and `ROUND_HALF_EVEN`;
no output quantization is applied. Serialization writes canonical decimal strings.
No caller Decimal context is changed. Only observations supplied by the caller are
used: the library does not fill, interpolate, or infer missing bars or exchange
holidays. Rolling periods count supplied observations. Session VWAP requires explicit
session hours and timezone and resets on each local session date; partial sessions are
annotated in result configuration metadata. This library does not aggregate or align
higher timeframes and does not claim market-calendar completeness.

Example public use:

```python
from aegis import (
    Candle,
    SessionContext,
    simple_moving_average,
    volume_weighted_average_price,
)

bars = [
    Candle.from_values("2026-01-05T09:15:00+05:30", 100, 102, 99, 101, 500),
    Candle.from_values("2026-01-05T09:16:00+05:30", 101, 103, 100, 102, 650),
]
result = simple_moving_average(bars, period=2)
serialized = result.to_dict()  # Decimal values are canonical strings; warm-up is None.

session = SessionContext("09:15", "15:30", "Asia/Kolkata", candle_interval_minutes=1)
vwap_result = volume_weighted_average_price(bars, session_context=session)
```

The existing Aegis architecture uses Python packages under `src/aegis`, SQLite for local
metadata, canonical decimal text for newly stored historical prices, and `Decimal` for
financial calculations. Existing Strategy Lab SMA uses a local Decimal context with
explicit `ROUND_HALF_EVEN`; backtesting uses isolated Decimal contexts and explicit
rounding conventions for monetary charges. These are precedents, not a blanket approved
indicator convention. Decide whether indicator values are returned as `Decimal`, their
serialization representation, context precision/rounding, and whether any display
quantization is allowed before implementation. Do not use SQLite `REAL` for persisted
indicator values.

## Indicator inventory and unresolved conventions

The formulas and parameter values below are common candidate conventions only. The
approved v1 table above defines the implemented subset and its fixed decisions. All
other rows remain proposals for a separately approved release and are not Phase 6 v1
acceptance requirements. Their unresolved choices remain DESIGN DECISION REQUIRED.

| Group / indicator | Candidate formula and inputs | Candidate parameters, history, and initialization | Edge handling and output |
|---|---|---|---|
| Trend — SMA | Arithmetic mean of the selected price over the latest `n` completed bars. | Candidate `n=20`; requires `n` valid observations. No smoothing beyond the window. Price source and default are DESIGN DECISION REQUIRED. | No denominator beyond `n`. Output is in price units. Missing bars must not be silently filled. |
| Trend — EMA | `EMA[t] = alpha*x[t] + (1-alpha)*EMA[t-1]`, `alpha=2/(n+1)`. | Candidate `n=20`; seed might be an initial `n`-bar SMA, but seed and earliest valid index are DESIGN DECISION REQUIRED. | Output is in price units. Missing-bar reset/continuation policy is DESIGN DECISION REQUIRED. |
| Trend — MACD | Difference of fast and slow EMAs; signal line is an EMA of the difference; histogram is MACD minus signal. | Candidate `(fast, slow, signal)=(12,26,9)`; requires both EMA seeds and signal warm-up. Ordering, seed, and smoothing are DESIGN DECISION REQUIRED. | Lines and histogram are signed price differences, not percentages. |
| Trend — ADX | True range and directional movements produce `+DI`, `-DI`, directional index, then smoothed ADX. | Candidate period `14`; requires a defined true-range window and additional directional-index smoothing history. Wilder versus other smoothing and seed are DESIGN DECISION REQUIRED. | `+DI`, `-DI`, and ADX conventionally range `[0,100]`. Zero smoothed true range and tie handling are DESIGN DECISION REQUIRED. |
| Trend — +DI / -DI | Smoothed positive/negative directional movement divided by smoothed true range, multiplied by 100. | Candidate period `14`; warm-up and smoothing follow the approved ADX convention. | Nominal range `[0,100]`; zero true range and equal up/down movement handling are DESIGN DECISION REQUIRED. |
| Trend — Supertrend | Midpoint-based bands offset by a multiple of ATR, with final-band/trend-state updates. | Candidate ATR period `10`, multiplier `3`; ATR smoothing, band update, initialization, and earliest valid state are DESIGN DECISION REQUIRED. | Band is in price units. Define state output without treating it as a BUY/SELL signal. |
| Trend — Ichimoku Cloud | Candidate components: midpoint of rolling high/low windows for Tenkan and Kijun; their midpoint for Span A; rolling high/low midpoint for Span B. | Candidate windows `(9,26,52)` and display displacement `26`; all windows, seed, and alignment are DESIGN DECISION REQUIRED. | Spans are price levels. A forward display offset must never make a value appear available before it was calculated; Chikou-style backward plotting must not backfill historical knowledge. |
| Trend — Parabolic SAR | Recursive stop-and-reversal level updated using an extreme point and acceleration factor. | Candidate step `0.02`, maximum `0.20`; initial direction, starting SAR, extreme-point update, step, cap, and first valid index are DESIGN DECISION REQUIRED. | Output is in price units. Reversal and equality-boundary behavior are DESIGN DECISION REQUIRED. |
| Momentum — RSI | Relative-strength transform `100 - 100/(1+RS)`, where `RS` is smoothed gains divided by smoothed losses. | Candidate period `14`; Wilder smoothing is a candidate only. Initial gain/loss seed and earliest valid index are DESIGN DECISION REQUIRED. | Range `[0,100]`. Zero loss, zero gain, and both zero require explicit approved outputs. |
| Momentum — Stochastic Oscillator | `%K = 100*(close-lowest_low)/(highest_high-lowest_low)`; `%D` smooths `%K`. | Candidate `(lookback, K-smoothing, D-smoothing)=(14,3,3)`; averaging methods and warm-up are DESIGN DECISION REQUIRED. | `%K/%D` nominally `[0,100]`; zero high-low range and missing values require explicit rules. |
| Momentum — Stochastic RSI | Stochastic normalization of RSI over a rolling RSI range, optionally smoothed into `%K/%D`. | Candidate RSI/stochastic/smoothing periods `(14,14,3,3)`; all periods and smoothing choices are DESIGN DECISION REQUIRED. | Nominal range `[0,100]`; zero RSI range and compounded warm-up are DESIGN DECISION REQUIRED. |
| Momentum — CCI | `(typical_price - mean(typical_price))/(0.015 * mean_absolute_deviation)`, with typical price `(H+L+C)/3`. | Candidate period `20`; mean convention and warm-up are DESIGN DECISION REQUIRED. | Dimensionless and unbounded. Zero mean deviation handling is DESIGN DECISION REQUIRED. |
| Momentum — Williams %R | `-100*(highest_high-close)/(highest_high-lowest_low)`. | Candidate lookback `14`; uses completed-bar highs, lows, and close. | Nominal range `[-100,0]`; zero range and missing-bar rules are DESIGN DECISION REQUIRED. |
| Momentum — ROC | `100*(current_price / price_n_bars_ago - 1)`. | Candidate lookback `12`; price source and warm-up are DESIGN DECISION REQUIRED. | Percentage, signed and unbounded. A zero reference price must be handled explicitly even though validated Aegis OHLC prices are positive. |
| Volatility — Bollinger Bands | Rolling center with upper/lower bands `center +/- k*standard_deviation`. | Candidate `(n,k)=(20,2)`; center, population/sample deviation (`ddof`), seed, and warm-up are DESIGN DECISION REQUIRED. | Bands are in price units; define deviation precision and avoid rounded values feeding later indicators. |
| Volatility — ATR | Mean/smoothed true range; true range is the maximum of high-low and gaps from prior close. | Candidate period `14`; Wilder/RMA versus SMA and first-bar true-range rule are DESIGN DECISION REQUIRED. | Non-negative price units; seed and missing previous-close handling are DESIGN DECISION REQUIRED. |
| Volatility — Keltner Channels | EMA center with upper/lower bands offset by a multiple of ATR. | Candidate EMA `20`, ATR `10`, multiplier `2`; all periods, smoothers, seeds, and warm-up are DESIGN DECISION REQUIRED. | Bands are in price units; inherits ATR and EMA edge rules. |
| Volatility — Donchian Channels | Rolling maximum high and minimum low, with optional midpoint. | Candidate lookback `20`; inclusion of current completed bar and midpoint output are DESIGN DECISION REQUIRED. | Price units; warm-up requires a complete window. No future bar may enter the range. |
| Volatility — Bollinger/Keltner squeeze | Candidate condition compares Bollinger bands as contained within Keltner bands; a width ratio may be reported. | Candidate parameters inherit Bollinger/Keltner rows. Boolean versus ratio output, transitions, and warm-up are DESIGN DECISION REQUIRED. | Define containment equality, missing input, and width-ratio denominator behavior. No squeeze state is a trade instruction. |
| Volume — RVOL | Current completed-bar volume divided by a baseline volume measure. | Candidate baseline is a rolling `20`-bar average; same-time-of-session comparison versus simple rolling baseline is DESIGN DECISION REQUIRED. | Dimensionless ratio. Zero baseline, partial session, and session-boundary behavior are DESIGN DECISION REQUIRED. |
| Volume — Volume Moving Average | Rolling arithmetic mean of volume. | Candidate period `20`; session reset versus continuous window and warm-up are DESIGN DECISION REQUIRED. | Volume units; no denominator beyond count. Missing volume is not zero unless an approved ingestion rule establishes it. |
| Volume — OBV | Cumulative volume added when close rises, subtracted when it falls, unchanged on equal close. | Initial cumulative value, tie rule, session reset, and anchoring are DESIGN DECISION REQUIRED. | Cumulative volume units; input close comparison and reset semantics must be fixed. |
| Volume — VPT | Cumulative `volume * (close-current_previous_close)/previous_close`. | Initial value, price/volume units, reset semantics, and first-bar handling are DESIGN DECISION REQUIRED. | Cumulative volume-weighted return. Zero previous close handling is DESIGN DECISION REQUIRED. |
| Volume — Accumulation/Distribution Line | Cumulative volume multiplied by close-location value `(2C-H-L)/(H-L)`. | Initial value and reset/anchor semantics are DESIGN DECISION REQUIRED. | Cumulative volume units; zero high-low range and out-of-range close are explicit edge tests. |
| Volume — CMF | Rolling sum of close-location-value times volume divided by rolling volume sum. | Candidate period `20`; rolling window and initialization are DESIGN DECISION REQUIRED. | Usually bounded `[-1,1]` for valid candles; zero volume sum, missing volume, and rounding are DESIGN DECISION REQUIRED. |
| Volume — VWAP | Session cumulative `sum(price_proxy*volume)/sum(volume)`. | Price proxy, session identifier/timezone, reset boundary, inclusion of current completed bar, and session coverage are DESIGN DECISION REQUIRED. Must be session-aware. | Price units. Zero cumulative volume and partial/missing session data require explicit behavior; do not carry a prior session's VWAP forward. |
| Volume — Anchored VWAP | Cumulative VWAP from an explicit anchor through the current completed bar. | Anchor kinds (timestamp, session, confirmed swing/event), inclusive/exclusive anchor bar, reset behavior, and timezone are DESIGN DECISION REQUIRED. | Price units. Reject unavailable/future anchors; zero volume and missing data require explicit behavior. |
| Price levels — Pivot Points | Candidate classic floor pivots from prior completed period: `P=(H+L+C)/3`, with derived support/resistance levels. | Pivot family, source period, formulas for each level, and applicable session are DESIGN DECISION REQUIRED. | Price units. Only completed source periods may be used; boundary and rounding rules are DESIGN DECISION REQUIRED. |
| Price levels — Fibonacci retracement | Ratios applied to a confirmed high-low swing range, measured from the selected direction's endpoint. | Candidate ratios `.236,.382,.500,.618,.786`; ratio set, direction, swing selection, and anchor convention are DESIGN DECISION REQUIRED. | Price units. Integrate only approved confirmed Phase 5 swings; no unconfirmed pivot or future swing may be used. |
| Price levels — Fibonacci extensions | Candidate ratios extend a defined prior swing range from a confirmed retracement/pivot. | Candidate ratios such as `1.272` and `1.618`; anchor sequence, ratio set, and applicability are DESIGN DECISION REQUIRED. | Price units. Require fully confirmed causal anchors; no extension is a forecast or signal. |
| Price levels — Previous-day high/low/close | Exact high, low, and close of the preceding completed exchange session. | Exchange calendar, timezone, session definition, and treatment of special/partial sessions are DESIGN DECISION REQUIRED. | Price units. Never use the current day's eventual range/close; unavailable prior session remains missing. |
| Price levels — Previous-week high/low/close | Exact high, low, and close of the preceding completed exchange week/session set. | Week boundary, holidays, exchange timezone/calendar, and partial-week policy are DESIGN DECISION REQUIRED. | Price units. Only completed prior weeks are available; missing calendar coverage must remain explicit. |
| Price levels — Multi-timeframe integration with Phase 5 zones | Align completed higher-timeframe levels with confirmed Phase 5 support/resistance zones; do not silently create an independent zone definition. | Supported timeframe relationships, zone identity/provenance, overlap tolerance, and alignment policy are DESIGN DECISION REQUIRED. | Preserve source timeframe, timestamps, and Phase 5 approximation/status metadata. Higher-timeframe values become available only after that bar completes. |

## Shared calculation and data contracts

- Accept the shared Phase 5 `Candle` contract and `prepare_candles` forms for 1-minute,
  5-minute, 15-minute, and daily timestamp sequences. Candle inputs do not carry an
  interval field; these functions preserve timestamps and calculate over the supplied
  sequence without aggregation or interval inference.
- Use an isolated 50-digit Decimal `Context`, `ROUND_HALF_EVEN`, Decimal runtime
  exponent limits, and no output quantization. No binary-float intermediate or ambient
  caller-context mutation is permitted.
- Emit `None` before the first complete required observation window. Do not fill,
  interpolate, or fabricate missing candles. Rolling periods count supplied rows and
  recursive indicators continue over supplied rows; identifying and gating gaps is the
  caller's market-data-quality responsibility.
- Reject invalid/incomplete candles, duplicate UTC instants, and out-of-order input by
  default. `normalize=True` explicitly requests ordering by instant. Preserve source
  timestamp text in output. Candle validation rejects missing/invalid OHLCV fields.
- Higher-timeframe aggregation/alignment and exchange-calendar inference are deferred.
- VWAP requires explicit `SessionContext`, accepts bar timestamps in the configured
  local `[open, close)` interval, resets by local date, and records whether the first
  supplied bar for a date equals session open. It does not prove complete session data.
  Anchored VWAP is deferred.
- The public interface is batch-only. Recalculating successive prefixes must match the
  corresponding values in a full batch result, including empty and warm-up prefixes;
  this does not claim a stateful streaming API. Appended future candles must not alter
  earlier serialized results.
- Results use schema/definition version `1.0.0`, immutable timestamp-aligned columns,
  configuration metadata, and canonical Decimal strings. Public names are exported
  from `aegis`; calculations are not persisted.
- Keep calculation logic outside the dashboard and database layer. Persisting,
  caching, or registering indicator outputs is not assumed; any storage design requires
  a separate schema and migration review.

## Required reference and test convention

For **each approved v1 indicator**, document the approved equation and build an independent
reference calculation from hand-computable fixtures. The reference must use the same
explicitly approved seed, smoothing, inclusion, session, and rounding conventions, but
must not call the production implementation. Any third-party comparison is optional,
must be version-pinned, and is not authoritative until its conventions are reconciled.
Golden values must include the complete input, configuration, warm-up positions, and
expected unrounded/serialized outputs.

Every indicator requires:

- Positive values that hand-calculate to the reference.
- Negative/non-trending/zero-change cases appropriate to its mathematical domain.
- Boundary values, including the first valid output, exact threshold/equality cases,
  zero denominators or zero ranges, and the behavior after missing input.
- A golden-value sequence with multiple outputs, not only a single final value.
- A causality/prefix test proving each output uses only candles available at that
  timestamp and remains unchanged when later candles are appended.
- A batch-versus-incremental parity test across empty, warm-up, valid, gapped, and
  session-boundary sequences.

Do not mark an indicator complete if an unresolved formula, parameter, initialization,
missing-value, zero-denominator, precision, or reference-convention decision remains.

## Temporal causality and non-goals

No approved v1 indicator may read incomplete candles, use future bars, revise a
previously calculated prefix when later bars are appended, or assign a value to a
timestamp before its required inputs are available. Rolling windows include the current
completed observation and use only the preceding `period-1` supplied observations.
Session VWAP applies only to candles inside the explicit caller-provided session; no
higher-timeframe aggregation or deferred candidate family is implemented.

Explicitly out of scope: strategy generation, optimization or trade rules, BUY/SELL
signals, ML prediction, LLM interpretation or agent decisions, broker connectivity,
order placement, live execution, paid feeds, and any change from `RESEARCH_ONLY`.

## Acceptance criteria

- Every indicator in the approved v1 subset above has its formula, defaults, warm-up,
  initialization, edge behavior, numeric representation, and public API documented.
- Independent multi-point reference tests, positive/negative cases, boundaries, flat
  and zero-volume cases, and insufficient-history behavior cover each approved output.
- Results preserve timestamps and prefix invariance, and tests cover empty, warm-up,
  valid, irregularly spaced, and VWAP session-boundary input sequences. The public
  contract is batch-only; prefix-by-prefix batch calculations establish the incremental
  availability/parity behavior without claiming a streaming API.
- 1m, 5m, 15m, and daily timestamp sequences are accepted. No higher-timeframe
  aggregation/alignment or exchange-calendar completeness is claimed.
- Versioned serialization and package exports are stable; results are deterministic
  under altered caller Decimal contexts and do not fabricate warm-up or future values.
- Large bounded inputs measure runtime and peak memory with justified regression limits.
- Calculations remain outside dashboard/database layers and do not add strategy
  decisions, broker access, execution paths, or changes from `RESEARCH_ONLY`.
