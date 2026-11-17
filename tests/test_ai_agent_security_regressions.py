from __future__ import annotations

import io
import json
import multiprocessing
import os
import time
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from aegis.ai_agent.models import (
    AgentResearchResult,
    ChatMessage,
    LLMResponse,
    ModelUsage,
    ProviderError,
    Role,
    ToolCallAudit,
    ToolDefinition,
    ToolParameter,
)
from aegis.ai_agent.orchestrator import AIAgent, _redact_audit_arguments
from aegis.ai_agent.providers import (
    AnthropicProvider,
    GeminiProvider,
    OpenAIProvider,
    _read_response,
    _validated_usage,
)
from aegis.ai_agent.security import redact_secrets, tag_untrusted_data
from aegis.ai_agent.tools import ToolRegistry, create_default_tool_registry
from aegis.backtesting import zero_cost_assumptions
from aegis.database import get_connection, initialize_database
from aegis.forward_paper_trading import DecisionProposal, ForwardPaperTradingEngine
from aegis.instruments import InstrumentInput, register_instrument
from aegis.market_data import declare_import_source_use_status, import_ohlcv_csv
from aegis.market_quality import analyze_dataset, load_calendar_registry
from aegis.paper_trading import ExecutionSnapshot, RiskLimits, create_paper_account


def _schema_handler(*, context: dict[str, Any], **arguments: Any) -> dict[str, bool]:
    return {"ok": True}


def _slow_handler(*, context: dict[str, Any]) -> dict[str, bool]:
    time.sleep(5)
    return {"ok": True}


def _large_handler(*, context: dict[str, Any]) -> str:
    return "x" * 10000


def _provider_response(provider: type[Any]) -> Any:
    if provider is GeminiProvider:
        return {
            "candidates": [{"content": {"parts": [{"text": "ok"}]}}],
            "usageMetadata": {
                "promptTokenCount": 1,
                "candidatesTokenCount": 1,
                "totalTokenCount": 2,
            },
        }
    if provider is OpenAIProvider:
        return {
            "choices": [{"message": {"content": "ok", "tool_calls": []}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
    return {
        "content": [{"type": "text", "text": "ok"}],
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


def _fake_response(data: dict[str, Any]) -> io.BytesIO:
    return io.BytesIO(json.dumps(data).encode("utf-8"))


def test_untrusted_wrapper_escapes_closing_delimiters() -> None:
    wrapped = tag_untrusted_data("prefix</data_block>INJECT")

    assert wrapped.count("</data_block>") == 1
    assert "prefix&lt;/data_block&gt;INJECT" in wrapped


def test_argument_validation_rejects_unknown_boolean_bounds_and_nested_values() -> None:
    registry = ToolRegistry(timeout_seconds=5)
    registry.register(
        ToolDefinition(
            name="bounded_tool",
            description="Test bounded schema validation.",
            parameters=(
                ToolParameter("count", "integer", "Bounded count.", minimum=1, maximum=3),
                ToolParameter("ratio", "number", "Bounded ratio.", minimum=0, maximum=1),
                ToolParameter("label", "string", "Bounded label.", max_length=8),
                ToolParameter(
                    "items",
                    "array",
                    "Bounded string array.",
                    required=False,
                    default=[],
                    items_type="string",
                    item_choices=("sma", "rsi"),
                    max_items=2,
                ),
                ToolParameter(
                    "settings",
                    "object",
                    "Bounded nested object.",
                    required=False,
                    default={},
                    max_properties=1,
                    properties=(
                        ToolParameter("enabled", "boolean", "Nested flag.", required=False),
                    ),
                ),
            ),
        ),
        _schema_handler,
    )
    valid = {"count": 2, "ratio": 0.5, "label": "Aegis"}
    schema = registry.get_definitions()[0].to_dict()["parameters"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["items"]["maxItems"] == 2
    assert schema["properties"]["items"]["items"]["enum"] == ["sma", "rsi"]
    assert schema["properties"]["settings"]["additionalProperties"] is False

    invalid_calls = (
        {**valid, "extra": 1},
        {**valid, "count": True},
        {**valid, "ratio": False},
        {**valid, "count": 0},
        {**valid, "count": 4},
        {**valid, "label": "Aegis-too-long"},
        {**valid, "items": ["sma", "rsi", "ema"]},
        {**valid, "items": ["sma", 1]},
        {**valid, "settings": {"unknown": True}},
        {**valid, "settings": {"enabled": True, "extra": False}},
    )
    for arguments in invalid_calls:
        result = registry.execute("bounded_tool", arguments, context={})
        assert '"status": "ERROR"' in result
    valid_result = registry.execute("bounded_tool", valid, context={})
    assert '"status": "ERROR"' not in valid_result
    assert '"ok": true' in valid_result

    invalid_root = registry.execute("bounded_tool", [], context={})  # type: ignore[arg-type]
    assert '"status": "ERROR"' in invalid_root


def test_decimal_numeric_bounds_and_defaults_are_validated_and_serialized() -> None:
    with pytest.raises(ValueError, match="finite number"):
        ToolParameter(
            "ratio",
            "number",
            "Decimal ratio bound.",
            minimum=Decimal("NaN"),
            maximum=Decimal("1.0"),
        )
    with pytest.raises(ValueError, match="finite numbers"):
        ToolParameter(
            "ratio",
            "number",
            "Decimal ratio default.",
            minimum=Decimal(0),
            maximum=Decimal(1),
            default=Decimal("NaN"),
        )

    param = ToolParameter(
        "ratio",
        "number",
        "Decimal ratio value.",
        minimum=Decimal("0.01"),
        maximum=Decimal("1.50"),
        default=Decimal("0.25"),
    )
    tool = ToolDefinition(
        name="decimal_rule",
        description="Test Decimal-validated numeric schema.",
        parameters=(param,),
    )
    schema = tool.to_dict()["parameters"]["properties"]["ratio"]

    assert schema["minimum"] == 0.01
    assert schema["maximum"] == 1.5
    assert schema["default"] == 0.25


@pytest.fixture(autouse=True)
def isolate_calendar_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AEGIS_CALENDAR_CONFIG_PATH", raising=False)


def _synthetic_calendar(path: Path, *, as_of: str) -> object:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "staleness_policy": {"potential_after_missing": 1, "stale_after_missing": 3},
                "calendars": [
                    {
                        "exchange": "NSE",
                        "segment": "NSE",
                        "timezone": "Asia/Kolkata",
                        "confidence": "CONFIRMED",
                        "coverage_start": as_of,
                        "coverage_end": as_of,
                        "source": "SYNTHETIC_TEST_ONLY",
                        "weekdays": [0, 1, 2, 3, 4],
                        "sessions": [{"open": "09:15", "close": "09:22"}],
                        "holidays": [],
                        "special_sessions": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    os.environ["AEGIS_CALENDAR_CONFIG_PATH"] = str(path)
    return load_calendar_registry(path)


def _write_candle_csv(path: Path, *, symbol: str, closes: list[int]) -> Path:
    rows = ["timestamp,open,high,low,close,volume"]
    for index, close in enumerate(closes):
        minute = 15 + index
        rows.append(
            f"2026-01-05T09:{minute:02d}:00+05:30,{close},{close + 2},{close - 2},{close},1000"
        )
    csv_path = path / f"{symbol}.csv"
    csv_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return csv_path


def test_default_registry_does_not_expose_paper_account_information(tmp_path: Path) -> None:
    registry = create_default_tool_registry(tmp_path / "research.db")
    names = {definition.name for definition in registry.get_definitions()}

    assert "get_paper_account_status" not in names
    assert len(names) == 6


def test_forward_paper_evidence_is_instrument_scoped_and_import_verified(tmp_path: Path) -> None:
    database = tmp_path / "forward_paper.db"
    initialize_database(database)
    calendar_path = tmp_path / "calendar.json"
    calendar = _synthetic_calendar(calendar_path, as_of="2026-01-05")

    alpha = register_instrument(
        database,
        InstrumentInput(
            "NSE", "ALPHA", "Alpha Synthetic", "EQUITY", segment="NSE", tick_size=Decimal("0.05")
        ),
    )
    beta = register_instrument(
        database,
        InstrumentInput(
            "NSE", "BETA", "Beta Synthetic", "EQUITY", segment="NSE", tick_size=Decimal("0.05")
        ),
    )
    alpha_csv = _write_candle_csv(tmp_path, symbol="ALPHA", closes=[100, 101, 102])
    beta_csv = _write_candle_csv(tmp_path, symbol="BETA", closes=[200, 201, 202])

    alpha_import = import_ohlcv_csv(database, alpha_csv, "NSE", "ALPHA", "1m")
    beta_import = import_ohlcv_csv(database, beta_csv, "NSE", "BETA", "1m")
    declare_import_source_use_status(
        database,
        alpha_import.import_id,
        "VERIFIED_PERMITTED",
        declared_by="test",
        rationale="synthetic",
    )
    declare_import_source_use_status(
        database,
        beta_import.import_id,
        "VERIFIED_PERMITTED",
        declared_by="test",
        rationale="synthetic",
    )

    engine = ForwardPaperTradingEngine(database, 1)
    alpha_evidence = engine.build_market_evidence(
        alpha.id,
        interval="1m",
        as_of="2026-01-05T09:18:00+05:30",
        calendars=calendar,
    )
    beta_evidence = engine.build_market_evidence(
        beta.id,
        interval="1m",
        as_of="2026-01-05T09:18:00+05:30",
        calendars=calendar,
    )

    assert alpha_evidence.instrument_id == alpha.id
    assert beta_evidence.instrument_id == beta.id
    assert alpha_evidence.reference_price == Decimal(102)
    assert beta_evidence.reference_price == Decimal(202)
    assert alpha_evidence.source_import_id == alpha_import.import_id
    assert beta_evidence.source_import_id == beta_import.import_id
    assert alpha_evidence.interval == "1m"

    quality = analyze_dataset(
        database,
        "NSE",
        "ALPHA",
        "1m",
        date(2026, 1, 5),
        date(2026, 1, 5),
        as_of=datetime(2026, 1, 5, 9, 17, tzinfo=ZoneInfo("Asia/Kolkata")),
        calendars=calendar,
    )
    assert quality.overall_status == "COMPLETE"


def test_forward_paper_route_requires_verified_execution_snapshot(tmp_path: Path) -> None:
    database = tmp_path / "forward_route.db"
    initialize_database(database)
    calendar = _synthetic_calendar(tmp_path / "route_calendar.json", as_of="2026-01-05")
    instrument = register_instrument(
        database,
        InstrumentInput(
            "NSE", "SYNC", "Synthetic Route", "EQUITY", segment="NSE", tick_size=Decimal("0.05")
        ),
    )
    csv_path = _write_candle_csv(
        tmp_path,
        symbol="SYNC",
        closes=[100, 101, 102, 103, 104, 105, 106],
    )
    imported = import_ohlcv_csv(database, csv_path, "NSE", "SYNC", "1m")
    declare_import_source_use_status(
        database,
        imported.import_id,
        "VERIFIED_PERMITTED",
        declared_by="test",
        rationale="synthetic",
    )
    with closing(get_connection(database)) as connection:
        connection.execute(
            "UPDATE data_imports SET completed_at = ? WHERE id = ?",
            ("2026-01-05T09:22:00+05:30", imported.import_id),
        )
        connection.commit()
    engine = ForwardPaperTradingEngine(
        database,
        1,
        risk_limits=RiskLimits(
            max_order_value=Decimal(100000),
            max_position_quantity=10,
            max_instrument_exposure=Decimal(100000),
            max_portfolio_exposure=Decimal(100000),
            max_daily_loss=Decimal(100000),
            max_market_data_age_seconds=600,
        ),
    )
    account_id = create_paper_account(
        database,
        name="route-test",
        initial_cash=Decimal(10000),
        risk_limits=RiskLimits(
            max_order_value=Decimal(100000),
            max_position_quantity=10,
            max_instrument_exposure=Decimal(100000),
            max_portfolio_exposure=Decimal(100000),
            max_daily_loss=Decimal(100000),
            max_market_data_age_seconds=600,
        ),
        costs=zero_cost_assumptions(),
        created_at="2026-01-05T09:15:00+05:30",
    )
    engine = ForwardPaperTradingEngine(
        database,
        account_id,
        max_execution_price_deviation=Decimal("0.05"),
        risk_limits=RiskLimits(
            max_order_value=Decimal(100000),
            max_position_quantity=10,
            max_instrument_exposure=Decimal(100000),
            max_portfolio_exposure=Decimal(100000),
            max_daily_loss=Decimal(100000),
            max_market_data_age_seconds=600,
        ),
    )

    evidence = engine.build_market_evidence(
        instrument.id,
        interval="1m",
        as_of="2026-01-05T09:22:00+05:30",
        calendars=calendar,
    )
    proposal_expiry = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    proposal = DecisionProposal(
        proposal_id="sync-buy-1",
        instrument_id=instrument.id,
        action="BUY",
        quantity=1,
        entry_price=evidence.reference_price,
        stop_loss=Decimal("99.00"),
        target_price=Decimal("110.00"),
        confidence=Decimal("0.8"),
        rationale="Synthetic route validation.",
        timeframe="1m",
        decision_timestamp="2026-01-05T09:22:00+05:30",
        submitted_at="2026-01-05T09:22:00+05:30",
        expires_at=proposal_expiry,
    )
    rejected = engine.route_proposal(proposal, evidence=evidence)
    assert rejected.status == "REJECTED"
    assert "execution snapshot" in (rejected.reason or "").lower()

    quality = analyze_dataset(
        database,
        "NSE",
        "SYNC",
        "1m",
        date(2026, 1, 5),
        date(2026, 1, 5),
        as_of=datetime(2026, 1, 5, 9, 22, 10, tzinfo=ZoneInfo("Asia/Kolkata")),
        calendars=calendar,
    )
    snapshot = ExecutionSnapshot(
        instrument_id=instrument.id,
        interval="1m",
        timestamp="2026-01-05T09:22:10+05:30",
        as_of="2026-01-05T09:22:10+05:30",
        reference_price=Decimal("106.00"),
        price_field="execution_quote",
        available_quantity=10,
        import_id=imported.import_id,
        quality=quality,
    )
    filled = engine.route_proposal(proposal, evidence=evidence, execution_snapshot=snapshot)
    assert filled.status == "FILLED"
    assert filled.order_id is not None


def test_tool_timeout_terminates_the_worker() -> None:
    registry = ToolRegistry(timeout_seconds=0.2)
    registry.register(
        ToolDefinition(name="slow_tool", description="Sleeps.", parameters=()),
        _slow_handler,
    )
    children_before = {child.pid for child in multiprocessing.active_children()}
    started = time.monotonic()

    output = registry.execute("slow_tool", {}, context={})

    assert '"status": "TIMEOUT"' in output
    assert time.monotonic() - started < 3
    assert {child.pid for child in multiprocessing.active_children()} == children_before


def test_tool_output_and_handler_configuration_are_bounded() -> None:
    registry = ToolRegistry(max_output_chars=64)
    definition = ToolDefinition(
        name="large_tool", description="Returns a large result.", parameters=()
    )
    registry.register(definition, _large_handler)
    output = registry.execute("large_tool", {}, context={})

    assert len(output) < 300
    assert "OUTPUT TRUNCATED" in output
    with pytest.raises(TypeError):
        registry.register(
            ToolDefinition(name="local_tool", description="Not importable.", parameters=()),
            lambda *, context: None,
        )
    with pytest.raises(ValueError):
        ToolRegistry(timeout_seconds=121)
    with pytest.raises(ValueError):
        ToolRegistry(max_output_chars=1_000_001)


def test_audit_arguments_are_redacted_without_mutating_provider_input() -> None:
    original = {
        "api_key": "test-secret-value",
        "nested": [{"message": "Bearer abc.def.ghi"}],
    }

    sanitized = _redact_audit_arguments(original)

    assert sanitized["api_key"] == "[REDACTED]"
    assert "abc.def.ghi" not in repr(sanitized)
    assert original["api_key"] == "test-secret-value"
    assert "?key=[REDACTED]&" in redact_secrets("https://example.test?key=secret&x=1")


def test_confidence_reports_empty_or_unverified_data_as_insufficient(tmp_path: Path) -> None:
    agent = AIAgent(
        provider=object(),  # type: ignore[arg-type]
        tool_registry=ToolRegistry(),
        database_path=tmp_path / "research.db",
    )
    call = ToolCallAudit(
        call_id="call-1",
        tool_name="compute_technical_indicators",
        arguments={},
        output_summary="No historical candles found; candles_count=0",
        is_error=False,
        duration_ms=1,
    )
    tool_message = ChatMessage(
        role=Role.TOOL,
        content='{"candles_count": 0, "message": "No historical candles found"}',
        name="compute_technical_indicators",
    )

    confidence = agent._assess_confidence([tool_message], [call])
    evidence = agent._extract_evidence([tool_message])

    assert confidence.startswith("Low")
    assert evidence and "candles_count" in evidence[0]
    assert "High" not in confidence


class _CountingProvider:
    name = "test"
    model_id = "counting"

    def __init__(self) -> None:
        self.calls = 0

    def generate(self, messages: list[ChatMessage], tools: Any = None) -> LLMResponse:
        self.calls += 1
        return LLMResponse(
            message=ChatMessage(role=Role.ASSISTANT, content="done"),
            usage=ModelUsage(prompt_tokens=5, completion_tokens=6, total_tokens=11),
        )


class _MalformedToolCallProvider:
    name = "test"
    model_id = "malformed"

    def generate(self, messages: list[ChatMessage], tools: Any = None) -> LLMResponse:
        return LLMResponse(
            message=ChatMessage(
                role=Role.ASSISTANT,
                content="",
                tool_calls=(None,),  # type: ignore[arg-type]
            )
        )


def test_agent_rejects_malformed_provider_tool_calls(tmp_path: Path) -> None:
    agent = AIAgent(
        provider=_MalformedToolCallProvider(),  # type: ignore[arg-type]
        tool_registry=ToolRegistry(),
        database_path=tmp_path / "research.db",
    )

    result = agent.run_research("Inspect only deterministic research evidence.")

    assert result.status == "FAILED"
    assert "malformed tool calls" in result.error
    assert result.tools_invoked == ()


def test_aggregate_token_budget_fails_closed_and_constructor_limits_are_validated(
    tmp_path: Path,
) -> None:
    provider = _CountingProvider()
    agent = AIAgent(
        provider=provider,  # type: ignore[arg-type]
        tool_registry=ToolRegistry(),
        database_path=tmp_path / "research.db",
        max_total_tokens=10,
    )
    result: AgentResearchResult = agent.run_research("Summarize deterministic evidence.")

    assert result.status == "FAILED"
    assert result.audit_record.status == "FAILED"
    assert provider.calls == 1
    with pytest.raises(ValueError):
        AIAgent(
            provider=provider,  # type: ignore[arg-type]
            tool_registry=ToolRegistry(),
            database_path=tmp_path / "research.db",
            max_steps=True,
        )
    with pytest.raises(ValueError):
        ToolRegistry(max_output_chars=0)
    with pytest.raises(ValueError):
        AIAgent(
            provider=provider,  # type: ignore[arg-type]
            tool_registry=ToolRegistry(),
            database_path=tmp_path / "research.db",
            max_steps=21,
        )
    with pytest.raises(ValueError):
        AIAgent(
            provider=provider,  # type: ignore[arg-type]
            tool_registry=ToolRegistry(),
            database_path=tmp_path / "research.db",
            max_total_tokens=100_001,
        )


@pytest.mark.parametrize(
    ("provider_type", "malformed_body"),
    [
        (
            GeminiProvider,
            {
                "candidates": [
                    {"content": {"parts": [{"functionCall": {"name": "tool", "args": []}}]}}
                ]
            },
        ),
        (
            OpenAIProvider,
            {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "function": {"name": "tool", "arguments": "[]"},
                                }
                            ]
                        }
                    }
                ]
            },
        ),
        (
            AnthropicProvider,
            {"content": [{"type": "tool_use", "id": "call-1", "name": "tool", "input": []}]},
        ),
    ],
)
def test_providers_reject_malformed_tool_arguments(
    monkeypatch: pytest.MonkeyPatch,
    provider_type: type[Any],
    malformed_body: dict[str, Any],
) -> None:
    monkeypatch.setattr(
        "aegis.ai_agent.providers.urllib.request.urlopen",
        lambda request, timeout: _fake_response(malformed_body),
    )
    provider = provider_type(api_key="test-key")

    with pytest.raises(ProviderError):
        provider.generate(messages=[])


@pytest.mark.parametrize(
    ("provider_type", "payload_path"),
    [
        (GeminiProvider, ("generationConfig", "maxOutputTokens")),
        (OpenAIProvider, ("max_tokens",)),
        (AnthropicProvider, ("max_tokens",)),
    ],
)
def test_provider_output_token_caps_are_sent(
    monkeypatch: pytest.MonkeyPatch,
    provider_type: type[Any],
    payload_path: tuple[str, ...],
) -> None:
    captured: dict[str, Any] = {}
    response_body = _provider_response(provider_type)

    def fake_urlopen(request: Any, timeout: float) -> io.BytesIO:
        captured.update(json.loads(request.data))
        return _fake_response(response_body)

    monkeypatch.setattr("aegis.ai_agent.providers.urllib.request.urlopen", fake_urlopen)
    provider = provider_type(api_key="test-key", max_output_tokens=73)
    provider.generate(messages=[])

    value = captured
    for key in payload_path:
        value = value[key]
    assert value == 73


def test_provider_response_size_and_usage_are_required() -> None:
    class OversizedResponse:
        def read(self, size: int) -> bytes:
            return b"x" * size

    with pytest.raises(ProviderError, match="size limit"):
        _read_response(OversizedResponse(), "test provider")
    with pytest.raises(ProviderError, match="omitted token usage"):
        _validated_usage({}, "test provider", "input_tokens", "output_tokens")
