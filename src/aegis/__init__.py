__version__ = "0.1.0"

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

__all__ = [
    "Candle",
    "CandlestickPatternError",
    "DetectionConfig",
    "PatternResult",
    "SessionContext",
    "__version__",
    "detect_all_patterns",
    "detect_multi_candle_patterns",
    "detect_pattern",
    "detect_patterns",
    "detect_price_action",
    "detect_single_candle_patterns",
    "detect_two_candle_patterns",
    "prepare_candles",
]
