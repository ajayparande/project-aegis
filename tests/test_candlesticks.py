from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal, Inexact, localcontext
from time import perf_counter
from zoneinfo import ZoneInfo

import pytest

from aegis.candlesticks import (
    Candle,
    CandlestickPatternError,
    DetectionConfig,
    PatternResult,
    SessionContext,
    detect_all_patterns,
    detect_multi_candle_patterns,
    detect_pattern,
    detect_patterns,
    detect_price_action,
    detect_single_candle_patterns,
    detect_two_candle_patterns,
    prepare_candles,
)

_START = datetime(2026, 1, 5, 9, 15, tzinfo=UTC)


def _candle(
    index: int,
    open_: int | str | Decimal,
    high: int | str | Decimal,
    low: int | str | Decimal,
    close: int | str | Decimal,
    volume: int = 1000,
) -> Candle:
    timestamp = (_START + timedelta(minutes=index)).isoformat().replace("+00:00", "Z")
    return Candle.from_values(timestamp, open_, high, low, close, volume)


def _ids(results: list[PatternResult]) -> set[str]:
    return {result.pattern_id for result in results}


def _result_signature(results: list[PatternResult]) -> list[dict[str, object]]:
    return [result.to_dict() for result in results]


@pytest.mark.parametrize(
    ("open_", "high", "low", "close", "message"),
    [
        (8, 10, 9, 9, "open outside"),
        (11, 10, 9, 9, "open outside"),
        (9, 10, 8, 7, "close outside"),
        (9, 10, 8, 11, "close outside"),
        (9, 8, 10, 9, "high < low"),
        (0, 10, 8, 9, "non-positive"),
        (-1, 10, 8, 9, "non-positive"),
        (9, 10, 8, Decimal("NaN"), "finite"),
        (9, Decimal("Infinity"), 8, 9, "finite"),
    ],
)
def test_candle_rejects_invalid_ohlc(
    open_: object, high: object, low: object, close: object, message: str
) -> None:
    with pytest.raises(CandlestickPatternError, match=message):
        Candle.from_values("2026-01-05T09:15:00Z", open_, high, low, close, 1)  # type: ignore[arg-type]


@pytest.mark.parametrize("volume", [-1, True, 1.5, "1.5"])
def test_candle_rejects_invalid_volume(volume: object) -> None:
    with pytest.raises(CandlestickPatternError):
        Candle.from_values("2026-01-05T09:15:00Z", 9, 10, 8, 9, volume)  # type: ignore[arg-type]


def test_candle_accepts_equal_price_boundaries_and_zero_volume() -> None:
    candle = Candle.from_values("2026-01-05T09:15:00Z", 10, 10, 10, 10, 0)
    assert candle.open == candle.high == candle.low == candle.close == Decimal(10)
    assert candle.volume == 0


def test_incomplete_candles_are_rejected() -> None:
    with pytest.raises(CandlestickPatternError, match="completed"):
        Candle.from_values("2026-01-05T09:15:00Z", 9, 10, 8, 9, 1, is_complete=False)


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-02-30T09:15:00Z",
        "2026-01-05T09:15:00",
        "2026-01-05 09:15:00Z",
        "2026-01-05T09:15:00+0530",
        "2026-01-05T09:15:00+25:00",
        "",
    ],
)
def test_timestamp_parser_rejects_invalid_or_ambiguous_values(timestamp: str) -> None:
    with pytest.raises(CandlestickPatternError):
        Candle.from_values(timestamp, 9, 10, 8, 9, 1)


def test_timestamps_order_by_utc_instant_and_preserve_original_text() -> None:
    first = Candle.from_values("2026-01-05T09:15:00+05:30", 9, 10, 8, 9, 1)
    second = Candle.from_values("2026-01-05T03:46:00Z", 9, 10, 8, 9, 1)
    assert prepare_candles([first, second]) == [first, second]
    assert first.to_dict()["timestamp"] == "2026-01-05T09:15:00+05:30"
    with pytest.raises(CandlestickPatternError, match="Duplicate candle instant"):
        prepare_candles(
            [
                first,
                Candle.from_values("2026-01-05T03:45:00Z", 9, 10, 8, 9, 1),
            ]
        )


def test_normalization_is_explicit_and_orders_by_instant() -> None:
    late = Candle.from_values("2026-01-05T03:46:00Z", 9, 10, 8, 9, 1)
    early = Candle.from_values("2026-01-05T09:15:00+05:30", 9, 10, 8, 9, 1)
    with pytest.raises(CandlestickPatternError, match="chronological"):
        prepare_candles([late, early])
    assert prepare_candles([late, early], normalize=True) == [early, late]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"doji_body_ratio": Decimal("1.1")},
        {"long_wick_ratio": Decimal(0)},
        {"short_wick_ratio": Decimal("1.1")},
        {"equal_tolerance": Decimal("-0.1")},
        {"confirmation_lookback": True},
        {"confirmation_lookback": 0},
        {"range_lookback": 1},
        {"range_max_width_ratio": Decimal(0)},
        {"range_max_net_change_ratio": Decimal("1.1")},
    ],
)
def test_detection_config_rejects_invalid_values(kwargs: dict[str, object]) -> None:
    with pytest.raises(CandlestickPatternError):
        DetectionConfig(**kwargs)  # type: ignore[arg-type]


def test_hammer_doji_variants_and_zero_body_pin_bar() -> None:
    candles = [
        _candle(0, 100, 102, 91, 101),
        _candle(1, 100, 100.1, 90, 100),
        _candle(2, 100, 110, 90, 100),
        _candle(3, 100, 110, 100, 101),
    ]
    results = detect_single_candle_patterns(candles)
    assert "hammer" in _ids(results)
    assert "doji" in _ids(results)
    assert "dragonfly_doji" in _ids(results)
    assert "gravestone_doji" in _ids(results)
    assert "long_legged_doji" in _ids(results)
    assert "pin_bar" not in {
        result.pattern_id for result in results if result.start_timestamp == candles[1].timestamp
    }


def test_doji_threshold_boundary_is_inclusive() -> None:
    candle = _candle(0, 100, 105, 95, 101)
    assert "doji" in _ids(detect_single_candle_patterns([candle]))
    config = DetectionConfig(doji_body_ratio=Decimal("0.099"))
    assert "doji" not in _ids(detect_single_candle_patterns([candle], config=config))


def test_remaining_single_candle_shapes_use_configured_ratios() -> None:
    examples = {
        "hanging_man": _candle(0, 101, 102, 90, 100),
        "inverted_hammer": _candle(1, 100, 110, 99, 102),
        "shooting_star": _candle(2, 102, 110, 99, 100),
        "spinning_top": _candle(3, 100, 105, 97, 102),
        "bullish_marubozu": _candle(4, 100, 110, 100, 109),
        "bearish_marubozu": _candle(5, 110, 110, 100, 101),
        "pin_bar": _candle(6, 100, 102, 90, 101),
    }
    for pattern_id, candle in examples.items():
        assert pattern_id in _ids(detect_pattern([candle], pattern_id)), pattern_id


@pytest.mark.parametrize(
    ("pattern_id", "positive", "near_miss"),
    [
        ("hammer", _candle(0, 100, 101, 90, 100.5), _candle(0, 100, 101, 99.4, 100.5)),
        ("hanging_man", _candle(0, 101, 102, 90, 100), _candle(0, 100, 102, 90, 101)),
        ("inverted_hammer", _candle(0, 100, 110, 99, 101), _candle(0, 100, 110, 99, 99)),
        ("shooting_star", _candle(0, 101, 110, 99, 100), _candle(0, 100, 110, 99, 101)),
        ("doji", _candle(0, 100, 105, 95, 101), _candle(0, 100, 105, 95, 102)),
        ("dragonfly_doji", _candle(0, 100, 100.5, 90, 100), _candle(0, 100, 102, 90, 100)),
        ("gravestone_doji", _candle(0, 100, 110, 99.5, 100), _candle(0, 100, 110, 98.8, 100)),
        ("long_legged_doji", _candle(0, 100, 106, 94, 100), _candle(0, 100, 109, 94, 100)),
        ("spinning_top", _candle(0, 100, 106, 94, 102), _candle(0, 100, 107, 94, 104)),
        ("bullish_marubozu", _candle(0, 100, 110, 100, 109), _candle(0, 100, 110, 100, 107)),
        ("bearish_marubozu", _candle(0, 110, 110, 100, 101), _candle(0, 110, 110, 100, 103)),
        ("pin_bar", _candle(0, 100, 102, 90, 101), _candle(0, 100, 105, 90, 101)),
    ],
)
def test_single_detector_positive_and_realistic_near_miss(
    pattern_id: str, positive: Candle, near_miss: Candle
) -> None:
    """Bind each single-candle detector to its own positive and near-miss fixture."""
    assert pattern_id in _ids(detect_pattern([positive], pattern_id))
    assert pattern_id not in _ids(detect_pattern([near_miss], pattern_id))


@pytest.mark.parametrize(
    ("pattern_id", "boundary", "config", "outside_config"),
    [
        (
            "hammer",
            _candle(0, 100, 102, 98, 101),
            DetectionConfig(long_wick_ratio=Decimal(2)),
            DetectionConfig(long_wick_ratio=Decimal("2.01")),
        ),
        (
            "hanging_man",
            _candle(0, 101, 102, 98, 100),
            DetectionConfig(long_wick_ratio=Decimal(2)),
            DetectionConfig(long_wick_ratio=Decimal("2.01")),
        ),
        (
            "inverted_hammer",
            _candle(0, 100, 103, 100, 101),
            DetectionConfig(long_wick_ratio=Decimal(2)),
            DetectionConfig(long_wick_ratio=Decimal("2.01")),
        ),
        (
            "shooting_star",
            _candle(0, 101, 103, 100, 100),
            DetectionConfig(long_wick_ratio=Decimal(2)),
            DetectionConfig(long_wick_ratio=Decimal("2.01")),
        ),
        (
            "spinning_top",
            _candle(0, 100, 106, 96, 103),
            DetectionConfig(spinning_top_max_body_ratio=Decimal("0.3")),
            DetectionConfig(spinning_top_max_body_ratio=Decimal("0.299")),
        ),
        (
            "long_legged_doji",
            _candle(0, 100, 105.5, 95.5, 101),
            DetectionConfig(long_legged_min_shadow_ratio=Decimal("0.3")),
            DetectionConfig(long_legged_min_shadow_ratio=Decimal("0.451")),
        ),
        (
            "bullish_marubozu",
            _candle(0, 100, 109, 99, 108),
            DetectionConfig(
                marubozu_body_ratio=Decimal("0.8"),
                marubozu_max_shadow_ratio=Decimal("0.1"),
            ),
            DetectionConfig(
                marubozu_body_ratio=Decimal("0.8"),
                marubozu_max_shadow_ratio=Decimal("0.099"),
            ),
        ),
        (
            "bearish_marubozu",
            _candle(0, 108, 109, 99, 100),
            DetectionConfig(
                marubozu_body_ratio=Decimal("0.8"),
                marubozu_max_shadow_ratio=Decimal("0.1"),
            ),
            DetectionConfig(
                marubozu_body_ratio=Decimal("0.8"),
                marubozu_max_shadow_ratio=Decimal("0.099"),
            ),
        ),
        (
            "pin_bar",
            _candle(0, 100, 102, 98, 101),
            DetectionConfig(long_wick_ratio=Decimal(2)),
            DetectionConfig(long_wick_ratio=Decimal("2.01")),
        ),
    ],
)
def test_single_detector_ratio_boundaries(
    pattern_id: str,
    boundary: Candle,
    config: DetectionConfig,
    outside_config: DetectionConfig,
) -> None:
    """Check inclusive/exclusive shape ratios at the detector's public API."""
    assert pattern_id in _ids(detect_pattern([boundary], pattern_id, config=config))
    assert pattern_id not in _ids(detect_pattern([boundary], pattern_id, config=outside_config))


def test_single_detector_minimum_range_and_zero_range_boundaries() -> None:
    at_minimum = _candle(0, 100, "100.01", 100, 100)
    just_above = _candle(1, 100, "100.011", 100, 100)
    zero_range = _candle(2, 100, 100, 100, 100)
    assert "doji" not in _ids(detect_pattern([at_minimum], "doji"))
    assert "doji" in _ids(detect_pattern([just_above], "doji"))
    assert detect_pattern([zero_range], "doji") == []


def test_fixed_single_shape_boundaries_and_nonbinding_thresholds() -> None:
    hammer_at_short_wick_limit = _candle(6, 100, 102, 98, 101)
    assert "hammer" in _ids(
        detect_pattern(
            [hammer_at_short_wick_limit],
            "hammer",
            config=DetectionConfig(short_wick_ratio=Decimal("0.25")),
        )
    )
    assert "hammer" not in _ids(
        detect_pattern(
            [hammer_at_short_wick_limit],
            "hammer",
            config=DetectionConfig(short_wick_ratio=Decimal("0.249")),
        )
    )

    dragonfly_at_opposite_shadow_limit = _candle(0, 100, 101, 90, 100)
    dragonfly_outside_opposite_shadow_limit = _candle(1, 100, 102, 90, 100)
    assert "dragonfly_doji" in _ids(
        detect_pattern([dragonfly_at_opposite_shadow_limit], "dragonfly_doji")
    )
    assert "dragonfly_doji" not in _ids(
        detect_pattern([dragonfly_outside_opposite_shadow_limit], "dragonfly_doji")
    )

    gravestone_at_opposite_shadow_limit = _candle(2, 100, 109, 99, 101)
    gravestone_outside_opposite_shadow_limit = _candle(3, 100, 109, "98.89", 101)
    assert "gravestone_doji" in _ids(
        detect_pattern([gravestone_at_opposite_shadow_limit], "gravestone_doji")
    )
    assert "gravestone_doji" not in _ids(
        detect_pattern([gravestone_outside_opposite_shadow_limit], "gravestone_doji")
    )

    long_legged_below_exclusive_max = _candle(4, 100, "109.99", 95, 101)
    long_legged_at_exclusive_max = _candle(5, 100, 110, 95, 101)
    assert "long_legged_doji" in _ids(
        detect_pattern([long_legged_below_exclusive_max], "long_legged_doji")
    )
    assert "long_legged_doji" not in _ids(
        detect_pattern([long_legged_at_exclusive_max], "long_legged_doji")
    )


def test_correct_harami_directions_and_body_containment() -> None:
    bullish = [_candle(0, 110, 112, 98, 100), _candle(1, 102, 107, 101, 106)]
    bearish = [_candle(0, 100, 112, 98, 110), _candle(1, 108, 109, 103, 104)]
    assert "bullish_harami" in _ids(detect_two_candle_patterns(bullish))
    assert "bearish_harami" not in _ids(detect_two_candle_patterns(bullish))
    assert "bearish_harami" in _ids(detect_two_candle_patterns(bearish))
    assert "bullish_harami" not in _ids(detect_two_candle_patterns(bearish))


def test_piercing_line_requires_opening_gap_below_prior_range() -> None:
    gapped = [_candle(0, 110, 112, 98, 100), _candle(1, 96, 108, 95, 106)]
    continuous = [_candle(0, 110, 112, 98, 100), _candle(1, 99, 108, 97, 106)]
    assert "piercing_line" in _ids(detect_two_candle_patterns(gapped))
    assert "piercing_line" not in _ids(detect_two_candle_patterns(continuous))


def test_remaining_two_candle_reversals_and_matching_patterns() -> None:
    examples = {
        "bearish_engulfing": [
            _candle(0, 100, 103, 99, 102),
            _candle(1, 103, 104, 98, 99),
        ],
        "bearish_harami": [
            _candle(0, 100, 112, 98, 110),
            _candle(1, 108, 109, 103, 104),
        ],
        "harami_cross": [
            _candle(0, 110, 112, 98, 100),
            _candle(1, 104, 106, 103, 104),
        ],
        "dark_cloud_cover": [
            _candle(0, 100, 112, 98, 110),
            _candle(1, 114, 116, 103, 104),
        ],
        "tweezer_bottom": [
            _candle(0, 100, 103, 95, 96),
            _candle(1, 97, 103, 95, 101),
        ],
        "tweezer_top": [
            _candle(0, 96, 105, 94, 103),
            _candle(1, 102, 105, 94, 98),
        ],
        "bullish_kicker": [
            _candle(0, 100, 101, 90, 91),
            _candle(1, 102, 106, 102, 105),
        ],
        "bearish_kicker": [
            _candle(0, 91, 101, 90, 100),
            _candle(1, 89, 89, 84, 85),
        ],
        "matching_low": [
            _candle(0, 100, 102, 90, 95),
            _candle(1, 98, 100, 92, 95),
        ],
        "matching_high": [
            _candle(0, 90, 101, 89, 100),
            _candle(1, 94, 102, 93, 100),
        ],
    }
    for pattern_id, sequence in examples.items():
        assert pattern_id in _ids(detect_pattern(sequence, pattern_id)), pattern_id


def test_two_candle_engulfing_inside_bar_and_matching_close() -> None:
    candles = [
        _candle(0, 100, 103, 98, 99),
        _candle(1, 98, 104, 97, 103),
        _candle(2, 102, 103, 99, 101),
        _candle(3, 101, 104, 100, 102),
    ]
    results = detect_two_candle_patterns(candles)
    assert "bullish_engulfing" in _ids(results)
    assert "inside_bar" in _ids(results)
    assert "matching_high" not in _ids(results)


def test_tweezer_and_matching_price_tolerances_include_the_boundary() -> None:
    config = DetectionConfig(equal_tolerance=Decimal("0.1"))
    tweezer_top = [
        _candle(0, 100, 105, 98, 104),
        _candle(1, 104, "105.1", 99, 102),
    ]
    matching_low = [
        _candle(0, 102, 105, 99, 100),
        _candle(1, 101, 104, 98, "99.9"),
    ]
    outside_tolerance = [
        _candle(0, 102, 105, 99, 100),
        _candle(1, 101, 104, 98, "99.89"),
    ]
    assert "tweezer_top" in _ids(detect_two_candle_patterns(tweezer_top, config=config))
    assert "matching_low" in _ids(detect_two_candle_patterns(matching_low, config=config))
    assert "matching_low" not in _ids(detect_two_candle_patterns(outside_tolerance, config=config))


@pytest.mark.parametrize(
    ("pattern_id", "positive", "near_miss"),
    [
        (
            "bullish_engulfing",
            [_candle(0, 100, 103, 98, 99), _candle(1, 98, 104, 97, 103)],
            [_candle(0, 100, 103, 98, 99), _candle(1, 99.1, 103, 98, 102)],
        ),
        (
            "bearish_engulfing",
            [_candle(0, 100, 103, 99, 102), _candle(1, 103, 104, 98, 99)],
            [_candle(0, 100, 103, 99, 102), _candle(1, 101.9, 104, 99, 99)],
        ),
        (
            "bullish_harami",
            [_candle(0, 110, 112, 98, 100), _candle(1, 102, 107, 101, 106)],
            [_candle(0, 110, 112, 98, 100), _candle(1, 99, 107, 98, 106)],
        ),
        (
            "bearish_harami",
            [_candle(0, 100, 112, 98, 110), _candle(1, 108, 109, 103, 104)],
            [_candle(0, 100, 112, 98, 110), _candle(1, 111, 113, 103, 104)],
        ),
        (
            "harami_cross",
            [_candle(0, 110, 112, 98, 100), _candle(1, 104, 106, 103, 104)],
            [_candle(0, 110, 112, 98, 100), _candle(1, 104, 106, 103, 105)],
        ),
        (
            "piercing_line",
            [_candle(0, 110, 112, 98, 100), _candle(1, 96, 108, 95, 106)],
            [_candle(0, 110, 112, 98, 100), _candle(1, 99, 108, 97, 106)],
        ),
        (
            "dark_cloud_cover",
            [_candle(0, 100, 112, 98, 110), _candle(1, 114, 116, 103, 104)],
            [_candle(0, 100, 112, 98, 110), _candle(1, 114, 116, 103, 107)],
        ),
        (
            "tweezer_bottom",
            [_candle(0, 100, 103, 95, 96), _candle(1, 97, 103, 95, 101)],
            [_candle(0, 100, 103, 95, 96), _candle(1, 97, 103, 95.01, 101)],
        ),
        (
            "tweezer_top",
            [_candle(0, 96, 105, 94, 103), _candle(1, 102, 105, 94, 98)],
            [_candle(0, 96, 105, 94, 103), _candle(1, 102, 104.99, 94, 98)],
        ),
        (
            "bullish_kicker",
            [_candle(0, 100, 101, 90, 91), _candle(1, 102, 106, 102, 105)],
            [_candle(0, 100, 101, 90, 91), _candle(1, 101, 106, 101, 105)],
        ),
        (
            "bearish_kicker",
            [_candle(0, 91, 101, 90, 100), _candle(1, 89, 89, 84, 85)],
            [_candle(0, 91, 101, 90, 100), _candle(1, 90, 90, 84, 85)],
        ),
        (
            "matching_low",
            [_candle(0, 100, 102, 90, 95), _candle(1, 98, 100, 92, 95)],
            [_candle(0, 100, 102, 90, 95), _candle(1, 98, 100, 92, 96)],
        ),
        (
            "matching_high",
            [_candle(0, 90, 101, 89, 100), _candle(1, 94, 102, 93, 100)],
            [_candle(0, 90, 101, 89, 100), _candle(1, 94, 102, 93, 99)],
        ),
        (
            "inside_bar",
            [_candle(0, 100, 110, 90, 101), _candle(1, 100, 105, 95, 102)],
            [_candle(0, 100, 110, 90, 101), _candle(1, 100, 110, 90, 102)],
        ),
        (
            "outside_bar",
            [_candle(0, 100, 105, 95, 101), _candle(1, 100, 106, 94, 102)],
            [_candle(0, 100, 105, 95, 101), _candle(1, 100, 105, 94, 102)],
        ),
    ],
)
def test_two_candle_detector_positive_and_near_miss(
    pattern_id: str, positive: list[Candle], near_miss: list[Candle]
) -> None:
    """Bind each two-candle ID to its own fixture through the public API."""
    assert pattern_id in _ids(detect_pattern(positive, pattern_id))
    assert pattern_id not in _ids(detect_pattern(near_miss, pattern_id))


def test_two_candle_equality_and_threshold_boundaries() -> None:
    engulfing_equal = [_candle(0, 110, 112, 98, 100), _candle(1, 100, 112, 98, 110)]
    engulfing_inside = [_candle(0, 110, 112, 98, 100), _candle(1, 100.01, 112, 98, 109.99)]
    assert "bullish_engulfing" in _ids(detect_pattern(engulfing_equal, "bullish_engulfing"))
    assert "bullish_engulfing" not in _ids(detect_pattern(engulfing_inside, "bullish_engulfing"))
    bearish_engulfing_equal = [
        _candle(0, 100, 112, 98, 110),
        _candle(1, 110, 112, 98, 100),
    ]
    assert "bearish_engulfing" in _ids(detect_pattern(bearish_engulfing_equal, "bearish_engulfing"))

    harami_smaller = [_candle(0, 110, 112, 98, 100), _candle(1, 100.01, 110, 99, 109.99)]
    harami_equal = [_candle(0, 110, 112, 98, 100), _candle(1, 100, 110, 99, 110)]
    assert "bullish_harami" in _ids(detect_pattern(harami_smaller, "bullish_harami"))
    assert "bullish_harami" not in _ids(detect_pattern(harami_equal, "bullish_harami"))
    bearish_harami_smaller = [
        _candle(0, 100, 112, 98, 110),
        _candle(1, 109.99, 110, 100.01, 100.01),
    ]
    assert "bearish_harami" in _ids(detect_pattern(bearish_harami_smaller, "bearish_harami"))

    piercing_above_midpoint = [
        _candle(0, 110, 112, 98, 100),
        _candle(1, 96, 108, 95, "105.01"),
    ]
    piercing_at_midpoint = [
        _candle(0, 110, 112, 98, 100),
        _candle(1, 96, 108, 95, 105),
    ]
    assert "piercing_line" in _ids(detect_pattern(piercing_above_midpoint, "piercing_line"))
    assert "piercing_line" not in _ids(detect_pattern(piercing_at_midpoint, "piercing_line"))

    dark_cloud_below_midpoint = [
        _candle(0, 100, 112, 98, 110),
        _candle(1, 114, 116, 103, "104.99"),
    ]
    dark_cloud_at_midpoint = [
        _candle(0, 100, 112, 98, 110),
        _candle(1, 114, 116, 103, 105),
    ]
    assert "dark_cloud_cover" in _ids(detect_pattern(dark_cloud_below_midpoint, "dark_cloud_cover"))
    assert "dark_cloud_cover" not in _ids(
        detect_pattern(dark_cloud_at_midpoint, "dark_cloud_cover")
    )

    kicker_gap = [_candle(0, 100, 101, 90, 91), _candle(1, "101.01", 106, 101, 105)]
    kicker_touch = [_candle(0, 100, 101, 90, 91), _candle(1, 101, 106, 101, 105)]
    assert "bullish_kicker" in _ids(detect_pattern(kicker_gap, "bullish_kicker"))
    assert "bullish_kicker" not in _ids(detect_pattern(kicker_touch, "bullish_kicker"))
    bearish_kicker_gap = [
        _candle(0, 91, 101, 90, 100),
        _candle(1, "89.99", 90, 84, 85),
    ]
    bearish_kicker_touch = [
        _candle(0, 91, 101, 90, 100),
        _candle(1, 90, 90, 84, 85),
    ]
    assert "bearish_kicker" in _ids(detect_pattern(bearish_kicker_gap, "bearish_kicker"))
    assert "bearish_kicker" not in _ids(detect_pattern(bearish_kicker_touch, "bearish_kicker"))

    config = DetectionConfig(equal_tolerance=Decimal("0.1"))
    tweezer_bottom_equal = [
        _candle(0, 100, 103, 95, 96),
        _candle(1, 97, 103, "95.1", 101),
    ]
    tweezer_bottom_outside = [
        _candle(0, 100, 103, 95, 96),
        _candle(1, 97, 103, "95.11", 101),
    ]
    assert "tweezer_bottom" in _ids(
        detect_pattern(tweezer_bottom_equal, "tweezer_bottom", config=config)
    )
    assert "tweezer_bottom" not in _ids(
        detect_pattern(tweezer_bottom_outside, "tweezer_bottom", config=config)
    )
    matching_high_equal = [
        _candle(0, 90, 101, 89, 100),
        _candle(1, "99.5", "101.1", 93, "100.1"),
    ]
    matching_high_outside = [
        _candle(0, 90, 101, 89, 100),
        _candle(1, "99.5", "101.11", 93, "100.11"),
    ]
    assert "matching_high" in _ids(
        detect_pattern(matching_high_equal, "matching_high", config=config)
    )
    assert "matching_high" not in _ids(
        detect_pattern(matching_high_outside, "matching_high", config=config)
    )

    inside_edge = [_candle(0, 100, 110, 90, 101), _candle(1, 100, 110, 95, 105)]
    inside_equal = [_candle(0, 100, 110, 90, 101), _candle(1, 100, 110, 90, 105)]
    assert "inside_bar" in _ids(detect_pattern(inside_edge, "inside_bar"))
    assert "inside_bar" not in _ids(detect_pattern(inside_equal, "inside_bar"))

    outside_edge = [_candle(0, 100, 105, 95, 101), _candle(1, 100, 105, 94, 102)]
    outside_strict = [_candle(0, 100, 105, 95, 101), _candle(1, 100, 106, 94, 102)]
    assert "outside_bar" not in _ids(detect_pattern(outside_edge, "outside_bar"))
    assert "outside_bar" in _ids(detect_pattern(outside_strict, "outside_bar"))


def test_three_candle_star_variants_are_distinct() -> None:
    ordinary = [
        _candle(0, 105, 106, 98, 99),
        _candle(1, 98, 100, 97, 99),
        _candle(2, 98, 106, 97, 104),
    ]
    doji = [
        _candle(0, 105, 106, 98, 99),
        _candle(1, 98.9, 100, 97, 98.95),
        _candle(2, 98, 106, 97, 104),
    ]
    assert "morning_star" in _ids(detect_pattern(ordinary, "morning_star"))
    assert "morning_doji_star" in _ids(detect_pattern(doji, "morning_doji_star"))
    assert "morning_star" not in _ids(detect_pattern(doji, "morning_star"))


def test_three_inside_and_outside_require_three_bar_confirmation() -> None:
    inside = [
        _candle(0, 110, 112, 97, 100),
        _candle(1, 102, 108, 101, 106),
        _candle(2, 106, 112, 105, 111),
    ]
    outside = [
        _candle(0, 110, 112, 98, 100),
        _candle(1, 97, 114, 96, 113),
        _candle(2, 113, 116, 112, 115),
    ]
    assert "three_inside_up" in _ids(detect_pattern(inside, "three_inside_up"))
    assert "three_inside_up" not in _ids(detect_pattern(inside[:2], "three_inside_up"))
    assert "three_outside_up" in _ids(detect_pattern(outside, "three_outside_up"))


def test_abandoned_baby_requires_gaps_between_whole_ranges() -> None:
    valid = [
        _candle(0, 105, 106, 100, 101),
        _candle(1, 99, 99.5, 98.5, 99.1),
        _candle(2, 101, 107, 100, 106),
    ]
    invalid = [
        _candle(0, 105, 106, 100, 101),
        _candle(1, 100, 101, 99, 100.1),
        _candle(2, 101, 107, 100, 106),
    ]
    assert "bullish_abandoned_baby" in _ids(detect_pattern(valid, "bullish_abandoned_baby"))
    assert "bullish_abandoned_baby" not in _ids(detect_pattern(invalid, "bullish_abandoned_baby"))


def test_remaining_multi_candle_patterns_have_confirmed_sequences() -> None:
    examples = {
        "evening_star": [
            _candle(0, 99, 106, 98, 105),
            _candle(1, 105, 106, 103, 104),
            _candle(2, 105, 106, 99, 100),
        ],
        "evening_doji_star": [
            _candle(0, 99, 106, 98, 105),
            _candle(1, 102, 104, 101, 102),
            _candle(2, 105, 106, 99, 100),
        ],
        "three_inside_down": [
            _candle(0, 100, 112, 98, 110),
            _candle(1, 108, 109, 103, 104),
            _candle(2, 104, 105, 98, 99),
        ],
        "three_outside_down": [
            _candle(0, 100, 112, 98, 110),
            _candle(1, 113, 114, 97, 99),
            _candle(2, 100, 101, 94, 95),
        ],
        "rising_three_methods": [
            _candle(0, 100, 105, 99, 103),
            _candle(1, 103, 104, 101, 102),
            _candle(2, 102.5, 103, 100, 101.5),
            _candle(3, 102, 102.5, 100.5, 101),
            _candle(4, 101, 107, 100.5, 106),
        ],
        "falling_three_methods": [
            _candle(0, 105, 106, 100, 102),
            _candle(1, 102, 104, 101, 103),
            _candle(2, 102.5, 105, 102, 104),
            _candle(3, 103, 104.5, 102.5, 104),
            _candle(4, 105, 105.5, 98, 99),
        ],
        "three_white_soldiers": [
            _candle(0, 100, 103, 99, 102),
            _candle(1, 101, 105, 100, 104),
            _candle(2, 103, 107, 102, 106),
        ],
        "three_black_crows": [
            _candle(0, 106, 107, 103, 104),
            _candle(1, 105, 106, 101, 102),
            _candle(2, 103, 104, 99, 100),
        ],
        "bullish_three_line_strike": [
            _candle(0, 100, 103, 99, 102),
            _candle(1, 101, 105, 100, 104),
            _candle(2, 103, 107, 102, 106),
            _candle(3, 108, 109, 98, 99),
        ],
        "bearish_three_line_strike": [
            _candle(0, 106, 107, 103, 104),
            _candle(1, 105, 106, 101, 102),
            _candle(2, 103, 104, 99, 100),
            _candle(3, 99, 108, 98, 107),
        ],
        "bearish_abandoned_baby": [
            _candle(0, 100, 106, 99, 105),
            _candle(1, 107, 107.5, 106.5, 107),
            _candle(2, 106, 106.2, 99, 100),
        ],
    }
    for pattern_id, candles in examples.items():
        detected = detect_pattern(candles, pattern_id)
        assert pattern_id in _ids(detected), pattern_id
        assert all(result.confirmation_timestamp == candles[-1].timestamp for result in detected)
        last = candles[-1]
        stalled_confirmation = candles[:-1] + [
            _candle(len(candles) - 1, last.open, last.high, last.low, last.open)
        ]
        assert pattern_id not in _ids(detect_pattern(stalled_confirmation, pattern_id)), pattern_id


def test_soldier_and_crow_close_position_boundaries() -> None:
    soldiers_at_boundary = [
        _candle(0, 92, 100, 90, "96.6"),
        _candle(1, 95, 101, 91, "97.6"),
        _candle(2, 96, 102, 92, "98.6"),
    ]
    soldiers_outside = [
        _candle(0, 92, 100, 90, "96.59"),
        soldiers_at_boundary[1],
        soldiers_at_boundary[2],
    ]
    assert "three_white_soldiers" in _ids(
        detect_pattern(soldiers_at_boundary, "three_white_soldiers")
    )
    assert "three_white_soldiers" not in _ids(
        detect_pattern(soldiers_outside, "three_white_soldiers")
    )

    crows_at_boundary = [
        _candle(0, 108, 110, 100, "103.4"),
        _candle(1, 105, "108.6", "98.6", 102),
        _candle(2, 103, 105, 95, "98.4"),
    ]
    crows_outside = [
        crows_at_boundary[0],
        _candle(1, 105, "108.6", "98.6", "102.01"),
        crows_at_boundary[2],
    ]
    assert "three_black_crows" in _ids(detect_pattern(crows_at_boundary, "three_black_crows"))
    assert "three_black_crows" not in _ids(detect_pattern(crows_outside, "three_black_crows"))


def test_swing_confirmation_and_market_structure_use_pivots() -> None:
    candles = [
        _candle(0, 10, 11, 9, 10),
        _candle(1, 10, 14, 9, 13),
        _candle(2, 12, 13, 10, 11),
        _candle(3, 11, 12, 8, 9),
        _candle(4, 9, 11, 9, 10),
        _candle(5, 10, 15, 9, 14),
        _candle(6, 13, 14, 10, 11),
        _candle(7, 11, 12, 9, 10),
    ]
    results = detect_price_action(candles, config=DetectionConfig(confirmation_lookback=1))
    higher_high = next(result for result in results if result.pattern_id == "hh")
    assert higher_high.start_timestamp == candles[1].timestamp
    assert higher_high.end_timestamp == candles[5].timestamp
    assert higher_high.confirmation_timestamp == candles[6].timestamp
    assert higher_high.metadata["swing_confirmation"] == candles[6].timestamp
    assert not any(
        result.pattern_id in {"hh", "hl", "lh", "ll"}
        and result.start_timestamp == candles[0].timestamp
        for result in results
    )


def test_swing_pivot_tie_is_allowed_on_left_but_not_right() -> None:
    tied_left_high = [
        _candle(0, 10, 12, 9, 10),
        _candle(1, 10, 12, 9, 11),
        _candle(2, 10, 11, 9, 10),
    ]
    tied_right_high = [
        _candle(0, 10, 11, 9, 10),
        _candle(1, 10, 12, 9, 11),
        _candle(2, 10, 12, 9, 10),
    ]
    tied_left_low = [
        _candle(0, 10, 11, 8, 10),
        _candle(1, 10, 11, 8, 9),
        _candle(2, 10, 11, 9, 10),
    ]
    tied_right_low = [
        _candle(0, 10, 11, 9, 10),
        _candle(1, 10, 11, 8, 9),
        _candle(2, 10, 11, 8, 10),
    ]
    config = DetectionConfig(confirmation_lookback=1)
    high = detect_pattern(tied_left_high, "swing_high", config=config)
    low = detect_pattern(tied_left_low, "swing_low", config=config)
    assert len(high) == 1 and high[0].confirmation_timestamp == tied_left_high[2].timestamp
    assert len(low) == 1 and low[0].confirmation_timestamp == tied_left_low[2].timestamp
    assert detect_pattern(tied_right_high, "swing_high", config=config) == []
    assert detect_pattern(tied_right_low, "swing_low", config=config) == []


def test_confirmed_swings_classify_up_down_and_range_structure() -> None:
    rising_levels = [
        (10, 11, 9, 10),
        (12, 14, 10, 13),
        (11, 12, 10, 11),
        (12, 13, 8, 9),
        (10, 12, 9, 11),
        (13, 15, 10, 14),
        (12, 13, 10, 11),
        (13, 14, 9, 10),
        (12, 13, 11, 12),
        (14, 16, 12, 15),
        (13, 14, 12, 13),
    ]
    rising = [_candle(index, *level) for index, level in enumerate(rising_levels)]
    falling = [
        _candle(
            index,
            30 - close,
            30 - low,
            30 - high,
            30 - open_,
        )
        for index, (open_, high, low, close) in enumerate(rising_levels)
    ]
    flat_levels = [
        (10, 12, 10, 11),
        (11, 14, 9, 13),
        (11, 13, 9, 11),
        (10, 13, 8, 9),
        (9, 13, 9, 11),
        (11, 14, 9, 13),
        (11, 13, 9, 11),
        (10, 13, 8, 9),
        (9, 13, 9, 11),
        (11, 14, 9, 13),
        (11, 13, 9, 11),
    ]
    flat = [_candle(index, *level) for index, level in enumerate(flat_levels)]
    config = DetectionConfig(confirmation_lookback=1)
    rising_ids = _ids(detect_price_action(rising, config=config))
    falling_ids = _ids(detect_price_action(falling, config=config))
    flat_ids = _ids(detect_price_action(flat, config=config))
    assert {"hh", "hl", "trend_up"} <= rising_ids
    assert {"lh", "ll", "trend_down"} <= falling_ids
    assert "trend_range" in flat_ids
    assert "insufficient_trend_structure" in rising_ids

    for pattern_id in ("hh", "hl", "trend_up"):
        assert pattern_id in _ids(detect_pattern(rising, pattern_id, config=config))
    for pattern_id in ("lh", "ll", "trend_down"):
        assert pattern_id in _ids(detect_pattern(falling, pattern_id, config=config))
    assert "trend_range" in _ids(detect_pattern(flat, "trend_range", config=config))
    assert "insufficient_trend_structure" in _ids(
        detect_pattern(rising, "insufficient_trend_structure", config=config)
    )
    assert "hh" not in _ids(detect_pattern(flat, "hh", config=config))
    assert "hl" not in _ids(detect_pattern(flat, "hl", config=config))
    assert "lh" not in _ids(detect_pattern(flat, "lh", config=config))
    assert "ll" not in _ids(detect_pattern(flat, "ll", config=config))
    assert "trend_up" not in _ids(detect_pattern(falling, "trend_up", config=config))
    assert "trend_down" not in _ids(detect_pattern(rising, "trend_down", config=config))
    assert "trend_range" not in _ids(detect_pattern(rising, "trend_range", config=config))


def test_double_top_requires_two_confirmed_peaks_neckline_and_break() -> None:
    levels = [
        (10, 11, 9, 10),
        (10, 12, 9, 11),
        (10, 11, 9, 10),
        (9, 10, 8, 9),
        (9, 11, 9, 10),
        (10, 12, 9, 11),
        (10, 11, 9, 10),
        (8, 9, 7, 7),
    ]
    candles = [_candle(i, *level) for i, level in enumerate(levels)]
    results = detect_multi_candle_patterns(
        candles,
        config=DetectionConfig(
            confirmation_lookback=1,
            support_zone_tolerance=Decimal("0.1"),
            breakout_buffer=Decimal("0.1"),
        ),
    )
    double_top = next(result for result in results if result.pattern_id == "double_top")
    assert double_top.start_timestamp == candles[1].timestamp
    assert double_top.end_timestamp == candles[5].timestamp
    assert double_top.confirmation_timestamp == candles[7].timestamp
    assert "double_top" in _ids(
        detect_pattern(
            candles,
            "double_top",
            config=DetectionConfig(
                confirmation_lookback=1,
                support_zone_tolerance=Decimal("0.1"),
                breakout_buffer=Decimal("0.1"),
            ),
        )
    )
    assert "double_top" not in _ids(
        detect_pattern(
            candles[:7],
            "double_top",
            config=DetectionConfig(
                confirmation_lookback=1,
                support_zone_tolerance=Decimal("0.1"),
                breakout_buffer=Decimal("0.1"),
            ),
        )
    )


def test_double_bottom_requires_confirmed_troughs_neckline_and_break() -> None:
    levels = [
        (10, 11, 9, 10),
        (9, 10, 8, 9),
        (10, 11, 9, 10),
        (11, 12, 9, 11),
        (10, 11, 9, 10),
        (9, 10, 8, 9),
        (10, 11, 9, 10),
        (12, 14, 11, 13),
    ]
    candles = [_candle(index, *level) for index, level in enumerate(levels)]
    results = detect_multi_candle_patterns(
        candles,
        config=DetectionConfig(
            confirmation_lookback=1,
            support_zone_tolerance=Decimal("0.1"),
            breakout_buffer=Decimal("0.1"),
        ),
    )
    double_bottom = next(result for result in results if result.pattern_id == "double_bottom")
    assert double_bottom.start_timestamp == candles[1].timestamp
    assert double_bottom.end_timestamp == candles[5].timestamp
    assert double_bottom.confirmation_timestamp == candles[7].timestamp
    assert "double_bottom" in _ids(
        detect_pattern(
            candles,
            "double_bottom",
            config=DetectionConfig(
                confirmation_lookback=1,
                support_zone_tolerance=Decimal("0.1"),
                breakout_buffer=Decimal("0.1"),
            ),
        )
    )
    assert "double_bottom" not in _ids(
        detect_pattern(
            candles[:7],
            "double_bottom",
            config=DetectionConfig(
                confirmation_lookback=1,
                support_zone_tolerance=Decimal("0.1"),
                breakout_buffer=Decimal("0.1"),
            ),
        )
    )


def test_mother_bar_and_fakey_require_containment_then_false_break_closeback() -> None:
    candles = [
        _candle(0, 100, 110, 90, 101),
        _candle(1, 100, 105, 95, 102),
        _candle(2, 104, 112, 101, 111),
        _candle(3, 110, 111, 100, 108),
    ]
    results = detect_price_action(candles)
    mother = next(result for result in results if result.pattern_id == "mother_bar")
    fakey = next(result for result in results if result.pattern_id == "bearish_fakey")
    assert mother.start_timestamp == candles[0].timestamp
    assert mother.confirmation_timestamp == candles[1].timestamp
    assert fakey.confirmation_timestamp == candles[3].timestamp
    assert "bearish_fakey" not in _ids(detect_price_action(candles[:3]))
    assert "mother_bar" in _ids(detect_pattern(candles[:2], "mother_bar"))
    escaped_child = [candles[0], _candle(1, 100, 111, 95, 102)]
    assert "mother_bar" not in _ids(detect_pattern(escaped_child, "mother_bar"))


def test_support_zone_uses_two_confirmed_touches_and_breakout_retest() -> None:
    levels = [
        (10, 12, 9, 10),
        (9, 10, 8, 9),
        (10, 12, 9, 11),
        (9, 11, 8.2, 9),
        (10, 12, 9, 11),
        (8.5, 10, 8, 8.2),
        (7.5, 9, 7, 7.5),
        (7.3, 8.2, 7.1, 7.5),
    ]
    candles = [_candle(index, *level) for index, level in enumerate(levels)]
    config = DetectionConfig(
        confirmation_lookback=1,
        support_zone_tolerance=Decimal("0.3"),
        breakout_buffer=Decimal("0.1"),
        pattern_lookback=10,
    )
    results = detect_price_action(candles, config=config)
    zone = next(result for result in results if result.pattern_id == "support_zone")
    assert zone.details["touch_count"] == 2
    assert zone.confirmation_timestamp == candles[4].timestamp
    breakdown = next(
        result for result in results if result.pattern_id == "bearish_support_breakdown"
    )
    assert breakdown.confirmation_timestamp == candles[6].timestamp
    retest = next(result for result in results if result.pattern_id == "bearish_support_retest")
    assert retest.confirmation_timestamp == candles[7].timestamp
    assert "support_zone" not in _ids(detect_price_action(candles[:3], config=config))
    assert "support_zone" in _ids(detect_pattern(candles, "support_zone", config=config))
    failed_retest = candles[:-1] + [_candle(7, 7.6, 8.2, 7.1, 8.0)]
    assert "failed_bearish_breakdown" in _ids(detect_price_action(failed_retest, config=config))
    assert "bearish_support_retest" in _ids(
        detect_pattern(candles, "bearish_support_retest", config=config)
    )
    assert "failed_bearish_breakdown" in _ids(
        detect_pattern(failed_retest, "failed_bearish_breakdown", config=config)
    )


def test_support_zone_touch_tolerance_is_inclusive() -> None:
    levels = [
        (10, 12, 9, 10),
        (9, 10, 8, 9),
        (10, 12, 9, 11),
        (9, 11, 8.2, 9),
        (10, 12, 9, 11),
    ]
    candles = [_candle(index, *level) for index, level in enumerate(levels)]
    inclusive = DetectionConfig(
        confirmation_lookback=1,
        support_zone_tolerance=Decimal("0.2"),
    )
    just_outside = DetectionConfig(
        confirmation_lookback=1,
        support_zone_tolerance=Decimal("0.199"),
    )
    assert "support_zone" in _ids(detect_pattern(candles, "support_zone", config=inclusive))
    assert "support_zone" not in _ids(detect_pattern(candles, "support_zone", config=just_outside))


def test_support_zone_reports_a_later_confirmed_pivot_touch() -> None:
    levels = [
        (10, 12, 9, 10),
        (9, 10, 8, 9),
        (10, 12, 9, 11),
        (9, 11, 8.2, 9),
        (10, 12, 9, 11),
        (9, 10, 8.1, 9),
        (10, 12, 9, 11),
    ]
    candles = [_candle(index, *level) for index, level in enumerate(levels)]
    config = DetectionConfig(
        confirmation_lookback=1,
        support_zone_tolerance=Decimal("0.3"),
    )
    results = detect_price_action(candles, config=config)
    touch = next(result for result in results if result.pattern_id == "support_zone_touch")
    assert touch.details["touch_count"] == 3
    assert touch.confirmation_timestamp == candles[6].timestamp
    assert "support_zone_touch" in _ids(
        detect_pattern(candles, "support_zone_touch", config=config)
    )
    assert "support_zone_touch" not in _ids(
        detect_pattern(candles[:5], "support_zone_touch", config=config)
    )


def test_resistance_zone_breakout_retest_failure_and_rejection_events() -> None:
    levels = [
        (10, 11, 9, 10),
        (11, 12, 10, 11),
        (10, 11, 9, 10),
        (10, 11, 9, 10),
        (10, 11, 9, 10),
        (11, 12, 10, 11),
        (10, 11, 9, 10),
        (11.5, 12, 11.3, 11.4),
        (10, 11, 9, 10),
        (12, 13, 11, 12.5),
        (12.2, 12.5, 11.9, 12.4),
    ]
    candles = [_candle(index, *level) for index, level in enumerate(levels)]
    config = DetectionConfig(
        confirmation_lookback=1,
        support_zone_tolerance=Decimal("0.3"),
        breakout_buffer=Decimal("0.1"),
    )
    results = detect_price_action(candles, config=config)
    assert {
        "resistance_zone",
        "resistance_zone_touch",
        "resistance_rejection",
        "bullish_resistance_breakout",
        "bullish_resistance_retest",
    } <= _ids(results)

    resistance = next(item for item in results if item.pattern_id == "resistance_zone")
    resistance_touch = next(item for item in results if item.pattern_id == "resistance_zone_touch")
    rejection = next(item for item in results if item.pattern_id == "resistance_rejection")
    breakout = next(item for item in results if item.pattern_id == "bullish_resistance_breakout")
    retest = next(item for item in results if item.pattern_id == "bullish_resistance_retest")
    assert resistance.confirmation_timestamp == candles[6].timestamp
    assert resistance_touch.confirmation_timestamp == candles[8].timestamp
    assert rejection.confirmation_timestamp == candles[7].timestamp
    assert breakout.confirmation_timestamp == candles[9].timestamp
    assert retest.confirmation_timestamp == candles[10].timestamp
    for pattern_id in (
        "resistance_zone",
        "resistance_zone_touch",
        "resistance_rejection",
        "bullish_resistance_breakout",
        "bullish_resistance_retest",
    ):
        assert pattern_id in _ids(detect_pattern(candles, pattern_id, config=config))
    assert "resistance_zone_touch" not in _ids(
        detect_pattern(candles[:7], "resistance_zone_touch", config=config)
    )
    assert "bullish_resistance_breakout" not in _ids(
        detect_pattern(candles[:9], "bullish_resistance_breakout", config=config)
    )

    failed_retest = candles[:-1] + [_candle(10, 12.2, 12.5, 11.9, 12.1)]
    assert "failed_bullish_breakout" in _ids(detect_price_action(failed_retest, config=config))
    assert "failed_bullish_breakout" in _ids(
        detect_pattern(failed_retest, "failed_bullish_breakout", config=config)
    )
    assert "failed_bullish_breakout" not in _ids(
        detect_pattern(candles[:10], "failed_bullish_breakout", config=config)
    )
    resistance_prefix = candles[:9]
    breakout_at_buffer = resistance_prefix + [_candle(9, 12, 13, 11, "12.4")]
    breakout_beyond_buffer = resistance_prefix + [_candle(9, 12, 13, 11, "12.41")]
    assert "bullish_resistance_breakout" not in _ids(
        detect_pattern(
            breakout_at_buffer,
            "bullish_resistance_breakout",
            config=config,
        )
    )
    beyond_result = detect_pattern(
        breakout_beyond_buffer,
        "bullish_resistance_breakout",
        config=config,
    )
    assert len(beyond_result) == 1
    assert beyond_result[0].confirmation_timestamp == breakout_beyond_buffer[-1].timestamp


def test_support_zone_rejection_requires_a_long_lower_shadow() -> None:
    levels = [
        (10, 12, 9, 10),
        (9, 10, 8, 9),
        (10, 12, 9, 11),
        (9, 11, 8.2, 9),
        (10, 12, 9, 11),
        (10, 10.2, 7.8, 9.8),
    ]
    candles = [_candle(index, *level) for index, level in enumerate(levels)]
    config = DetectionConfig(
        confirmation_lookback=1,
        support_zone_tolerance=Decimal("0.3"),
        rejection_min_shadow_ratio=Decimal("0.5"),
    )
    results = detect_price_action(candles, config=config)
    assert "support_rejection" in _ids(results)
    assert "bearish_support_breakdown" not in _ids(results)


def test_rejection_wick_ratio_boundary_for_both_zone_directions() -> None:
    support_prefix = [
        _candle(0, 10, 12, 9, 10),
        _candle(1, 9, 10, 8, 9),
        _candle(2, 10, 12, 9, 11),
        _candle(3, 9, 11, 8.2, 9),
        _candle(4, 10, 12, 9, 11),
    ]
    resistance_prefix = [
        _candle(0, 10, 11, 9, 10),
        _candle(1, 11, 12, 10, 11),
        _candle(2, 10, 11, 9, 10),
        _candle(3, 10, 11, 9, 10),
        _candle(4, 10, 11, 9, 10),
        _candle(5, 11, 12, 10, 11),
        _candle(6, 10, 11, 9, 10),
    ]
    config = DetectionConfig(
        confirmation_lookback=1,
        support_zone_tolerance=Decimal("0.3"),
        rejection_min_shadow_ratio=Decimal("0.5"),
    )
    support_equal = support_prefix + [_candle(5, 8.7, 9.7, 7.7, 8.7)]
    support_below = support_prefix + [_candle(5, 8.7, 10, 7.7, 8.7)]
    assert "support_rejection" in _ids(
        detect_pattern(support_equal, "support_rejection", config=config)
    )
    assert "support_rejection" not in _ids(
        detect_pattern(support_below, "support_rejection", config=config)
    )

    resistance_equal = resistance_prefix + [_candle(7, 11.5, 12.5, 10.5, 11.5)]
    resistance_below = resistance_prefix + [_candle(7, 11.5, 12.5, 9.5, 11.5)]
    assert "resistance_rejection" in _ids(
        detect_pattern(resistance_equal, "resistance_rejection", config=config)
    )
    assert "resistance_rejection" not in _ids(
        detect_pattern(resistance_below, "resistance_rejection", config=config)
    )


def test_breakout_requires_a_close_not_an_intrabar_breach() -> None:
    levels = [
        (10, 12, 9, 10),
        (9, 10, 8, 9),
        (10, 12, 9, 11),
        (9, 11, 8.2, 9),
        (10, 12, 9, 11),
        (8.5, 10, 8, 8.2),
        (8, 9, 7, 8),
    ]
    candles = [_candle(index, *level) for index, level in enumerate(levels)]
    config = DetectionConfig(
        confirmation_lookback=1,
        support_zone_tolerance=Decimal("0.3"),
        breakout_buffer=Decimal("0.1"),
    )
    assert "bearish_support_breakdown" not in _ids(detect_price_action(candles, config=config))


def test_support_breakdown_buffer_is_strict_at_equality() -> None:
    levels = [
        (10, 12, 9, 10),
        (9, 10, 8, 9),
        (10, 12, 9, 11),
        (9, 11, 8.2, 9),
        (10, 12, 9, 11),
        (8.5, 10, 8, 8.2),
    ]
    prefix = [_candle(index, *level) for index, level in enumerate(levels)]
    config = DetectionConfig(
        confirmation_lookback=1,
        support_zone_tolerance=Decimal("0.3"),
        breakout_buffer=Decimal("0.1"),
    )
    at_buffer = prefix + [_candle(6, 7.8, 9, 7.1, 7.6)]
    beyond_buffer = prefix + [_candle(6, 7.8, 9, 7.1, "7.59")]
    assert "bearish_support_breakdown" not in _ids(
        detect_pattern(at_buffer, "bearish_support_breakdown", config=config)
    )
    result = detect_pattern(beyond_buffer, "bearish_support_breakdown", config=config)
    assert len(result) == 1
    assert result[0].confirmation_timestamp == beyond_buffer[-1].timestamp


def test_fakey_requires_a_close_back_inside_and_correct_direction() -> None:
    bullish = [
        _candle(0, 100, 110, 90, 101),
        _candle(1, 100, 105, 95, 102),
        _candle(2, 88, 99, 87, 89),
        _candle(3, 89, 100, 89, 92),
    ]
    no_closeback = bullish[:3] + [_candle(3, 88, 95, 87, 88)]
    assert "bullish_fakey" in _ids(detect_price_action(bullish))
    assert "bullish_fakey" not in _ids(detect_price_action(no_closeback))
    assert "bullish_fakey" not in _ids(detect_price_action(bullish[:3]))


def test_fakey_requires_a_completed_close_beyond_the_mother_bar() -> None:
    close_on_boundary = [
        _candle(0, 100, 110, 90, 101),
        _candle(1, 100, 105, 95, 102),
        _candle(2, 109, 112, 108, "110.1"),
        _candle(3, 108, 111, 100, 107),
    ]
    config = DetectionConfig(breakout_buffer=Decimal("0.1"))
    assert "bearish_fakey" not in _ids(detect_price_action(close_on_boundary, config=config))

    wick_only = close_on_boundary[:2] + [
        _candle(2, 109, 112, 108, 109),
        close_on_boundary[3],
    ]
    assert "bearish_fakey" not in _ids(detect_price_action(wick_only, config=config))

    bullish_template = close_on_boundary[:2]
    bullish_at_buffer = bullish_template + [
        _candle(2, 89, 90, 88, "89.9"),
        _candle(3, 89, 95, 89, 92),
    ]
    bullish_beyond_buffer = bullish_template + [
        _candle(2, 89, 90, 88, "89.89"),
        _candle(3, 89, 95, 89, 92),
    ]
    assert "bullish_fakey" not in _ids(
        detect_pattern(bullish_at_buffer, "bullish_fakey", config=config)
    )
    bullish_confirmed = detect_pattern(
        bullish_beyond_buffer,
        "bullish_fakey",
        config=config,
    )
    assert len(bullish_confirmed) == 1
    assert bullish_confirmed[0].confirmation_timestamp == bullish_beyond_buffer[-1].timestamp


def test_inside_bar_breakout_requires_a_close_beyond_mother_range() -> None:
    breakout = [
        _candle(0, 100, 110, 90, 101),
        _candle(1, 100, 105, 95, 102),
        _candle(2, 108, 115, 106, 114),
    ]
    wick_only = [
        _candle(0, 100, 110, 90, 101),
        _candle(1, 100, 105, 95, 102),
        _candle(2, 108, 115, 106, 109),
    ]
    result = next(
        item for item in detect_price_action(breakout) if item.pattern_id == "inside_bar_breakout"
    )
    assert result.confirmation_timestamp == breakout[2].timestamp
    assert "inside_bar_breakout" not in _ids(detect_price_action(wick_only))
    bearish = breakout[:2] + [_candle(2, 92, 94, 85, 86)]
    bearish_result = next(
        result
        for result in detect_price_action(bearish)
        if result.pattern_id == "inside_bar_breakout"
    )
    assert bearish_result.direction == "BEARISH"

    config = DetectionConfig(breakout_buffer=Decimal("0.1"))
    at_buffer = breakout[:2] + [_candle(2, 108, 111, 106, "110.1")]
    beyond_buffer = breakout[:2] + [_candle(2, 108, 111, 106, "110.11")]
    assert "inside_bar_breakout" not in _ids(
        detect_pattern(at_buffer, "inside_bar_breakout", config=config)
    )
    confirmed = detect_pattern(beyond_buffer, "inside_bar_breakout", config=config)
    assert len(confirmed) == 1
    assert confirmed[0].confirmation_timestamp == beyond_buffer[-1].timestamp


def test_price_action_detects_directional_pullbacks_and_supply_demand() -> None:
    bullish_pullback = [
        _candle(0, 100, 110, 99, 108),
        _candle(1, 108, 109, 104, 105),
        _candle(2, 106, 112, 105, 111),
    ]
    bearish_pullback = [
        _candle(0, 110, 111, 99, 100),
        _candle(1, 102, 105, 101, 104),
        _candle(2, 100, 101, 98, 98.5),
    ]
    assert {
        result.direction
        for result in detect_price_action(bullish_pullback)
        if result.pattern_id == "pullback"
    } == {"BULLISH"}
    assert {
        result.direction
        for result in detect_price_action(bearish_pullback)
        if result.pattern_id == "pullback"
    } == {"BEARISH"}
    assert "pullback" in _ids(detect_pattern(bullish_pullback, "pullback"))
    assert "pullback" in _ids(detect_pattern(bearish_pullback, "pullback"))
    bullish_no_followthrough = bullish_pullback[:-1] + [_candle(2, 106, 112, 105, 110)]
    bearish_no_followthrough = bearish_pullback[:-1] + [_candle(2, 100, 101, 98, 99)]
    assert "pullback" not in _ids(detect_pattern(bullish_no_followthrough, "pullback"))
    assert "pullback" not in _ids(detect_pattern(bearish_no_followthrough, "pullback"))

    demand = [
        _candle(0, 100, 105, 95, 101, volume=100),
        _candle(1, 101, 121, 100, 120, volume=200),
    ]
    supply = [
        _candle(0, 100, 105, 95, 101, volume=100),
        _candle(1, 104, 105, 84, 85, volume=200),
    ]
    no_volume_expansion = [
        demand[0],
        _candle(1, 101, 121, 100, 120, volume=100),
    ]
    supply_no_volume_expansion = [
        supply[0],
        _candle(1, 104, 105, 84, 85, volume=100),
    ]
    assert "demand_zone" in _ids(detect_price_action(demand))
    assert "supply_zone" in _ids(detect_price_action(supply))
    assert "demand_zone" in _ids(detect_pattern(demand, "demand_zone"))
    assert "supply_zone" in _ids(detect_pattern(supply, "supply_zone"))
    assert "demand_zone" not in _ids(detect_pattern(no_volume_expansion, "demand_zone"))
    assert "supply_zone" not in _ids(detect_pattern(supply_no_volume_expansion, "supply_zone"))


def test_supply_demand_body_impulse_and_volume_boundaries() -> None:
    demand_boundary = [
        _candle(0, 100, 105, 95, 103, volume=100),
        _candle(1, 103, 118, 100, 118, volume=101),
    ]
    demand_large_base = [
        _candle(0, 100, 105, 95, "103.01", volume=100),
        demand_boundary[1],
    ]
    demand_small_impulse = [
        demand_boundary[0],
        _candle(1, 103, 118, 100, "117.99", volume=101),
    ]
    demand_no_volume = [
        demand_boundary[0],
        _candle(1, 103, 118, 100, 118, volume=100),
    ]
    assert "demand_zone" in _ids(detect_pattern(demand_boundary, "demand_zone"))
    assert "demand_zone" not in _ids(detect_pattern(demand_large_base, "demand_zone"))
    assert "demand_zone" not in _ids(detect_pattern(demand_small_impulse, "demand_zone"))
    assert "demand_zone" not in _ids(detect_pattern(demand_no_volume, "demand_zone"))

    supply_boundary = [
        _candle(0, 100, 105, 95, 103, volume=100),
        _candle(1, 118, 118, 103, 103, volume=101),
    ]
    supply_large_base = [
        _candle(0, 100, 105, 95, "103.01", volume=100),
        supply_boundary[1],
    ]
    supply_small_impulse = [
        supply_boundary[0],
        _candle(1, 118, 118, 103, "103.01", volume=101),
    ]
    supply_no_volume = [
        supply_boundary[0],
        _candle(1, 118, 118, 103, 103, volume=100),
    ]
    assert "supply_zone" in _ids(detect_pattern(supply_boundary, "supply_zone"))
    assert "supply_zone" not in _ids(detect_pattern(supply_large_base, "supply_zone"))
    assert "supply_zone" not in _ids(detect_pattern(supply_small_impulse, "supply_zone"))
    assert "supply_zone" not in _ids(detect_pattern(supply_no_volume, "supply_zone"))


def test_opening_range_requires_complete_session_scoped_interval() -> None:
    zone = ZoneInfo("Asia/Kolkata")

    def session_candle(day: int, minute: int, high: int, low: int) -> Candle:
        timestamp = datetime(2026, 1, day, 9, 15, tzinfo=zone) + timedelta(minutes=minute)
        return Candle.from_values(timestamp.isoformat(), low + 1, high, low, high - 1, 100)

    session = SessionContext(
        "09:15",
        "15:30",
        "Asia/Kolkata",
        opening_range_minutes=5,
        candle_interval_minutes=1,
    )
    candles = [session_candle(5, minute, 110 + minute, 90 - minute) for minute in range(5)]
    candles.append(session_candle(5, 5, 120, 113))
    candles += [session_candle(6, minute, 200, 80) for minute in range(5)]
    candles.append(
        Candle.from_values(
            (datetime(2026, 1, 6, 9, 20, tzinfo=zone)).isoformat(),
            78,
            82,
            70,
            71,
            100,
        )
    )
    results = detect_price_action(candles, session_context=session)
    opening = [result for result in results if result.pattern_id == "opening_range"]
    assert len(opening) == 2
    assert opening[0].details["session_date"] == "2026-01-05"
    assert opening[0].details["candle_count"] == 5
    assert opening[0].metadata["session_context"] == {
        "session_open": "09:15",
        "session_close": "15:30",
        "timezone": "Asia/Kolkata",
        "market_name": None,
        "opening_range_minutes": 5,
        "candle_interval_minutes": 1,
    }
    opening_breakout = next(
        result for result in results if result.pattern_id == "opening_range_breakout"
    )
    assert opening_breakout.confirmation_timestamp == candles[5].timestamp
    opening_breakdown = next(
        result for result in results if result.pattern_id == "opening_range_breakdown"
    )
    assert opening_breakdown.confirmation_timestamp == candles[-1].timestamp
    assert "opening_range_breakout" not in _ids(
        detect_price_action(candles[:5], session_context=session)
    )
    missing = detect_price_action(candles[:2] + candles[3:5], session_context=session)
    assert "opening_range" not in _ids(missing)

    opening_bars = [session_candle(5, minute, 110 + minute, 90 - minute) for minute in range(5)]
    range_high = max(candle.high for candle in opening_bars)
    range_low = min(candle.low for candle in opening_bars)
    equality_break = opening_bars + [
        Candle.from_values(
            datetime(2026, 1, 5, 9, 20, tzinfo=zone).isoformat(),
            range_high,
            range_high + Decimal("0.0001"),
            range_high,
            range_high + Decimal("0.0001"),
            100,
        )
    ]
    beyond_break = opening_bars + [
        Candle.from_values(
            datetime(2026, 1, 5, 9, 20, tzinfo=zone).isoformat(),
            range_high,
            range_high + Decimal("0.0002"),
            range_high,
            range_high + Decimal("0.0002"),
            100,
        )
    ]
    assert "opening_range_breakout" not in _ids(
        detect_pattern(
            equality_break,
            "opening_range_breakout",
            session_context=session,
        )
    )
    confirmed_break = detect_pattern(
        beyond_break,
        "opening_range_breakout",
        session_context=session,
    )
    assert len(confirmed_break) == 1
    assert confirmed_break[0].confirmation_timestamp == beyond_break[-1].timestamp

    equality_breakdown = opening_bars + [
        Candle.from_values(
            datetime(2026, 1, 5, 9, 20, tzinfo=zone).isoformat(),
            range_low,
            range_low,
            range_low - Decimal("0.0001"),
            range_low - Decimal("0.0001"),
            100,
        )
    ]
    beyond_breakdown = opening_bars + [
        Candle.from_values(
            datetime(2026, 1, 5, 9, 20, tzinfo=zone).isoformat(),
            range_low,
            range_low,
            range_low - Decimal("0.0002"),
            range_low - Decimal("0.0002"),
            100,
        )
    ]
    assert "opening_range_breakdown" not in _ids(
        detect_pattern(
            equality_breakdown,
            "opening_range_breakdown",
            session_context=session,
        )
    )
    confirmed_breakdown = detect_pattern(
        beyond_breakdown,
        "opening_range_breakdown",
        session_context=session,
    )
    assert len(confirmed_breakdown) == 1
    assert confirmed_breakdown[0].confirmation_timestamp == beyond_breakdown[-1].timestamp


@pytest.mark.parametrize(
    "args",
    [
        ("15:30", "09:15", "Asia/Kolkata"),
        ("09:15", "15:30", "No/Such_Zone"),
        ("09:15", "15:30", "Asia/Kolkata", None, 6, 4),
    ],
)
def test_session_context_rejects_invalid_session_configuration(args: tuple[object, ...]) -> None:
    with pytest.raises(CandlestickPatternError):
        SessionContext(*args)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("opening_range_minutes", "candle_interval_minutes"),
    [("5", 1), (True, 1), (5, "1"), (5, False)],
)
def test_session_context_rejects_non_integer_intervals(
    opening_range_minutes: object, candle_interval_minutes: object
) -> None:
    with pytest.raises(CandlestickPatternError):
        SessionContext(
            "09:15",
            "15:30",
            opening_range_minutes=opening_range_minutes,  # type: ignore[arg-type]
            candle_interval_minutes=candle_interval_minutes,  # type: ignore[arg-type]
        )


def test_opening_range_fails_closed_for_dst_gap_and_ambiguous_open() -> None:
    spring_candle = Candle.from_values("2026-03-08T07:00:00Z", 10, 11, 9, 10, 1)
    spring_session = SessionContext("02:15", "05:00", "America/New_York")
    with pytest.raises(CandlestickPatternError, match="nonexistent"):
        detect_price_action([spring_candle], session_context=spring_session)

    autumn_candle = Candle.from_values("2026-11-01T05:30:00Z", 10, 11, 9, 10, 1)
    autumn_session = SessionContext("01:15", "04:00", "America/New_York")
    with pytest.raises(CandlestickPatternError, match="Ambiguous"):
        detect_price_action([autumn_candle], session_context=autumn_session)


def test_decimal_context_independence_includes_full_results_and_serialization() -> None:
    candles = [
        _candle(0, "100.00", "102.00", "91.00", "101.00"),
        _candle(1, "100.00", "100.10", "90.00", "100.00"),
        _candle(2, "98.00", "104.00", "97.00", "103.00"),
    ]
    outputs = []
    for precision, rounding in ((3, "ROUND_DOWN"), (7, "ROUND_UP"), (50, "ROUND_HALF_EVEN")):
        with localcontext() as context:
            context.prec = precision
            context.rounding = rounding
            outputs.append(_result_signature(detect_all_patterns(candles)))
    assert outputs[0] == outputs[1] == outputs[2]
    with localcontext() as context:
        context.prec = 3
        context.traps[Inexact] = True
        expected_trap = context.traps[Inexact]
        assert _result_signature(detect_all_patterns(candles)) == outputs[0]
        assert context.prec == 3
        assert context.traps[Inexact] is expected_trap


def test_pattern_results_are_deeply_immutable_and_json_safe() -> None:
    result = PatternResult(
        "Example",
        "example",
        "single_candle",
        None,
        details={"nested": {"value": Decimal("1.20")}, "items": [Decimal("2.30")]},
    )
    with pytest.raises(TypeError):
        result.details["new"] = "value"  # type: ignore[index]
    with pytest.raises(TypeError):
        result.details["nested"]["value"] = "changed"  # type: ignore[index]
    assert json.loads(json.dumps(result.to_dict()))["details"] == {
        "nested": {"value": "1.2"},
        "items": ["2.3"],
    }


def test_detector_results_record_definition_version_and_configuration() -> None:
    config = DetectionConfig(doji_body_ratio=Decimal("0.12"), pattern_lookback=12)
    result = next(
        item
        for item in detect_single_candle_patterns(
            [_candle(0, 100, 105, 95, 101)],
            config=config,
        )
        if item.pattern_id == "doji"
    )
    serialized_metadata = json.loads(json.dumps(result.to_dict()))["metadata"]
    assert serialized_metadata["definition_version"] == "1.0.0"
    assert serialized_metadata["configuration"]["doji_body_ratio"] == "0.12"
    assert serialized_metadata["configuration"]["pattern_lookback"] == 12
    with pytest.raises(TypeError):
        result.metadata["configuration"]["doji_body_ratio"] = "changed"  # type: ignore[index]


def test_every_public_detector_api_attaches_reproducibility_metadata() -> None:
    config = DetectionConfig(pattern_lookback=12)
    two_candle = [
        _candle(0, 100, 103, 98, 99),
        _candle(1, 98, 104, 97, 103),
    ]
    three_candle = [
        _candle(0, 105, 106, 98, 99),
        _candle(1, 98, 100, 97, 99),
        _candle(2, 98, 106, 97, 104),
    ]
    mother_inside = [
        _candle(0, 100, 110, 90, 101),
        _candle(1, 100, 105, 95, 102),
    ]
    outputs = [
        detect_single_candle_patterns([_candle(0, 100, 102, 91, 101)], config=config),
        detect_two_candle_patterns(two_candle, config=config),
        detect_multi_candle_patterns(three_candle, config=config),
        detect_price_action(mother_inside, config=config),
        detect_pattern(two_candle, "bullish_engulfing", config=config),
        detect_patterns(two_candle, include={"two_candle"}, config=config),
        detect_all_patterns(three_candle, config=config),
    ]
    assert all(output for output in outputs)
    for output in outputs:
        assert all(result.metadata["definition_version"] == "1.0.0" for result in output)
        assert all(result.metadata["configuration"]["pattern_lookback"] == 12 for result in output)


def test_double_top_respects_pattern_lookback_boundary() -> None:
    levels = [
        (10, 11, 8, 10),
        (10, 12, 9, 11),
        (10, 11, 9, 10),
        (9, 10, 8, 9),
        (10, 11, 9, 10),
        (10, 11, 9, 10),
        (10, 11, 9, 10),
        (10, 12, 9, 11),
        (10, 11, 9, 10),
        (8, 9, 7, 7),
    ]
    candles = [_candle(index, *level) for index, level in enumerate(levels)]
    base = {
        "confirmation_lookback": 1,
        "support_zone_tolerance": Decimal("0.1"),
        "breakout_buffer": Decimal("0.1"),
    }
    bounded = DetectionConfig(pattern_lookback=5, **base)
    extended = DetectionConfig(pattern_lookback=6, **base)
    assert "double_top" not in _ids(detect_multi_candle_patterns(candles, config=bounded))
    assert "double_top" in _ids(detect_multi_candle_patterns(candles, config=extended))


def test_consolidation_rejects_directional_drift_within_a_narrow_percent_band() -> None:
    config = DetectionConfig(range_lookback=20, range_max_width_ratio=Decimal("0.03"))
    stricter_drift = DetectionConfig(
        range_lookback=20,
        range_max_width_ratio=Decimal("0.03"),
        range_max_net_change_ratio=Decimal("0.49"),
    )
    consolidation = [
        _candle(index, 1005, 1020, 1000, 1005 if index % 2 == 0 else 1015) for index in range(20)
    ]
    monotonic_trend = [
        _candle(index, 1000 + index, 1002 + index, 999 + index, 1001 + index) for index in range(20)
    ]
    assert "range" in _ids(detect_price_action(consolidation, config=config))
    assert "range" not in _ids(detect_price_action(consolidation, config=stricter_drift))
    assert "range" not in _ids(detect_price_action(monotonic_trend, config=config))


def test_range_detector_width_drift_and_history_boundaries() -> None:
    width_boundary = [
        _candle(0, 101, 103, 100, 101),
        _candle(1, 101, 103, 100, 101),
    ]
    width_outside = [
        _candle(0, 101, "103.01", 100, 101),
        _candle(1, 101, "103.01", 100, 101),
    ]
    width_config = DetectionConfig(range_lookback=2, range_max_width_ratio=Decimal("0.03"))
    assert "range" in _ids(detect_pattern(width_boundary, "range", config=width_config))
    assert "range" not in _ids(detect_pattern(width_outside, "range", config=width_config))
    assert detect_pattern(width_boundary[:1], "range", config=width_config) == []

    drift_boundary = [
        _candle(0, 100, 102, 100, 100),
        _candle(1, 101, 102, 100, 101),
    ]
    drift_outside = [
        _candle(0, 100, "102.02", 100, 100),
        _candle(1, "101.02", "102.02", 100, "101.02"),
    ]
    assert "range" in _ids(detect_pattern(drift_boundary, "range", config=width_config))
    assert "range" not in _ids(detect_pattern(drift_outside, "range", config=width_config))


def test_public_api_filtering_ordering_and_consistency() -> None:
    candles = [
        _candle(0, 100, 110, 90, 101),
        _candle(1, 100, 100.1, 90, 100),
        _candle(2, 98, 120, 97, 118),
    ]
    all_results = detect_all_patterns(candles)
    assert detect_patterns(candles) == all_results
    assert detect_pattern(candles, "bullish_engulfing") == [
        result for result in all_results if result.pattern_id == "bullish_engulfing"
    ]
    filtered = detect_patterns(candles, include={"two_candle"})
    assert filtered
    assert all(result.category == "two_candle" for result in filtered)
    with pytest.raises(CandlestickPatternError, match="Unknown detector"):
        detect_patterns(candles, include={"unknown"})
    with pytest.raises(CandlestickPatternError):
        detect_patterns(candles, include="two_candle")
    with pytest.raises(CandlestickPatternError, match="config"):
        detect_patterns(candles, config=object())  # type: ignore[arg-type]
    with pytest.raises(CandlestickPatternError, match="session_context"):
        detect_price_action(candles, session_context=object())  # type: ignore[arg-type]


def test_prefix_and_truncation_invariance_for_completed_results() -> None:
    candles = [
        _candle(0, 100, 104, 98, 99),
        _candle(1, 98, 100, 96, 97),
        _candle(2, 97, 102, 95, 101),
        _candle(3, 101, 106, 100, 105),
        _candle(4, 105, 107, 99, 100),
        _candle(5, 100, 103, 98, 102),
        _candle(6, 102, 104, 97, 98),
        _candle(7, 98, 100, 94, 95),
    ]
    for prefix_length in range(1, len(candles)):
        prefix = detect_all_patterns(candles[:prefix_length])
        complete = detect_all_patterns(candles)
        cutoff = candles[prefix_length - 1].timestamp
        known_later = [
            item
            for item in complete
            if item.confirmation_timestamp is not None and item.confirmation_timestamp <= cutoff
        ]
        assert _result_signature(prefix) == _result_signature(known_later)


def test_flat_valid_history_is_a_negative_case_for_all_detector_categories() -> None:
    candles = [_candle(index, 100, 100, 100, 100) for index in range(12)]
    assert detect_all_patterns(candles) == []
    assert detect_all_patterns(candles, config=DetectionConfig(range_lookback=2)) == []


def test_large_full_detection_sequence_has_bounded_runtime() -> None:
    candles = [
        _candle(index, 100 + index, 102 + index, 99 + index, 101 + index) for index in range(5000)
    ]
    started = perf_counter()
    results = detect_all_patterns(candles)
    elapsed = perf_counter() - started
    assert results == []
    assert elapsed < 5


def test_single_candle_hammer_and_doji() -> None:
    candles = [_candle(0, 100, 102, 91, 101), _candle(1, 100, 100.5, 99.5, 100)]
    ids = _ids(detect_single_candle_patterns(candles))
    assert {"hammer", "doji"} <= ids


def test_two_candle_engulfing_and_inside_bar() -> None:
    candles = [
        _candle(0, 100, 103, 98, 99),
        _candle(1, 98, 104, 97, 103),
        _candle(2, 102, 103, 99, 101),
    ]
    ids = _ids(detect_two_candle_patterns(candles))
    assert {"bullish_engulfing", "inside_bar", "outside_bar"} <= ids


def test_multi_candle_morning_star() -> None:
    candles = [
        _candle(0, 105, 106, 98, 99),
        _candle(1, 98, 100, 97, 99),
        _candle(2, 98, 106, 97, 104),
    ]
    assert "morning_star" in _ids(detect_multi_candle_patterns(candles))


def test_price_action_swing_and_breakout_are_temporally_causal() -> None:
    levels = [
        (10, 12, 9, 10),
        (9, 10, 8, 9),
        (10, 12, 9, 11),
        (9, 11, 8.2, 9),
        (10, 12, 9, 11),
        (8.5, 10, 8, 8.2),
        (7.5, 9, 7, 7.5),
    ]
    candles = [_candle(index, *level) for index, level in enumerate(levels)]
    results = detect_price_action(
        candles,
        config=DetectionConfig(
            confirmation_lookback=1,
            support_zone_tolerance=Decimal("0.3"),
            breakout_buffer=Decimal("0.1"),
        ),
    )
    swing = next(result for result in results if result.pattern_id == "swing_low")
    breakdown = next(
        result for result in results if result.pattern_id == "bearish_support_breakdown"
    )
    assert swing.confirmation_timestamp == candles[2].timestamp
    assert breakdown.confirmation_timestamp == candles[6].timestamp
    assert "bearish_support_breakdown" not in _ids(detect_price_action(candles[:6]))


def test_detect_all_patterns_is_deterministic() -> None:
    candles = [
        _candle(0, 100, 103, 99, 101),
        _candle(1, 101, 102, 98, 99),
        _candle(2, 95, 120, 94, 118),
    ]
    first = detect_all_patterns(candles)
    assert first == detect_all_patterns(candles)
    assert detect_pattern(candles, "bullish_engulfing")


def test_invalid_sequence_and_missing_fields_fail_closed() -> None:
    with pytest.raises(CandlestickPatternError, match="chronological"):
        prepare_candles([_candle(1, 9, 10, 8, 9), _candle(0, 9, 10, 8, 9)])
    with pytest.raises(CandlestickPatternError, match="missing required"):
        prepare_candles([{"timestamp": "2026-01-05T09:15:00Z", "open": 9, "high": 10, "low": 8}])


def test_detect_patterns_supports_include_filter() -> None:
    candles = [_candle(0, 100, 103, 98, 99), _candle(1, 98, 104, 97, 103)]
    results = detect_patterns(candles, include={"two_candle"})
    assert results
    assert all(result.category == "two_candle" for result in results)


def test_decimal_context_does_not_change_results() -> None:
    candles = [_candle(0, 100, 110, 90, 101), _candle(1, 100, 100.5, 99.5, 100)]
    with localcontext() as context:
        context.prec = 3
        context.rounding = "ROUND_DOWN"
        low_precision = _result_signature(detect_single_candle_patterns(candles))
    with localcontext() as context:
        context.prec = 50
        context.rounding = "ROUND_HALF_EVEN"
        high_precision = _result_signature(detect_single_candle_patterns(candles))
    assert low_precision == high_precision


def test_no_future_candle_leakage() -> None:
    candles = [_candle(0, 100, 103, 98, 99), _candle(1, 98, 104, 97, 103)]
    assert "bullish_engulfing" not in _ids(detect_all_patterns(candles[:1]))
    assert "bullish_engulfing" in _ids(detect_all_patterns(candles))


def test_missing_multi_candle_patterns_are_detected() -> None:
    cases = {
        "three_inside_up": [
            _candle(0, 110, 112, 97, 100),
            _candle(1, 102, 108, 101, 106),
            _candle(2, 106, 112, 105, 111),
        ],
        "three_outside_up": [
            _candle(0, 110, 112, 98, 100),
            _candle(1, 97, 114, 96, 113),
            _candle(2, 113, 116, 112, 115),
        ],
    }
    for pattern_id, candles in cases.items():
        assert pattern_id in _ids(detect_multi_candle_patterns(candles))


def test_price_action_supports_session_context() -> None:
    zone = ZoneInfo("Asia/Kolkata")
    candles = []
    for index in range(5):
        timestamp = datetime(2026, 1, 5, 9, 15, tzinfo=zone) + timedelta(minutes=index)
        candles.append(
            Candle.from_values(timestamp.isoformat(), 100, 110 + index, 90 - index, 105, 100)
        )
    result = detect_price_action(
        candles, session_context=SessionContext("09:15", "15:30", "Asia/Kolkata")
    )
    assert "opening_range" in _ids(result)
