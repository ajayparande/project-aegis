"""Data models, message structures, and audit schemas for the Aegis AI agent."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any


def _is_finite_numeric(value: Any) -> bool:
    """Return True for finite numbers that are valid JSON schema quantities."""
    if isinstance(value, bool):
        return False
    if isinstance(value, Decimal):
        return value.is_finite()
    if isinstance(value, (int, float)):
        return math.isfinite(value)
    return False


def _json_number(value: Any) -> int | float:
    """Convert a finite numeric value to a JSON-friendly scalar."""
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Numeric values must be finite.")
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Numeric values must be finite.")
        return value
    raise TypeError(f"Unsupported numeric value: {type(value)!r}")


class Role(str, Enum):
    """Chat message roles supported across LLM providers."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ToolPermission(str, Enum):
    """Enforced permission levels for tools in Project Aegis."""

    READ_ONLY = "read_only"
    RESEARCH = "research"
    ADMIN = "admin"
    MUTATION = "mutation"


class AgentError(Exception):
    """Base exception for all AI agent framework errors."""


class ProviderError(AgentError):
    """Raised when an external or mock LLM provider fails."""


class ProviderTimeoutError(ProviderError):
    """Raised when an LLM provider call exceeds the configured timeout."""


class ToolExecutionError(AgentError):
    """Raised when a tool encounters an unhandled execution failure."""


class SecurityViolationError(AgentError):
    """Raised when an unauthorized action or prompt injection is detected."""


@dataclass(frozen=True, slots=True)
class ToolParameter:
    """Specification of a single parameter accepted by a tool."""

    name: str
    type_name: str  # "string", "integer", "number", "boolean", "array", "object"
    description: str
    required: bool = True
    default: Any = None
    choices: tuple[Any, ...] | None = None
    minimum: int | float | None = None
    maximum: int | float | None = None
    min_length: int | None = None
    max_length: int | None = None
    min_items: int | None = None
    max_items: int | None = None
    items_type: str | None = None
    item_choices: tuple[Any, ...] | None = None
    properties: tuple[ToolParameter, ...] | None = None
    max_properties: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name or len(self.name) > 128:
            raise ValueError("Parameter name must be a non-empty string of at most 128 characters.")
        if not isinstance(self.description, str) or len(self.description) > 1024:
            raise ValueError("Parameter description must be a string of at most 1024 characters.")
        if self.type_name not in {"string", "integer", "number", "boolean", "array", "object"}:
            raise ValueError(f"Unsupported tool parameter type: {self.type_name}.")
        if (self.minimum is not None or self.maximum is not None) and self.type_name not in {
            "integer",
            "number",
        }:
            raise ValueError("Numeric bounds require a numeric parameter.")
        if self.minimum is not None and not _is_finite_numeric(self.minimum):
            raise ValueError("Parameter minimum must be a finite number.")
        if self.maximum is not None and not _is_finite_numeric(self.maximum):
            raise ValueError("Parameter maximum must be a finite number.")
        if self.default is not None and self.type_name in {"integer", "number"}:
            if self.type_name == "integer":
                if isinstance(self.default, bool) or not isinstance(self.default, int):
                    raise ValueError("Integer defaults must be integers.")
            elif not _is_finite_numeric(self.default):
                raise ValueError("Numeric defaults must be finite numbers.")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("Parameter minimum cannot exceed its maximum.")
        for name, value in (
            ("min_length", self.min_length),
            ("max_length", self.max_length),
            ("min_items", self.min_items),
            ("max_items", self.max_items),
            ("max_properties", self.max_properties),
        ):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise ValueError(f"{name} must be a non-negative integer.")
        if (
            self.min_length is not None
            and self.max_length is not None
            and self.min_length > self.max_length
        ):
            raise ValueError("Minimum string length cannot exceed its maximum.")
        if (
            self.min_length is not None or self.max_length is not None
        ) and self.type_name != "string":
            raise ValueError("String length constraints require a string parameter.")
        if self.type_name == "array":
            if self.items_type not in {
                None,
                "string",
                "integer",
                "number",
                "boolean",
                "object",
            }:
                raise ValueError("Array item type is unsupported.")
            if (
                self.min_items is not None
                and self.max_items is not None
                and self.min_items > self.max_items
            ):
                raise ValueError("Minimum array size cannot exceed its maximum.")
        elif self.items_type is not None or self.item_choices is not None:
            raise ValueError("Array item constraints require an array parameter.")
        if (self.min_items is not None or self.max_items is not None) and self.type_name != "array":
            raise ValueError("Item count constraints require an array parameter.")
        if self.properties is not None:
            if self.type_name != "object" and not (
                self.type_name == "array" and self.items_type == "object"
            ):
                raise ValueError("Nested properties require an object parameter.")
            names = [item.name for item in self.properties]
            if len(names) != len(set(names)):
                raise ValueError("Nested object property names must be unique.")
        if self.choices is not None and not self.choices:
            raise ValueError("Parameter choices cannot be empty.")
        if self.item_choices is not None and not self.item_choices:
            raise ValueError("Array item choices cannot be empty.")
        if (
            self.max_properties is not None
            and self.type_name != "object"
            and not (self.type_name == "array" and self.items_type == "object")
        ):
            raise ValueError("max_properties requires an object parameter.")


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """Metadata, parameter schema, and permission level for a registered tool."""

    name: str
    description: str
    parameters: tuple[ToolParameter, ...]
    permission: ToolPermission = ToolPermission.READ_ONLY

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name or len(self.name) > 128:
            raise ValueError("Tool name must be a non-empty string of at most 128 characters.")
        if not isinstance(self.description, str) or len(self.description) > 1024:
            raise ValueError("Tool description must be a string of at most 1024 characters.")
        if not isinstance(self.permission, ToolPermission):
            raise TypeError("Tool permission must be a ToolPermission value.")
        names = [parameter.name for parameter in self.parameters]
        if len(names) != len(set(names)):
            raise ValueError("Tool parameter names must be unique.")

    def to_dict(self) -> dict[str, Any]:
        """Convert tool definition to JSON-schema compatible format for LLMs."""

        def parameter_schema(param: ToolParameter) -> dict[str, Any]:
            param_spec: dict[str, Any] = {
                "type": param.type_name,
                "description": param.description,
            }
            if param.choices:
                param_spec["enum"] = [
                    _json_number(choice) if isinstance(choice, Decimal) else choice
                    for choice in param.choices
                ]
            if param.default is not None:
                param_spec["default"] = (
                    _json_number(param.default)
                    if isinstance(param.default, (Decimal, int, float))
                    and not isinstance(param.default, bool)
                    else param.default
                )
            if param.minimum is not None:
                param_spec["minimum"] = _json_number(param.minimum)
            if param.maximum is not None:
                param_spec["maximum"] = _json_number(param.maximum)
            if param.type_name == "string":
                param_spec["minLength"] = param.min_length if param.min_length is not None else 0
                param_spec["maxLength"] = param.max_length if param.max_length is not None else 256
            if param.min_length is not None:
                param_spec["minLength"] = param.min_length
            if param.max_length is not None:
                param_spec["maxLength"] = param.max_length
            if param.type_name == "array":
                param_spec["items"] = parameter_schema(
                    ToolParameter(
                        name="item",
                        type_name=param.items_type or "string",
                        description=f"Item of {param.name}.",
                        choices=param.item_choices,
                        properties=param.properties,
                        max_properties=param.max_properties,
                    )
                )
                if param.item_choices:
                    param_spec["items"]["enum"] = list(param.item_choices)
                if param.min_items is not None:
                    param_spec["minItems"] = param.min_items
                param_spec["maxItems"] = param.max_items if param.max_items is not None else 100
            if param.type_name == "object":
                nested_properties = param.properties or ()
                param_spec["properties"] = {
                    item.name: parameter_schema(item) for item in nested_properties
                }
                param_spec["required"] = [item.name for item in nested_properties if item.required]
                param_spec["additionalProperties"] = False
                param_spec["maxProperties"] = (
                    param.max_properties
                    if param.max_properties is not None
                    else min(len(nested_properties), 32)
                )
            return param_spec

        properties: dict[str, Any] = {}
        required_params: list[str] = []

        for param in self.parameters:
            properties[param.name] = parameter_schema(param)
            if param.required:
                required_params.append(param.name)

        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required_params,
                "additionalProperties": False,
            },
        }


@dataclass(frozen=True, slots=True)
class ToolCall:
    """A tool invocation requested by the language model."""

    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Result of executing a tool call, formatted for LLM consumption."""

    call_id: str
    name: str
    content: str
    is_error: bool = False


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """A unified chat message exchangeable across providers."""

    role: Role
    content: str
    tool_calls: tuple[ToolCall, ...] | None = None
    tool_call_id: str | None = None
    name: str | None = None


@dataclass(frozen=True, slots=True)
class ModelUsage:
    """Token usage metrics for an LLM request."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True, slots=True)
class LLMResponse:
    """Standardized response from any LLM provider."""

    message: ChatMessage
    usage: ModelUsage = field(default_factory=ModelUsage)
    finish_reason: str = "stop"


@dataclass(frozen=True, slots=True)
class ToolCallAudit:
    """Audit log entry for a single executed tool call."""

    call_id: str
    tool_name: str
    arguments: dict[str, Any]
    output_summary: str
    is_error: bool
    duration_ms: float


@dataclass(frozen=True, slots=True)
class AgentAuditRecord:
    """Complete provenance and audit metadata for one research query."""

    audit_id: str
    timestamp_utc: str
    provider_name: str
    model_id: str
    prompt_sha256: str
    system_prompt_sha256: str
    tools_called: tuple[ToolCallAudit, ...]
    total_duration_ms: float
    token_usage: ModelUsage
    status: str  # "SUCCESS", "FAILED", "PARTIAL", "REJECTED"
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class AgentResearchResult:
    """Structured final output returned by the Aegis AI research agent."""

    query: str
    answer: str
    evidence_references: tuple[str, ...]
    confidence_assessment: str
    tools_invoked: tuple[ToolCallAudit, ...]
    audit_record: AgentAuditRecord
    status: str  # "SUCCESS", "FAILED", "PARTIAL", "REJECTED"
    error: str | None = None
