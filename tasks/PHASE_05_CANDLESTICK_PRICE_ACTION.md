# Phase 5 — Candlestick and Price-Action Research Library

## Status

Phase 5 v1 scope is the deterministic, research-only library documented below.
**Status: ACCEPTED WITH DOCUMENTED LIMITATIONS.** The acceptance decision and
validation record are in
[`../docs/reviews/PHASE_05_IMPLEMENTATION_REPORT.md`](../docs/reviews/PHASE_05_IMPLEMENTATION_REPORT.md).
Final verification on 2026-11-09 reconfirmed the acceptance criteria and recorded
134 targeted tests and 388 total tests passing; the report contains the complete
current validation results.
The library emits descriptive results only; it does not produce strategies,
BUY/SELL recommendations, orders, or trading decisions. A detected formation is
not evidence of predictive value or profitability.

## Approved v1 scope

- Validated, completed-candle OHLCV input; timezone-aware ISO-8601 timestamps.
- The single-, two-, and multi-candle detectors listed in the catalogue below.
- Confirmed swings, swing-derived market structure, zones, selected zone reactions,
  breakouts, retests, failures, and the documented bounded price-action events.
- Optional opening-range analysis using explicit caller-supplied session context.
- Deterministic Decimal calculations, immutable JSON-safe results, bounded searches,
  and reproducible result metadata.

No separate technical indicator, strategy, execution, or chart-pattern catalogue
has been approved in the Phase 5 specification. Head and Shoulders, inverse Head
and Shoulders, Triple Top/Bottom, Rounding Top/Bottom, triangles, wedges, flags,
pennants, rectangles, Cup and Handle, and Inverse Cup and Handle are proposed
future enhancements only. They are not implemented or acceptance requirements for
this v1 specification. Their definitions are not universally standardized and
would need an explicit versioned mathematical specification and approval.

## Input, ordering, and numeric rules

- A candle has timestamp, positive finite Decimal OHLC, non-negative integer
  volume, and `is_complete=True`. High/low must contain open and close.
- Timestamp text must be ISO-8601 with an explicit UTC offset. The input text is
  preserved; ordering and duplicate checks use the UTC instant. Duplicate instants
  (including equal instants expressed with different offsets) fail. Input is not
  reordered unless the caller explicitly requests `prepare_candles(...,
  normalize=True)`.
- Incomplete candles and malformed or ambiguous timestamps fail with
  `CandlestickPatternError`. No detector infers missing bars or silently repairs
  the data.
- Detection arithmetic uses an isolated Decimal context sized for input precision;
  it does not depend on or mutate the caller's Decimal context. Binary floating
  arithmetic is not used for price comparisons. Float input is converted through
  its decimal string representation for backward-compatible convenience; exact
  financial ingestion should provide canonical decimal text or `Decimal`.
- Every detector-generated result has `is_confirmed=True`. Provisional candidates
  are not emitted.

## Versioned catalogue and rules

Definition version `1.0.0` is a reproducible OHLC/OHLCV approximation, not a claim
that traditional charting definitions are universally standardized. Unless noted,
each event is confirmed at the close of its final required candle. The rules below
are the implemented predicates; no prior trend, predictive value, or strategy
meaning is implied.

### Single-candle (`single_candle`)

All single-candle shapes require `high - low > min_range` (default `0.01`).
Let body be `abs(close-open)` and shadows be the distances from the body to high
and low.

| IDs | Rule |
|---|---|
| `hammer`, `hanging_man` | Nonzero bullish / bearish body respectively; lower shadow is at least `long_wick_ratio × body` (default `2×`); upper shadow is at most `short_wick_ratio × range` (default `0.25×`). |
| `inverted_hammer`, `shooting_star` | Same body-direction distinction, with upper and lower shadow roles reversed. |
| `doji` | Body/range `<= doji_body_ratio` (default `0.10`). |
| `dragonfly_doji`, `gravestone_doji` | Doji plus lower / upper shadow respectively `>= 0.60× range` and opposite shadow `<= 0.10× range`. |
| `long_legged_doji` | Doji; each shadow `>= long_legged_min_shadow_ratio × range` (default `0.30×`) and `< 0.60× range`. |
| `spinning_top` | Body/range is above the doji ratio and `<= spinning_top_max_body_ratio` (default `0.30`); both shadows are `>= 0.50× range`. |
| `bullish_marubozu`, `bearish_marubozu` | Bullish / bearish body respectively; body/range `>= 0.80`; each shadow/range `<= 0.10`. |
| `pin_bar` | Nonzero body/range `<= 0.40`; one shadow is at least `2× body` and the opposite shadow is at most `0.25× range`. Direction follows the long-shadow rejection side. |

These are geometric shapes, not context-aware reversal findings. There is no
separate invalidation event for a completed one-candle result.

### Two-candle (`two_candle`)

History is two adjacent completed candles. All results confirm on the second
candle; boundaries use the configured exact Decimal thresholds.

| IDs | Rule |
|---|---|
| `bullish_engulfing`, `bearish_engulfing` | Opposite-color prior/current bodies; current body contains the prior body, has at least its size, and the current candle is bullish / bearish respectively. |
| `bullish_harami`, `bearish_harami` | Opposite-color prior/current bodies; the smaller current body is contained in the prior body; direction follows the current candle. |
| `harami_cross` | Nonzero prior body and nonzero-range current doji contained in the prior body; direction is opposite the prior candle. |
| `piercing_line`, `dark_cloud_cover` | Opposite-color candles; current opens beyond the prior whole-candle low/high respectively, then closes beyond the prior-body midpoint but not past the prior open. |
| `tweezer_bottom`, `tweezer_top` | Bearish then bullish lows, or bullish then bearish highs, differ by at most `equal_tolerance` (default `0.000001`). |
| `bullish_kicker`, `bearish_kicker` | Prior bearish / bullish and current bullish / bearish; current open gaps beyond the prior high / low. |
| `matching_low`, `matching_high` | Same-color bearish / bullish candles whose closes differ by at most `equal_tolerance`. |
| `inside_bar` | Current high/low are inside prior high/low and at least one boundary is strict. |
| `outside_bar` | Current high is strictly above and current low strictly below the prior range; direction follows current close vs open. |

The gap-based predicates are continuous-OHLC approximations and do not account
for exchange-specific gap conventions. No later invalidation is emitted.

### Multi-candle (`multi_candle`)

| IDs | History and rule |
|---|---|
| `morning_star`, `morning_doji_star`, `evening_star`, `evening_doji_star` | Three candles. First directional candle, smaller middle body, opposite-color third close past the first body's midpoint. The middle candle is classified as doji using `doji_body_ratio`; a traditional gap is not required. |
| `three_inside_up`, `three_inside_down` | Three candles: first body directional, smaller opposite-color second body contained within it, and third candle closes beyond the first open in the reversal direction. |
| `three_outside_up`, `three_outside_down` | Three candles: second body engulfs the first body in the opposite direction; third candle continues beyond the second close. |
| `three_white_soldiers`, `three_black_crows` | Three same-direction candles with successively advancing closes and prescribed open/body containment; each close is in the outer 34% of its range (66% for soldiers, 34% for crows). |
| `rising_three_methods`, `falling_three_methods` | Five candles: directional first candle, three opposite-color smaller-body candles contained inside the first whole range, then a same-direction fifth close beyond the first close. |
| `bullish_three_line_strike`, `bearish_three_line_strike` | Four candles: three progressively advancing same-color candles followed by an opposite-color candle spanning the first open and closing beyond it. |
| `bullish_abandoned_baby`, `bearish_abandoned_baby` | Three candles with middle doji and whole-range gaps on both sides exceeding `gap_tolerance` (default `0.0001`), with reversal-direction first and third candles. |
| `double_top`, `double_bottom` | At least two confirmed swing highs/lows separated by an intervening confirmed opposing pivot. Similar pivots differ by no more than `support_zone_tolerance` (default `0.01`). A later completed close must cross the selected neckline by `breakout_buffer` (default `0.0001`). Pivot confirmation uses `confirmation_lookback` bars on both sides. Pair distance and post-pivot confirmation search are bounded by `pattern_lookback`. |

Three-, four-, and five-candle patterns confirm on their final candle. Double
Top/Bottom confirms only on the later neckline-breaking close; no provisional
candidate is output. A completed formation is not retroactively removed, and the
library does not emit a separate invalidation event.

### Price-action (`price_action`)

| IDs | Rule and confirmation |
|---|---|
| `swing_high`, `swing_low` | Pivot high is `>=` all left highs and `>` all configured right highs; pivot low is `<=` all left lows and `<` all right lows. Left and right windows use `confirmation_lookback` (default 2); result timestamp is the final right-side completed candle. |
| `hh`, `hl`, `lh`, `ll` | Compare each newly confirmed same-kind swing with its prior confirmed swing; `trend_tolerance` (default `0.0001`) separates higher/lower/equal. Confirmation is the new swing's confirmation candle. |
| `insufficient_trend_structure`, `trend_up`, `trend_down`, `trend_range` | Summarize the two latest confirmed swing highs and lows. Up/down requires both changes to exceed tolerance in the same direction; range requires both absolute changes within tolerance; otherwise structure is insufficient. |
| `support_zone`, `resistance_zone`, `support_zone_touch`, `resistance_zone_touch` | Cluster confirmed swing lows/highs within `support_zone_tolerance`; a zone forms on the second confirmed pivot, with bounds equal to extreme pivots plus/minus tolerance. Touches are confirmed swing pivots, not all intrabar contacts. |
| `bullish_resistance_breakout`, `bearish_support_breakdown` | After a zone is formed, a completed close crosses its outer bound by `breakout_buffer`. Wick-only excursions do not qualify. |
| `bullish_resistance_retest`, `bearish_support_retest` | A later candle intersects the broken zone and closes beyond the relevant outer bound. |
| `failed_bullish_breakout`, `failed_bearish_breakdown` | A later zone interaction closes back through the broken boundary, or the close fails to hold beyond the zone before a retest. These are separate later events. |
| `mother_bar` | First later completed candle contained by the mother candle's whole range, searched only within `pattern_lookback` (default 100). A later range escape ends that mother-bar search. |
| `inside_bar_breakout` | After strict inside-bar containment, a subsequent completed close crosses the mother high/low by `breakout_buffer`. Intrabar breaches alone do not qualify. |
| `bullish_fakey`, `bearish_fakey` | Mother/inside sequence, completed breakout-candle close beyond the mother boundary by `breakout_buffer`, then a later completed close back inside the mother range. The final close confirms the event. |
| `support_rejection`, `resistance_rejection` | After zone formation, candle intersects the zone, closes back beyond its outer edge, and the relevant shadow is at least `rejection_min_shadow_ratio` (default `0.50`) of the whole range. |
| `pullback` | Three-candle continuation approximation: directional impulse, opposite retracement that stays within the impulse's adverse extreme, then same-direction close beyond the impulse extreme. |
| `demand_zone`, `supply_zone` | Two-candle OHLCV approximation: base body `<= 0.30×` base range; next candle has greater volume and same-direction body `>= 1.50×` base range. This is not order-book liquidity. |
| `opening_range` | Requires an explicit `SessionContext`; exact expected completed candles covering the configured range are required. High/low are extrema of those candles; confirmation is the final opening-range candle. |
| `opening_range_breakout`, `opening_range_breakdown` | After the complete opening range, first eligible completed close beyond the range high/low by `breakout_buffer`, restricted to bars ending no later than supplied session close. |
| `range` | Rolling `range_lookback` (default 20) candles; total width must exceed `min_range`, width/low must be `<= range_max_width_ratio` (default `0.03`), and absolute first-to-last close change/width must be `<= range_max_net_change_ratio` (default `0.50`). Confirmation is the window's final candle. |

Searches that compare events across time use `pattern_lookback` where specified.
Simple adjacent-candle patterns and rolling ranges use their fixed local history;
confirmed swing/structure summaries use completed input history. No incomplete
candle or unconfirmed right-side swing is used.

## Configuration and reproducibility

`DetectionConfig` is immutable and validates values at construction.
`pattern_lookback` must be at least `2 * confirmation_lookback + 1`; default is
100. `range_lookback` must be at least 2; default is 20. Configurable Decimal
defaults are:

| Field | Default | Use |
|---|---:|---|
| `doji_body_ratio` | `0.10` | Doji body/range maximum. |
| `long_wick_ratio` | `2` | Long shadow/body minimum. |
| `short_wick_ratio` | `0.25` | Short shadow/range maximum. |
| `equal_tolerance` | `0.000001` | Tweezer/matching-price tolerance. |
| `min_range` | `0.01` | Minimum eligible candle or range width; equality is excluded for candle shapes. |
| `support_zone_tolerance` | `0.01` | Swing clustering and double-top/bottom tolerance. |
| `breakout_buffer` | `0.0001` | Required close distance beyond a boundary. |
| `gap_tolerance` | `0.0001` | Whole-range gap threshold for Abandoned Baby. |
| `trend_tolerance` | `0.0001` | HH/HL/structure comparison threshold. |
| `marubozu_body_ratio` | `0.8` | Minimum body/range. |
| `marubozu_max_shadow_ratio` | `0.1` | Maximum shadow/range. |
| `pin_max_body_ratio` | `0.4` | Maximum Pin Bar body/range. |
| `long_legged_min_shadow_ratio` | `0.3` | Minimum Long-Legged Doji shadow/range. |
| `spinning_top_max_body_ratio` | `0.3` | Maximum Spinning Top body/range. |
| `rejection_min_shadow_ratio` | `0.5` | Minimum rejection shadow/range. |
| `range_max_width_ratio` | `0.03` | Maximum rolling range width/low. |
| `range_max_net_change_ratio` | `0.50` | Maximum absolute first-to-last close drift as a fraction of rolling range width. |

`SessionContext` requires session open/close; defaults are `Asia/Kolkata`,
five-minute opening range, and one-minute candles. It rejects invalid/nonexistent
local times and ambiguous session boundaries. Caller-provided session context is
not certified exchange-calendar data.

Each detector-generated result records `metadata.definition_version` (`1.0.0`)
and the full `metadata.configuration` snapshot. Decimal parameters serialize as
canonical strings. When a `SessionContext` is provided, its full configuration is
also recorded as `metadata.session_context`. Existing detector-specific metadata
remains additive.
`PatternResult` has this shape:

```json
{
  "name": "Hammer",
  "pattern_id": "hammer",
  "category": "single_candle",
  "direction": "BULLISH",
  "start_timestamp": "2026-01-05T09:15:00Z",
  "end_timestamp": "2026-01-05T09:15:00Z",
  "confirmation_timestamp": "2026-01-05T09:15:00Z",
  "instrument": null,
  "interval": null,
  "details": {"body_ratio": "0.1"},
  "metadata": {
    "definition_version": "1.0.0",
    "configuration": {"doji_body_ratio": "0.1"}
  },
  "is_confirmed": true
}
```

In actual output, `configuration` contains every `DetectionConfig` field. The
example is abbreviated. Result details and metadata are recursively immutable;
`to_dict()` returns JSON-safe values. Input timestamp spelling/offset is retained;
UTC-instant ordering is used internally.

## Causality, bounds, and limitations

- Single/two/multi-candle confirmations cannot precede their final required close.
- Swings are unavailable until all required right-side bars are complete.
- Breakouts use closes beyond defined levels; retests/failures are later events.
- `pattern_lookback` limits double-top/bottom comparisons and specified zone/mother
  relationships. Results cannot be confirmed using future bars beyond the selected
  boundary. Prefix/truncation invariance is regression-tested.
- Approximate work is linear for local candle detectors. Pivot/zone and bounded
  relationship searches are approximately `O(n * L)` in the number of candles `n`
  and configured lookback `L`; opening-range processing also scans supplied bars
  per represented session. Memory is `O(n)` or `O(n * L)` where event results are
  retained. There is no hard input-size cap; performance tests are local observations,
  not guarantees.
- Candlestick labels do not imply market context. Star patterns do not require
  traditional gaps; supply/demand is only an OHLCV approximation; pullback is a
  three-candle approximation; zones count confirmed pivots rather than every touch.
- The library does not establish data authenticity, completeness, corporate-action
  adjustment, licensing, official exchange calendars, or predictive value.

## Test coverage matrix

The targeted tests are in `tests/test_candlesticks.py`.

| Area | Positive/negative/boundary evidence |
|---|---|
| Candle/timestamp/config input | `test_candle_rejects_invalid_ohlc`, `test_candle_rejects_invalid_volume`, `test_incomplete_candles_are_rejected`, timestamp/UTC-ordering tests, `test_detection_config_rejects_invalid_values`, SessionContext invalid/DST tests. |
| Single-candle catalogue | `test_hammer_doji_variants_and_zero_body_pin_bar`, `test_doji_threshold_boundary_is_inclusive`, `test_remaining_single_candle_shapes_use_configured_ratios`; zero/short-range and ratio boundaries are exercised. |
| Two-candle catalogue | `test_correct_harami_directions_and_body_containment`, `test_piercing_line_requires_opening_gap_below_prior_range`, `test_remaining_two_candle_reversals_and_matching_patterns`, `test_two_candle_engulfing_inside_bar_and_matching_close`, and `test_tweezer_and_matching_price_tolerances_include_the_boundary`. |
| Multi-candle catalogue | `test_three_candle_star_variants_are_distinct`, `test_three_inside_and_outside_require_three_bar_confirmation`, `test_abandoned_baby_requires_gaps_between_whole_ranges`, `test_remaining_multi_candle_patterns_have_confirmed_sequences`, double-top/bottom neckline and lookback tests. |
| Price-action events | `test_confirmed_swings_classify_up_down_and_range_structure`, support/resistance formation/touch/breakout/retest/failure/rejection tests, `test_price_action_detects_directional_pullbacks_and_supply_demand`, mother/inside/Fakey close rules, Opening Range breakout/breakdown and DST/coverage, `test_consolidation_rejects_directional_drift_within_a_narrow_percent_band`, and prefix/truncation invariance. |
| Shared result/runtime contract | Decimal-context independence, deep immutability/JSON safety, version/config metadata through all public APIs, deterministic ordering, malformed-input handling, `test_flat_valid_history_is_a_negative_case_for_all_detector_categories`, and `test_large_full_detection_sequence_has_bounded_runtime` on 5,000 candles. |

Positive synthetic examples cover the implemented catalogue IDs. Negative and
boundary checks target the shared mathematical rules and high-risk event sequences;
they do not exhaust every threshold combination, market, instrument, or exchange
convention. The implementation report maps detector groups to tests and states
remaining detector-specific limitations explicitly.

## Out of scope

Technical indicators (Phase 6), ML research (Phase 7), strategy generation,
optimization, BUY/SELL recommendations, broker access, paid data, live feeds,
orders, paper execution, cloud infrastructure, and exchange-calendar claims.
No application execution-mode change is permitted.

## Acceptance evidence

The implementation report records the actual final verdict, Git state, test counts
and timings, lint/format/type-check/Markdown-link checks, `git diff --check`,
causality evidence, and limitations. Passing tests do not establish profitability,
predictive value, exchange-data completeness, or live readiness.
