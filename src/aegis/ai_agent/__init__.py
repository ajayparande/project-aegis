"""Aegis AI Agent and Controlled Tool Framework.

Provides provider-agnostic LLM reasoning, strictly permissioned read-only tools,
prompt-injection protection, and structured audit logs while ensuring deterministic
Python components remain authoritative for all financial, risk, and trading logic.
"""

from __future__ import annotations

from aegis.ai_agent.models import (
    AgentAuditRecord,
    AgentError,
    AgentResearchResult,
    ChatMessage,
    LLMResponse,
    ModelUsage,
    ProviderError,
    ProviderTimeoutError,
    Role,
    SecurityViolationError,
    ToolCall,
    ToolCallAudit,
    ToolDefinition,
    ToolExecutionError,
    ToolParameter,
    ToolPermission,
    ToolResult,
)
from aegis.ai_agent.orchestrator import AIAgent
from aegis.ai_agent.providers import (
    AnthropicProvider,
    GeminiProvider,
    LLMProvider,
    MockLLMProvider,
    OpenAIProvider,
)
from aegis.ai_agent.security import (
    enforce_tool_permission,
    redact_nested_secrets,
    redact_secrets,
    tag_untrusted_data,
    validate_query_bounds,
)
from aegis.ai_agent.tools import ToolRegistry, create_default_tool_registry

__all__ = [
    "AIAgent",
    "AgentAuditRecord",
    "AgentError",
    "AgentResearchResult",
    "AnthropicProvider",
    "ChatMessage",
    "GeminiProvider",
    "LLMProvider",
    "LLMResponse",
    "MockLLMProvider",
    "ModelUsage",
    "OpenAIProvider",
    "ProviderError",
    "ProviderTimeoutError",
    "Role",
    "SecurityViolationError",
    "ToolCall",
    "ToolCallAudit",
    "ToolDefinition",
    "ToolExecutionError",
    "ToolParameter",
    "ToolPermission",
    "ToolRegistry",
    "ToolResult",
    "create_default_tool_registry",
    "enforce_tool_permission",
    "redact_nested_secrets",
    "redact_secrets",
    "tag_untrusted_data",
    "validate_query_bounds",
]
