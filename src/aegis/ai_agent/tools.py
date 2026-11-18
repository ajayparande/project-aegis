"""Controlled, permissioned read-only tool registry and research tools for Project Aegis."""

from __future__ import annotations

import json
import math
import multiprocessing
import pickle
import threading
import time
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

from aegis.ai_agent.models import (
    SecurityViolationError,
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
from aegis.historical_research import (
    MAX_INSTRUMENT_RESULTS,
    MAX_RESEARCH_CANDLE_LIMIT,
    discover_historical_instruments,
    inspect_historical_coverage,
    inspect_historical_data_quality,
    retrieve_historical_candles,
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
from aegis.market_data import (
    SUPPORTED_INTERVALS,
    list_historical_candles,
    source_use_is_eligible,
)
from aegis.market_quality import (
    CalendarConfigError,
    CalendarRegistry,
    load_approved_calendar_registry,
)

_MIN_TRUNCATED_JSON_LENGTH = len('{"status":"TRUNCATED","complete":false}')
_TOOL_SUCCESS_STATUS = "OK"
_TOOL_ERROR_FRAME = b'["ERROR","Tool handler failed; internal details are withheld."]'


def _maximum_tool_frame_bytes(max_output_chars: int) -> int:
    """Bound both worker frames using exact integer arithmetic and ASCII JSON encoding."""
    if isinstance(max_output_chars, bool) or not isinstance(max_output_chars, int):
        raise TypeError("max_output_chars must be a positive integer.")
    if not _MIN_TRUNCATED_JSON_LENGTH <= max_output_chars <= 1_000_000:
        raise ValueError("max_output_chars is outside the permitted transport range.")
    # OK has a fixed status and envelope; each output code point can require two
    # six-byte surrogate escapes. ERROR has a fixed status and fixed message.
    success_overhead = len(
        json.dumps([_TOOL_SUCCESS_STATUS, ""], ensure_ascii=True).encode("utf-8")
    )
    return max(success_overhead + 12 * max_output_chars, len(_TOOL_ERROR_FRAME))


def _bounded_tool_output(output: str, max_output_chars: int) -> str:
    if len(output) <= max_output_chars:
        return output

    envelope: dict[str, Any] = {
        "status": "TRUNCATED",
        "complete": False,
        "truncated": True,
    }

    def compact(value: dict[str, Any]) -> str:
        return json.dumps(value, separators=(",", ":"), ensure_ascii=True)

    if len(compact(envelope)) > max_output_chars:
        envelope = {"status": "TRUNCATED", "complete": False}
    if len(compact(envelope)) > max_output_chars:
        raise ValueError("max_output_chars cannot fit the truncation envelope.")

    preview_envelope = {**envelope, "preview": ""}
    if len(compact(preview_envelope)) > max_output_chars:
        return compact(envelope)

    low = 0
    high = len(output)
    best = compact(preview_envelope)
    while low <= high:
        middle = (low + high) // 2
        candidate = compact({**envelope, "preview": output[:middle]})
        if len(candidate) <= max_output_chars:
            best = candidate
            low = middle + 1
        else:
            high = middle - 1
    return best


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
        result = _bounded_tool_output(result, max_output_chars)
        connection.send_bytes(
            json.dumps([_TOOL_SUCCESS_STATUS, result], ensure_ascii=True).encode("utf-8")
        )
    except Exception:  # noqa: BLE001
        connection.send_bytes(_TOOL_ERROR_FRAME)
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
        if not _MIN_TRUNCATED_JSON_LENGTH <= max_output_chars <= 1_000_000:
            raise ValueError(
                f"max_output_chars must be between {_MIN_TRUNCATED_JSON_LENGTH} and 1000000."
            )
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
        # A readable pipe may contain only a partial frame. Receive on a disposable
        # reader thread so even an incomplete frame cannot defeat the deadline.
        deadline = time.monotonic() + self._timeout_seconds
        max_frame_bytes = _maximum_tool_frame_bytes(self._max_output_chars)
        process_context = multiprocessing.get_context("spawn")
        receiver, sender = process_context.Pipe(duplex=False)
        process = process_context.Process(
            target=_tool_worker,
            args=(sender, handler, arguments, context, self._max_output_chars),
            daemon=True,
        )
        started = False
        received = threading.Event()
        messages: list[bytes | Exception] = []
        reader: threading.Thread | None = None

        def receive() -> None:
            try:
                # Bound the encoded frame independently of the decoded character limit.
                messages.append(receiver.recv_bytes(max_frame_bytes))
            except Exception as exc:  # noqa: BLE001
                messages.append(exc)
            finally:
                received.set()

        try:
            process.start()
            started = True
            sender.close()
            reader = threading.Thread(target=receive, name="aegis-tool-receiver", daemon=True)
            reader.start()
            if not received.wait(max(0.0, deadline - time.monotonic())):
                raise TimeoutError("Tool execution exceeded its time limit.")
            message = messages[0]
            if isinstance(message, Exception):
                raise RuntimeError("Tool worker transport failed.") from message  # noqa: TRY004
            try:
                document = json.loads(message.decode("utf-8"))
            except (UnicodeDecodeError, ValueError, RecursionError) as exc:
                raise RuntimeError("Tool worker returned an invalid result.") from exc
            if (
                type(document) is not list
                or len(document) != 2
                or document[0] != _TOOL_SUCCESS_STATUS
                or type(document[1]) is not str
                or len(document[1]) > self._max_output_chars
            ):
                raise RuntimeError("Tool worker failed; internal details are withheld.")
            if time.monotonic() >= deadline:
                raise TimeoutError("Tool execution exceeded its time limit.")
            process.join(max(0.0, deadline - time.monotonic()))
            if process.is_alive():
                raise TimeoutError("Tool execution exceeded its time limit.")
            if process.exitcode != 0:
                raise RuntimeError("Tool worker exited unsuccessfully.")
            return str(document[1])
        finally:
            if started and process.is_alive():
                process.terminate()
                process.join(1.0)
                if process.is_alive():
                    process.kill()
                    process.join(1.0)
            receiver.close()
            sender.close()
            if reader is not None:
                reader.join(1.0)
            if started and not process.is_alive():
                process.close()

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

            output_str = _bounded_tool_output(output_str, self._max_output_chars)

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
        except (SecurityViolationError, TypeError, ValueError) as exc:
            return tag_untrusted_data(
                json.dumps(
                    {
                        "error": str(exc),
                        "status": "REJECTED",
                    }
                ),
                label=f"{tool_name}_rejected",
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
    report = inspect_historical_data_quality(
        database_path,
        exchange=exchange,
        trading_symbol=symbol,
        interval=interval,
        start_date=start_date,
        end_date=end_date,
        calendars=calendars,
    )
    quality = report["data_quality"]
    if (
        quality.get("calendar_status") != "FULL"
        or quality.get("calendar_confidence") != "CONFIRMED"
    ):
        raise ValueError("Quality summaries require full CONFIRMED approved calendar coverage.")
    expected_candles = quality.get("expected_candles")
    passes_data_quality_gate = (
        quality.get("overall_status") == "COMPLETE"
        and quality.get("calendar_status") == "FULL"
        and quality.get("calendar_confidence") == "CONFIRMED"
        and type(expected_candles) is int
        and expected_candles > 0
    )
    source_use_eligibility = report["source_use_eligibility_status"]
    passes_backtest_gate = passes_data_quality_gate and source_use_eligibility == "PASS"

    return {
        "exchange": exchange,
        "trading_symbol": symbol,
        "interval": interval,
        "requested_start": report["requested_start_date"],
        "requested_end": report["requested_end_date"],
        "overall_status": quality.get("overall_status"),
        "data_quality_status": quality.get("status", "UNKNOWN"),
        "calendar_status": quality.get("calendar_status"),
        "calendar_confidence": quality.get("calendar_confidence"),
        "actual_candles": quality.get("actual_candles"),
        "expected_candles": expected_candles,
        "missing_candles": quality.get("missing_candle_count"),
        "duplicate_candles": quality.get("duplicate_identity_count"),
        "invalid_candles": quality.get("invalid_candle_count"),
        "completeness_percentage": str(quality.get("completeness_percentage")),
        "passes_data_quality_gate": passes_data_quality_gate,
        "source_use_statuses": report["source_use_statuses"],
        "source_use_eligibility_status": source_use_eligibility,
        "eligibility_status": report["eligibility_status"],
        "eligible_for_research": passes_backtest_gate,
        "passes_backtest_gate": passes_backtest_gate,
        "reasons": quality.get("warnings", []),
    }


def _list_research_candles(
    database_path: Path,
    *,
    exchange: str,
    symbol: str,
    interval: str,
    limit: int,
) -> tuple[list[dict[str, object]], list[str], bool]:
    candles = list_historical_candles(
        database_path=database_path,
        exchange=exchange,
        trading_symbol=symbol,
        interval=interval,
        limit=limit,
    )
    source_statuses = sorted({str(row.get("source_use_status")) for row in candles})
    return candles, source_statuses, source_use_is_eligible(source_statuses)


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

    candle_dicts, source_statuses, source_eligible = _list_research_candles(
        database_path,
        exchange=exchange,
        symbol=symbol,
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

    if not source_eligible:
        return {
            "status": "INELIGIBLE_SOURCE_USE",
            "symbol": symbol,
            "exchange": exchange,
            "interval": interval,
            "candles_analyzed": 0,
            "source_use_statuses": source_statuses,
            "message": "Every contributing import must be declared VERIFIED_PERMITTED.",
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
        "source_use_statuses": source_statuses,
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

    candle_dicts, source_statuses, source_eligible = _list_research_candles(
        database_path,
        exchange=exchange,
        symbol=symbol,
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

    if not source_eligible:
        return {
            "status": "INELIGIBLE_SOURCE_USE",
            "symbol": symbol,
            "exchange": exchange,
            "interval": interval,
            "candles_analyzed": 0,
            "source_use_statuses": source_statuses,
            "message": "Every contributing import must be declared VERIFIED_PERMITTED.",
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
        "source_use_statuses": source_statuses,
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


def _tool_discover_historical_instruments(
    interval: str,
    exchange: str | None = None,
    trading_symbol: str | None = None,
    start_timestamp: str | None = None,
    end_timestamp: str | None = None,
    minimum_candles: int = 1,
    quality_status: str = "ANY",
    limit: int = 50,
    cursor: str | None = None,
    *,
    context: dict[str, Any],
) -> dict[str, Any]:
    try:
        return discover_historical_instruments(
            context["database_path"],
            interval=interval,
            exchange=exchange,
            trading_symbol=trading_symbol,
            start_timestamp=start_timestamp,
            end_timestamp=end_timestamp,
            minimum_candles=minimum_candles,
            quality_status=quality_status,
            limit=limit,
            cursor=cursor,
        )
    except (TypeError, ValueError) as exc:
        return {"status": "REJECTED", "error": str(exc)}


def _tool_inspect_historical_coverage(
    exchange: str,
    trading_symbol: str,
    interval: str,
    start_timestamp: str | None = None,
    end_timestamp: str | None = None,
    *,
    context: dict[str, Any],
) -> dict[str, Any]:
    try:
        return inspect_historical_coverage(
            context["database_path"],
            exchange=exchange,
            trading_symbol=trading_symbol,
            interval=interval,
            start_timestamp=start_timestamp,
            end_timestamp=end_timestamp,
        )
    except (TypeError, ValueError) as exc:
        return {"status": "REJECTED", "error": str(exc)}


def _tool_retrieve_historical_candles(
    exchange: str,
    trading_symbol: str,
    interval: str,
    start_timestamp: str,
    end_timestamp: str,
    limit: int = 50,
    cursor: str | None = None,
    *,
    context: dict[str, Any],
) -> dict[str, Any]:
    try:
        return retrieve_historical_candles(
            context["database_path"],
            exchange=exchange,
            trading_symbol=trading_symbol,
            interval=interval,
            start_timestamp=start_timestamp,
            end_timestamp=end_timestamp,
            limit=limit,
            cursor=cursor,
        )
    except (TypeError, ValueError) as exc:
        return {"status": "REJECTED", "error": str(exc)}


def _tool_inspect_historical_data_quality(
    exchange: str,
    trading_symbol: str,
    interval: str,
    start_date: str,
    end_date: str,
    *,
    context: dict[str, Any],
) -> dict[str, Any]:
    try:
        return inspect_historical_data_quality(
            context["database_path"],
            exchange=exchange,
            trading_symbol=trading_symbol,
            interval=interval,
            start_date=start_date,
            end_date=end_date,
        )
    except (TypeError, ValueError) as exc:
        return {"status": "REJECTED", "error": str(exc)}


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

    # 7. discover_historical_instruments
    registry.register(
        ToolDefinition(
            name="discover_historical_instruments",
            description=(
                "Discover instruments with stored candles and report separate source-use, "
                "data-quality, and research-eligibility statuses. Results are paginated; "
                "follow next_cursor until complete is true before treating discovery as exhaustive."
            ),
            parameters=(
                ToolParameter(
                    name="interval",
                    type_name="string",
                    description="Stored candle timeframe.",
                    choices=SUPPORTED_INTERVALS,
                ),
                ToolParameter(
                    name="exchange",
                    type_name="string",
                    description="Optional exact exchange filter (NSE or BSE).",
                    required=False,
                    default=None,
                    choices=("NSE", "BSE"),
                ),
                ToolParameter(
                    name="trading_symbol",
                    type_name="string",
                    description="Optional exact trading-symbol filter.",
                    required=False,
                    default=None,
                    min_length=1,
                    max_length=64,
                ),
                ToolParameter(
                    name="start_timestamp",
                    type_name="string",
                    description="Optional inclusive ISO-8601 timestamp with timezone.",
                    required=False,
                    default=None,
                    max_length=40,
                ),
                ToolParameter(
                    name="end_timestamp",
                    type_name="string",
                    description="Optional inclusive ISO-8601 timestamp with timezone.",
                    required=False,
                    default=None,
                    max_length=40,
                ),
                ToolParameter(
                    name="minimum_candles",
                    type_name="integer",
                    description="Minimum stored records required for discovery.",
                    required=False,
                    default=1,
                    minimum=1,
                    maximum=1_000_000,
                ),
                ToolParameter(
                    name="quality_status",
                    type_name="string",
                    description="Quality filter: ANY, PASS, FAIL, or UNKNOWN.",
                    required=False,
                    default="ANY",
                    choices=("ANY", "PASS", "FAIL", "UNKNOWN"),
                ),
                ToolParameter(
                    name="limit",
                    type_name="integer",
                    description=f"Maximum instruments to return (1-{MAX_INSTRUMENT_RESULTS}).",
                    required=False,
                    default=50,
                    minimum=1,
                    maximum=MAX_INSTRUMENT_RESULTS,
                ),
                ToolParameter(
                    name="cursor",
                    type_name="string",
                    description="Opaque continuation cursor from a prior partial discovery result.",
                    required=False,
                    default=None,
                    min_length=1,
                    max_length=2048,
                ),
            ),
            permission=ToolPermission.READ_ONLY,
        ),
        _tool_discover_historical_instruments,
    )

    # 8. inspect_historical_coverage
    registry.register(
        ToolDefinition(
            name="inspect_historical_coverage",
            description=(
                "Inspect first/last candle, stored record count, continuity, source-use status, "
                "and data-quality eligibility for one instrument and timeframe."
            ),
            parameters=(
                ToolParameter(
                    name="exchange",
                    type_name="string",
                    description="Instrument exchange (NSE or BSE).",
                    choices=("NSE", "BSE"),
                ),
                ToolParameter(
                    name="trading_symbol",
                    type_name="string",
                    description="Exact instrument trading symbol.",
                    min_length=1,
                    max_length=64,
                ),
                ToolParameter(
                    name="interval",
                    type_name="string",
                    description="Stored candle timeframe.",
                    choices=SUPPORTED_INTERVALS,
                ),
                ToolParameter(
                    name="start_timestamp",
                    type_name="string",
                    description="Optional inclusive ISO-8601 timestamp with timezone.",
                    required=False,
                    default=None,
                    max_length=40,
                ),
                ToolParameter(
                    name="end_timestamp",
                    type_name="string",
                    description="Optional inclusive ISO-8601 timestamp with timezone.",
                    required=False,
                    default=None,
                    max_length=40,
                ),
            ),
            permission=ToolPermission.READ_ONLY,
        ),
        _tool_inspect_historical_coverage,
    )

    # 9. retrieve_historical_candles
    registry.register(
        ToolDefinition(
            name="retrieve_historical_candles",
            description=(
                "Retrieve a page of at most 100 chronological candles using inclusive "
                "timezone-aware timestamps. Follow next_cursor while has_more is true; "
                "all contributing imports must be VERIFIED_PERMITTED."
            ),
            parameters=(
                ToolParameter(
                    name="exchange",
                    type_name="string",
                    description="Instrument exchange (NSE or BSE).",
                    choices=("NSE", "BSE"),
                ),
                ToolParameter(
                    name="trading_symbol",
                    type_name="string",
                    description="Exact instrument trading symbol.",
                    min_length=1,
                    max_length=64,
                ),
                ToolParameter(
                    name="interval",
                    type_name="string",
                    description="Stored candle timeframe.",
                    choices=SUPPORTED_INTERVALS,
                ),
                ToolParameter(
                    name="start_timestamp",
                    type_name="string",
                    description="Inclusive ISO-8601 start timestamp with timezone.",
                    max_length=40,
                ),
                ToolParameter(
                    name="end_timestamp",
                    type_name="string",
                    description="Inclusive ISO-8601 end timestamp with timezone.",
                    max_length=40,
                ),
                ToolParameter(
                    name="limit",
                    type_name="integer",
                    description=f"Maximum candles to return (1-{MAX_RESEARCH_CANDLE_LIMIT}).",
                    required=False,
                    default=50,
                    minimum=1,
                    maximum=MAX_RESEARCH_CANDLE_LIMIT,
                ),
                ToolParameter(
                    name="cursor",
                    type_name="string",
                    description="Opaque continuation cursor from a prior candle page.",
                    required=False,
                    default=None,
                    min_length=1,
                    max_length=2048,
                ),
            ),
            permission=ToolPermission.READ_ONLY,
        ),
        _tool_retrieve_historical_candles,
    )

    # 10. inspect_historical_data_quality
    registry.register(
        ToolDefinition(
            name="inspect_historical_data_quality",
            description=(
                "Expose the existing local quality report for an exchange-local inclusive "
                "date range; unavailable calendar confidence remains UNKNOWN."
            ),
            parameters=(
                ToolParameter(
                    name="exchange",
                    type_name="string",
                    description="Instrument exchange (NSE or BSE).",
                    choices=("NSE", "BSE"),
                ),
                ToolParameter(
                    name="trading_symbol",
                    type_name="string",
                    description="Exact instrument trading symbol.",
                    min_length=1,
                    max_length=64,
                ),
                ToolParameter(
                    name="interval",
                    type_name="string",
                    description="Stored candle timeframe.",
                    choices=SUPPORTED_INTERVALS,
                ),
                ToolParameter(
                    name="start_date",
                    type_name="string",
                    description="Inclusive exchange-local date in YYYY-MM-DD format.",
                    min_length=10,
                    max_length=10,
                ),
                ToolParameter(
                    name="end_date",
                    type_name="string",
                    description="Inclusive exchange-local date in YYYY-MM-DD format.",
                    min_length=10,
                    max_length=10,
                ),
            ),
            permission=ToolPermission.READ_ONLY,
        ),
        _tool_inspect_historical_data_quality,
    )

    return registry
