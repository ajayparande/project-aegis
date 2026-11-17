"""Controlled, permissioned read-only tool registry and research tools for Project Aegis."""

from __future__ import annotations

import json
import math
import multiprocessing
import pickle
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from aegis.ai_agent.models import (
    ToolDefinition,
    ToolExecutionError,
    ToolParameter,
    ToolPermission,
)
from aegis.ai_agent.security import (
    enforce_tool_permission,
    redact_nested_secrets,
    redact_secrets,
    tag_untrusted_data,
)
from aegis.candlesticks import (
    SessionContext,
    detect_patterns,
    detect_price_action,
    prepare_candles,
)
from aegis.indicators import (
    average_true_range,
    bollinger_bands,
    exponential_moving_average,
    macd,
    on_balance_volume,
    relative_strength_index,
    simple_moving_average,
    stochastic_oscillator,
    volume_weighted_average_price,
)
from aegis.instruments import get_instrument, search_instruments
from aegis.market_data import list_historical_candles
from aegis.market_quality import (
    CalendarConfigError,
    CalendarRegistry,
    analyze_dataset,
    load_approved_calendar_registry,
)


def _tool_worker(
    connection: Any,
    handler: Callable[..., Any],
    arguments: dict[str, Any],
    context: dict[str, Any],
    max_output_chars: int,
) -> None:
    try:
        result = handler(**arguments, context=context)
        if isinstance(result, (dict, list)):
            result = json.dumps(redact_nested_secrets(result), default=str)
        else:
            result = redact_secrets(str(result))
        truncation_marker = "\n... [OUTPUT TRUNCATED]"
        if len(result) > max_output_chars:
            if max_output_chars <= len(truncation_marker):
                result = truncation_marker[:max_output_chars]
            else:
                result = result[: max_output_chars - len(truncation_marker)] + truncation_marker
        connection.send(("OK", result))
    except Exception:  # noqa: BLE001
        connection.send(("ERROR", "Tool handler failed; internal details are withheld."))
    finally:
        connection.close()


class ToolRegistry:
    """Registry managing allowlisted tools with typed validation and strict permission gates."""

    def __init__(
        self,
        max_output_chars: int = 25000,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._tools: dict[str, tuple[ToolDefinition, Callable[..., Any]]] = {}
        if isinstance(max_output_chars, bool) or not isinstance(max_output_chars, int):
            raise TypeError("max_output_chars must be a positive integer.")
        if not 0 < max_output_chars <= 1_000_000:
            raise ValueError("max_output_chars must be between 1 and 1000000.")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 120
        ):
            raise ValueError("timeout_seconds must be between 0 and 120 seconds.")
        self._max_output_chars = max_output_chars
        self._timeout_seconds = timeout_seconds

    def register(self, tool_def: ToolDefinition, handler: Callable[..., Any]) -> None:
        """Register a tool, enforcing permission gates at registration time."""
        enforce_tool_permission(tool_def.name, tool_def.permission)
        try:
            pickle.dumps(handler)
        except (pickle.PicklingError, AttributeError, TypeError) as exc:
            raise TypeError(
                "Tool handlers must be importable for isolated, time-limited execution."
            ) from exc
        self._tools[tool_def.name] = (tool_def, handler)

    def get_tool(self, name: str) -> ToolDefinition:
        """Retrieve tool definition by name."""
        if name not in self._tools:
            raise ToolExecutionError(f"Tool '{name}' is not registered or not permitted.")
        return self._tools[name][0]

    def get_definitions(self) -> list[ToolDefinition]:
        """Return all registered tool definitions."""
        return [tool_def for tool_def, _ in self._tools.values()]

    def _execute_with_timeout(
        self,
        handler: Callable[..., Any],
        arguments: dict[str, Any],
        context: dict[str, Any],
    ) -> str:
        process_context = multiprocessing.get_context("spawn")
        receiver, sender = process_context.Pipe(duplex=False)
        process = process_context.Process(
            target=_tool_worker,
            args=(sender, handler, arguments, context, self._max_output_chars),
            daemon=True,
        )
        started = False
        try:
            process.start()
            started = True
            sender.close()
            process.join(self._timeout_seconds)
            if process.is_alive():
                process.terminate()
                process.join(1.0)
                if process.is_alive():
                    process.kill()
                    process.join()
                raise TimeoutError("Tool execution exceeded its time limit.")
            if not receiver.poll():
                raise RuntimeError("Tool worker exited without a result.")
            status, result = receiver.recv()
            if status != "OK" or not isinstance(result, str):
                raise RuntimeError("Tool worker failed; internal details are withheld.")
            return result
        finally:
            receiver.close()
            sender.close()
            if started and process.is_alive():
                process.terminate()
                process.join(1.0)

    def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: dict[str, Any],
    ) -> str:
        """Validate parameters and execute a registered tool in a safe context."""
        if not isinstance(tool_name, str) or not tool_name:
            return tag_untrusted_data(
                json.dumps(
                    {
                        "error": "Tool name must be a non-empty string.",
                        "status": "REJECTED",
                    }
                ),
                label="invalid_tool",
            )
        if tool_name not in self._tools:
            return tag_untrusted_data(
                json.dumps(
                    {
                        "error": f"Tool '{tool_name}' is not registered or not permitted.",
                        "status": "REJECTED",
                    }
                ),
                label=tool_name,
            )

        tool_def, handler = self._tools[tool_name]

        try:
            # Deterministically enforce permission before execution
            enforce_tool_permission(tool_def.name, tool_def.permission)

            # Validate arguments against parameter schema
            validated_args = self._validate_arguments(tool_def, arguments)

            # Execute tool with validated arguments and server context
            raw_result = self._execute_with_timeout(handler, validated_args, context)

            # Format to JSON string
            if isinstance(raw_result, (dict, list)):
                cleaned = redact_nested_secrets(raw_result)
                output_str = json.dumps(cleaned, default=str)
            else:
                output_str = redact_secrets(str(raw_result))

            # Truncate if output exceeds safety limit
            if len(output_str) > self._max_output_chars:
                output_str = (
                    output_str[: self._max_output_chars]
                    + f"\n... [OUTPUT TRUNCATED: Exceeded {self._max_output_chars} characters]"
                )

            return tag_untrusted_data(output_str, label=tool_name)

        except TimeoutError:
            return tag_untrusted_data(
                json.dumps(
                    {
                        "error": "Tool execution timed out.",
                        "status": "TIMEOUT",
                    }
                ),
                label=f"{tool_name}_timeout",
            )
        except Exception:  # noqa: BLE001
            return tag_untrusted_data(
                json.dumps(
                    {
                        "error": "Tool execution failed; internal details are withheld.",
                        "status": "ERROR",
                    }
                ),
                label=f"{tool_name}_error",
            )

    def _validate_arguments(
        self,
        tool_def: ToolDefinition,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate the complete JSON argument object against the declared schema."""
        if not isinstance(arguments, dict):
            raise TypeError("Tool arguments must be an object.")

        missing = object()

        def validate_object(
            parameters: tuple[ToolParameter, ...],
            values: dict[str, Any],
            path: str,
            max_properties: int | None = None,
        ) -> dict[str, Any]:
            declared = {param.name for param in parameters}
            unexpected = set(values) - declared
            if unexpected:
                raise ValueError(f"Unexpected parameter(s) at {path}: {sorted(unexpected)}.")
            if max_properties is not None and len(values) > max_properties:
                raise ValueError(f"Too many properties at {path}.")

            validated_values: dict[str, Any] = {}
            for param in parameters:
                value = values.get(param.name, missing)
                parameter_path = f"{path}.{param.name}"
                if value is missing:
                    if param.required:
                        raise ValueError(f"Missing required parameter '{parameter_path}'.")
                    value = param.default
                elif value is None:
                    if param.required:
                        raise ValueError(f"Parameter '{parameter_path}' cannot be null.")
                    validated_values[param.name] = None
                    continue

                validated_values[param.name] = (
                    validate_value(param, value, parameter_path) if value is not None else None
                )
            return validated_values

        def validate_value(param: ToolParameter, value: Any, path: str) -> Any:
            type_name = param.type_name
            if type_name == "string":
                valid = isinstance(value, str)
            elif type_name == "integer":
                valid = isinstance(value, int) and not isinstance(value, bool)
            elif type_name == "number":
                valid = (
                    isinstance(value, (int, float, Decimal))
                    and not isinstance(value, bool)
                    and Decimal(str(value)).is_finite()
                )
            elif type_name == "boolean":
                valid = isinstance(value, bool)
            elif type_name == "array":
                valid = isinstance(value, list)
            elif type_name == "object":
                valid = isinstance(value, dict)
            else:
                raise ValueError(f"Unsupported schema type '{type_name}' for '{path}'.")
            if not valid:
                raise TypeError(f"Parameter '{path}' must be {type_name}.")

            if param.choices and value not in param.choices:
                raise ValueError(f"Parameter '{path}' is not an allowed choice.")
            if type_name in {"integer", "number"}:
                if param.minimum is not None and value < param.minimum:
                    raise ValueError(f"Parameter '{path}' is below its minimum.")
                if param.maximum is not None and value > param.maximum:
                    raise ValueError(f"Parameter '{path}' exceeds its maximum.")
            elif type_name == "string":
                if len(value) > (param.max_length if param.max_length is not None else 256):
                    raise ValueError(f"Parameter '{path}' exceeds its maximum length.")
                if param.min_length is not None and len(value) < param.min_length:
                    raise ValueError(f"Parameter '{path}' is below its minimum length.")
            elif type_name == "array":
                max_items = param.max_items if param.max_items is not None else 100
                if len(value) > max_items:
                    raise ValueError(f"Parameter '{path}' exceeds its maximum size.")
                if param.min_items is not None and len(value) < param.min_items:
                    raise ValueError(f"Parameter '{path}' is below its minimum size.")
                item_schema = ToolParameter(
                    name="item",
                    type_name=param.items_type or "string",
                    description=f"Item of {path}.",
                    choices=param.item_choices,
                    properties=param.properties,
                    max_properties=param.max_properties,
                )
                for index, item in enumerate(value):
                    validate_value(item_schema, item, f"{path}[{index}]")
            elif type_name == "object":
                nested_parameters = param.properties or ()
                nested_limit = (
                    param.max_properties
                    if param.max_properties is not None
                    else min(len(nested_parameters), 32)
                )
                value = validate_object(
                    nested_parameters,
                    value,
                    path,
                    max_properties=nested_limit,
                )
            return value

        return validate_object(tool_def.parameters, arguments, tool_def.name)


def _to_standard_candles(candle_dicts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map SQLite historical_candles column names to standard CandleInput keys in chronological order."""
    standard = [
        {
            "timestamp": str(c.get("candle_timestamp") or c.get("timestamp")),
            "open": c.get("open_decimal") or c.get("open"),
            "high": c.get("high_decimal") or c.get("high"),
            "low": c.get("low_decimal") or c.get("low"),
            "close": c.get("close_decimal") or c.get("close"),
            "volume": c.get("volume", 0),
        }
        for c in candle_dicts
    ]
    return sorted(standard, key=lambda c: str(c["timestamp"]))


# ---------------------------------------------------------------------------
# The 7 Built-In Read-Only Research Tool Implementations
# ---------------------------------------------------------------------------


def _tool_get_instrument_info(
    symbol: str,
    exchange: str = "NSE",
    *,
    context: dict[str, Any],
) -> dict[str, Any]:
    """Retrieve instrument master details for a symbol."""
    database_path: Path = context["database_path"]
    inst = get_instrument(database_path, exchange, symbol)

    if inst is not None:
        return {
            "found": True,
            "id": inst.id,
            "exchange": inst.exchange,
            "trading_symbol": inst.trading_symbol,
            "instrument_name": inst.instrument_name,
            "instrument_type": inst.instrument_type,
            "lot_size": inst.lot_size,
            "tick_size": str(inst.tick_size) if inst.tick_size is not None else None,
            "segment": inst.segment,
            "isin": inst.isin,
            "is_active": inst.is_active,
        }

    # If exact match not found, attempt search
    matches = search_instruments(database_path, symbol, exchange=exchange)
    return {
        "found": False,
        "message": f"Exact match for {exchange}:{symbol} not found.",
        "search_matches": [
            {
                "id": m.id,
                "exchange": m.exchange,
                "trading_symbol": m.trading_symbol,
                "instrument_name": m.instrument_name,
                "instrument_type": m.instrument_type,
                "segment": m.segment,
            }
            for m in matches[:5]
        ],
    }


def _tool_get_dataset_quality_summary(
    symbol: str,
    start_date: str,
    end_date: str,
    exchange: str = "NSE",
    interval: str = "1d",
    *,
    context: dict[str, Any],
) -> dict[str, Any]:
    """Summarize quality using approved, fully confirmed calendar coverage.

    Missing or invalid approved configuration and unavailable confirmed coverage
    raise ValueError; the registry returns a controlled unsuccessful tool result.
    """
    database_path: Path = context["database_path"]
    try:
        calendars = load_approved_calendar_registry(expected_registry=context.get("calendars"))
    except (CalendarConfigError, OSError) as exc:
        raise ValueError("Approved calendar evidence is unavailable or invalid.") from exc

    try:
        start_d = date.fromisoformat(start_date)
        end_d = date.fromisoformat(end_date)
    except ValueError as exc:
        raise ValueError(f"Dates must be in ISO format 'YYYY-MM-DD': {exc}") from exc

    report = analyze_dataset(
        database_path=database_path,
        exchange=exchange,
        trading_symbol=symbol,
        interval=interval,
        start_date=start_d,
        end_date=end_d,
        as_of=datetime.now(UTC),
        calendars=calendars,
    )

    if report.calendar_status != "FULL" or report.calendar_confidence != "CONFIRMED":
        raise ValueError("Quality summaries require full CONFIRMED approved calendar coverage.")
    passes_gate = report.overall_status == "COMPLETE" and report.calendar_status == "FULL"

    return {
        "exchange": report.exchange,
        "trading_symbol": report.trading_symbol,
        "interval": report.interval,
        "requested_start": report.requested_start,
        "requested_end": report.requested_end,
        "overall_status": report.overall_status,
        "calendar_status": report.calendar_status,
        "calendar_confidence": report.calendar_confidence,
        "actual_candles": report.actual_candles,
        "expected_candles": report.expected_candles,
        "missing_candles": len(report.missing_timestamps),
        "duplicate_candles": report.duplicate_identity_count,
        "invalid_candles": report.invalid_candle_count,
        "completeness_percentage": str(report.completeness_percentage),
        "passes_backtest_gate": passes_gate,
        "reasons": list(report.reasons),
    }


def _tool_compute_technical_indicators(
    symbol: str,
    exchange: str = "NSE",
    interval: str = "1d",
    indicators: list[str] | None = None,
    limit: int = 100,
    *,
    context: dict[str, Any],
) -> dict[str, Any]:
    """Calculate deterministic technical indicators over stored historical candles."""
    database_path: Path = context["database_path"]
    requested_indicators = [ind.lower().strip() for ind in (indicators or ["sma", "rsi"])]

    candle_dicts = list_historical_candles(
        database_path=database_path,
        exchange=exchange,
        trading_symbol=symbol,
        interval=interval,
        limit=min(limit, 500),
    )

    if not candle_dicts:
        return {
            "symbol": symbol,
            "exchange": exchange,
            "interval": interval,
            "candles_count": 0,
            "message": "No historical candles found in database for the given instrument and interval.",
        }

    # Stored candles are newest first; prepare_candles sorts and validates chronologically
    candles = prepare_candles(_to_standard_candles(candle_dicts), normalize=True)

    results: dict[str, Any] = {
        "symbol": symbol,
        "exchange": exchange,
        "interval": interval,
        "candles_analyzed": len(candles),
        "first_candle_time": candles[0].timestamp,
        "last_candle_time": candles[-1].timestamp,
        "latest_close": str(candles[-1].close),
        "indicators": {},
    }

    for ind in requested_indicators:
        if ind == "sma":
            res = simple_moving_average(candles, period=min(20, len(candles)))
            sma_values: list[Any] = list(res.columns.get("sma", []))
            latest_val = next((v for v in reversed(sma_values) if v is not None), None)
            results["indicators"]["sma_20"] = {
                "latest": latest_val,
                "recent": sma_values[-5:] if len(sma_values) >= 5 else sma_values,
            }
        elif ind == "ema":
            res = exponential_moving_average(candles, period=min(20, len(candles)))
            ema_values: list[Any] = list(res.columns.get("ema", []))
            latest_val = next((v for v in reversed(ema_values) if v is not None), None)
            results["indicators"]["ema_20"] = {
                "latest": latest_val,
                "recent": ema_values[-5:] if len(ema_values) >= 5 else ema_values,
            }
        elif ind == "rsi":
            res = relative_strength_index(candles, period=min(14, len(candles)))
            rsi_values: list[Any] = list(res.columns.get("rsi", []))
            latest_val = next((v for v in reversed(rsi_values) if v is not None), None)
            results["indicators"]["rsi_14"] = {
                "latest": latest_val,
                "recent": rsi_values[-5:] if len(rsi_values) >= 5 else rsi_values,
            }
        elif ind == "macd":
            res = macd(candles, fast_period=12, slow_period=26, signal_period=9)
            macd_vals: list[Any] = list(res.columns.get("macd", []))
            sig_vals: list[Any] = list(res.columns.get("signal", []))
            hist_vals: list[Any] = list(res.columns.get("histogram", []))
            results["indicators"]["macd"] = {
                "latest_macd": next((v for v in reversed(macd_vals) if v is not None), None),
                "latest_signal": next((v for v in reversed(sig_vals) if v is not None), None),
                "latest_hist": next((v for v in reversed(hist_vals) if v is not None), None),
            }
        elif ind == "atr":
            res = average_true_range(candles, period=min(14, len(candles)))
            atr_values: list[Any] = list(res.columns.get("atr", []))
            latest_val = next((v for v in reversed(atr_values) if v is not None), None)
            results["indicators"]["atr_14"] = {
                "latest": latest_val,
            }
        elif ind == "bollinger_bands":
            res = bollinger_bands(candles, period=min(20, len(candles)), deviations=Decimal("2.0"))
            results["indicators"]["bollinger_bands"] = {
                "latest_middle": next(
                    (v for v in reversed(res.columns.get("middle_band", [])) if v is not None), None
                ),
                "latest_upper": next(
                    (v for v in reversed(res.columns.get("upper_band", [])) if v is not None), None
                ),
                "latest_lower": next(
                    (v for v in reversed(res.columns.get("lower_band", [])) if v is not None), None
                ),
            }
        elif ind == "vwap":
            session_ctx = SessionContext(
                session_open="09:15",
                session_close="15:30",
                timezone="Asia/Kolkata",
            )
            res = volume_weighted_average_price(candles, session_context=session_ctx)
            vwap_values: list[Any] = list(res.columns.get("vwap", []))
            results["indicators"]["vwap"] = {
                "latest": next((v for v in reversed(vwap_values) if v is not None), None),
            }
        elif ind == "obv":
            res = on_balance_volume(candles)
            obv_values: list[Any] = list(res.columns.get("obv", []))
            results["indicators"]["obv"] = {
                "latest": next((v for v in reversed(obv_values) if v is not None), None),
            }
        elif ind == "stochastic":
            res = stochastic_oscillator(candles, lookback=14, k_smoothing=3, d_smoothing=3)
            results["indicators"]["stochastic"] = {
                "latest_k": next(
                    (v for v in reversed(res.columns.get("percent_k", [])) if v is not None), None
                ),
                "latest_d": next(
                    (v for v in reversed(res.columns.get("percent_d", [])) if v is not None), None
                ),
            }

    return results


def _tool_detect_price_action_patterns(
    symbol: str,
    exchange: str = "NSE",
    interval: str = "1d",
    limit: int = 50,
    *,
    context: dict[str, Any],
) -> dict[str, Any]:
    """Detect deterministic candlestick and price action patterns over historical candles."""
    database_path: Path = context["database_path"]

    candle_dicts = list_historical_candles(
        database_path=database_path,
        exchange=exchange,
        trading_symbol=symbol,
        interval=interval,
        limit=min(limit, 200),
    )

    if not candle_dicts:
        return {
            "symbol": symbol,
            "exchange": exchange,
            "interval": interval,
            "patterns_detected": [],
            "message": "No historical candles found in database.",
        }

    candles = prepare_candles(_to_standard_candles(candle_dicts), normalize=True)
    patterns = detect_patterns(candles)
    price_action = detect_price_action(candles)

    # Format detected patterns
    pattern_summaries = [
        {
            "name": p.name,
            "category": p.category,
            "direction": p.direction,
            "timestamp": p.start_timestamp,
            "is_confirmed": p.is_confirmed,
        }
        for p in patterns[-10:]  # Latest 10 patterns
    ]

    pa_summaries = [
        {
            "name": pa.name,
            "category": pa.category,
            "direction": pa.direction,
            "timestamp": pa.start_timestamp,
        }
        for pa in price_action[-5:]  # Latest 5 price action signals
    ]

    return {
        "symbol": symbol,
        "exchange": exchange,
        "interval": interval,
        "candles_analyzed": len(candles),
        "total_patterns_detected": len(patterns),
        "recent_patterns": pattern_summaries,
        "price_action_signals": pa_summaries,
    }


def _tool_get_backtest_summary(
    backtest_id: int | None = None,
    *,
    context: dict[str, Any],
) -> dict[str, Any]:
    """Retrieve backtest configurations, execution runs, and performance metrics."""
    from aegis.backtesting import list_backtest_runs, list_backtests

    database_path: Path = context["database_path"]

    if backtest_id is not None:
        runs = list_backtest_runs(database_path, backtest_id)
        return {
            "backtest_id": backtest_id,
            "total_runs": len(runs),
            "runs": runs[:5],
        }

    backtests = list_backtests(database_path)
    return {
        "total_registered_backtests": len(backtests),
        "backtests": backtests[:10],
    }


def _tool_get_strategy_experiment_results(
    experiment_id: int | None = None,
    *,
    context: dict[str, Any],
) -> dict[str, Any]:
    """Retrieve strategy lab experiment definitions and signal run records."""
    from contextlib import closing

    from aegis.database import get_connection
    from aegis.strategy_lab import list_strategy_experiments

    database_path: Path = context["database_path"]

    if experiment_id is not None:
        with closing(get_connection(database_path)) as connection:
            exp_row = connection.execute(
                "SELECT id, name, status, created_at, configuration_json FROM experiments WHERE id = ?",
                (experiment_id,),
            ).fetchone()
            if exp_row is None:
                return {"found": False, "message": f"Experiment {experiment_id} not found."}

            runs = connection.execute(
                "SELECT id, run_type, status, started_at, completed_at, result_json, error_message "
                "FROM runs WHERE experiment_id = ? ORDER BY id DESC LIMIT 5",
                (experiment_id,),
            ).fetchall()

        return {
            "found": True,
            "experiment_id": experiment_id,
            "name": exp_row["name"],
            "status": exp_row["status"],
            "created_at": exp_row["created_at"],
            "runs": [dict(r) for r in runs],
        }

    experiments = list_strategy_experiments(database_path)
    return {
        "total_strategy_experiments": len(experiments),
        "experiments": experiments[:10],
    }


# ---------------------------------------------------------------------------
# Default Tool Registry Factory
# ---------------------------------------------------------------------------


def create_default_tool_registry(
    database_path: Path,
    calendars: CalendarRegistry | None = None,
    max_output_chars: int = 25000,
    timeout_seconds: float = 30.0,
) -> ToolRegistry:
    """Create a registry containing only approved non-sensitive research tools."""
    registry = ToolRegistry(
        max_output_chars=max_output_chars,
        timeout_seconds=timeout_seconds,
    )

    # 1. get_instrument_info
    registry.register(
        ToolDefinition(
            name="get_instrument_info",
            description="Lookup registered Indian equity instruments by exchange and symbol, or search symbols.",
            parameters=(
                ToolParameter(
                    name="symbol",
                    type_name="string",
                    description="Trading symbol (e.g. 'RELIANCE', 'TCS', 'INFY').",
                    required=True,
                ),
                ToolParameter(
                    name="exchange",
                    type_name="string",
                    description="Exchange identifier ('NSE' or 'BSE').",
                    required=False,
                    default="NSE",
                    choices=("NSE", "BSE"),
                ),
            ),
            permission=ToolPermission.READ_ONLY,
        ),
        _tool_get_instrument_info,
    )

    # 2. get_dataset_quality_summary
    registry.register(
        ToolDefinition(
            name="get_dataset_quality_summary",
            description="Analyze data completeness, session calendar alignment, and quality gate pass/fail for a historical date range.",
            parameters=(
                ToolParameter(
                    name="symbol",
                    type_name="string",
                    description="Trading symbol of the instrument.",
                    required=True,
                ),
                ToolParameter(
                    name="start_date",
                    type_name="string",
                    description="Start date in ISO format 'YYYY-MM-DD'.",
                    required=True,
                ),
                ToolParameter(
                    name="end_date",
                    type_name="string",
                    description="End date in ISO format 'YYYY-MM-DD'.",
                    required=True,
                ),
                ToolParameter(
                    name="exchange",
                    type_name="string",
                    description="Exchange identifier ('NSE' or 'BSE').",
                    required=False,
                    default="NSE",
                    choices=("NSE", "BSE"),
                ),
                ToolParameter(
                    name="interval",
                    type_name="string",
                    description="Bar interval ('1m', '5m', '15m', '1h', '1d').",
                    required=False,
                    default="1d",
                    choices=("1m", "5m", "15m", "1h", "1d"),
                ),
            ),
            permission=ToolPermission.READ_ONLY,
        ),
        _tool_get_dataset_quality_summary,
    )

    # 3. compute_technical_indicators
    registry.register(
        ToolDefinition(
            name="compute_technical_indicators",
            description="Compute deterministic mathematical indicators (SMA, EMA, RSI, MACD, ATR, Bollinger, VWAP, OBV, Stochastic) from historical candles.",
            parameters=(
                ToolParameter(
                    name="symbol",
                    type_name="string",
                    description="Trading symbol of the instrument.",
                    required=True,
                ),
                ToolParameter(
                    name="exchange",
                    type_name="string",
                    description="Exchange identifier ('NSE' or 'BSE').",
                    required=False,
                    default="NSE",
                    choices=("NSE", "BSE"),
                ),
                ToolParameter(
                    name="interval",
                    type_name="string",
                    description="Bar interval ('1m', '5m', '15m', '1h', '1d').",
                    required=False,
                    default="1d",
                    choices=("1m", "5m", "15m", "1h", "1d"),
                ),
                ToolParameter(
                    name="indicators",
                    type_name="array",
                    description="List of indicators to compute (e.g. ['sma', 'rsi', 'macd']).",
                    required=False,
                    default=["sma", "rsi"],
                    items_type="string",
                    item_choices=(
                        "sma",
                        "ema",
                        "rsi",
                        "macd",
                        "atr",
                        "bollinger",
                        "bollinger_bands",
                        "vwap",
                        "obv",
                        "stochastic",
                    ),
                    max_items=9,
                ),
                ToolParameter(
                    name="limit",
                    type_name="integer",
                    description="Maximum number of historical candles to include (1-500).",
                    required=False,
                    default=100,
                    minimum=1,
                    maximum=500,
                ),
            ),
            permission=ToolPermission.READ_ONLY,
        ),
        _tool_compute_technical_indicators,
    )

    # 4. detect_price_action_patterns
    registry.register(
        ToolDefinition(
            name="detect_price_action_patterns",
            description="Detect multi-candle patterns, pinbars, dojis, engulfing formations, and swing highs/lows from historical data.",
            parameters=(
                ToolParameter(
                    name="symbol",
                    type_name="string",
                    description="Trading symbol of the instrument.",
                    required=True,
                ),
                ToolParameter(
                    name="exchange",
                    type_name="string",
                    description="Exchange identifier ('NSE' or 'BSE').",
                    required=False,
                    default="NSE",
                    choices=("NSE", "BSE"),
                ),
                ToolParameter(
                    name="interval",
                    type_name="string",
                    description="Bar interval ('1m', '5m', '15m', '1h', '1d').",
                    required=False,
                    default="1d",
                    choices=("1m", "5m", "15m", "1h", "1d"),
                ),
                ToolParameter(
                    name="limit",
                    type_name="integer",
                    description="Maximum number of historical candles to analyze (1-200).",
                    required=False,
                    default=50,
                    minimum=1,
                    maximum=500,
                ),
            ),
            permission=ToolPermission.READ_ONLY,
        ),
        _tool_detect_price_action_patterns,
    )

    # 5. get_backtest_summary
    registry.register(
        ToolDefinition(
            name="get_backtest_summary",
            description="Retrieve backtest experiment registrations, completed runs, and portfolio performance metrics (CAGR, Sharpe, Max Drawdown).",
            parameters=(
                ToolParameter(
                    name="backtest_id",
                    type_name="integer",
                    minimum=1,
                    maximum=2147483647,
                    description="Optional ID of a specific backtest experiment to inspect.",
                    required=False,
                    default=None,
                ),
            ),
            permission=ToolPermission.READ_ONLY,
        ),
        _tool_get_backtest_summary,
    )

    # 7. get_strategy_experiment_results
    registry.register(
        ToolDefinition(
            name="get_strategy_experiment_results",
            description="Retrieve Strategy Lab registered experiments, parameter configurations, and historical signal evaluation runs.",
            parameters=(
                ToolParameter(
                    name="experiment_id",
                    type_name="integer",
                    minimum=1,
                    maximum=2147483647,
                    description="Optional Strategy Lab experiment ID to inspect.",
                    required=False,
                    default=None,
                ),
            ),
            permission=ToolPermission.READ_ONLY,
        ),
        _tool_get_strategy_experiment_results,
    )

    return registry
