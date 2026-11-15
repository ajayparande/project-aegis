"""Provider-agnostic LLM interface with Mock, Gemini, OpenAI, and Anthropic support."""

from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from aegis.ai_agent.models import (
    ChatMessage,
    LLMResponse,
    ModelUsage,
    ProviderError,
    ProviderTimeoutError,
    Role,
    ToolCall,
    ToolDefinition,
)
from aegis.ai_agent.security import redact_secrets


def _validate_provider_limits(timeout_seconds: float, max_output_tokens: int) -> None:
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(timeout_seconds)
        or not 0 < timeout_seconds <= 120
    ):
        raise ValueError("timeout_seconds must be between 0 and 120 seconds.")
    if (
        isinstance(max_output_tokens, bool)
        or not isinstance(max_output_tokens, int)
        or not 0 < max_output_tokens <= 8192
    ):
        raise ValueError("max_output_tokens must be between 1 and 8192.")


_MAX_PROVIDER_RESPONSE_BYTES = 1_000_000


def _decode_response(data: bytes, provider_name: str) -> dict[str, Any]:
    try:
        decoded = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProviderError(f"{provider_name} returned invalid JSON.") from exc
    if not isinstance(decoded, dict):
        raise ProviderError(f"{provider_name} returned a malformed response.")
    return decoded


def _read_response(response: Any, provider_name: str) -> dict[str, Any]:
    data = response.read(_MAX_PROVIDER_RESPONSE_BYTES + 1)
    if len(data) > _MAX_PROVIDER_RESPONSE_BYTES:
        raise ProviderError(f"{provider_name} response exceeded the size limit.")
    return _decode_response(data, provider_name)


def _require_mapping(value: Any, provider_name: str, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProviderError(f"{provider_name} returned a malformed {field}.")
    return value


def _require_sequence(value: Any, provider_name: str, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise ProviderError(f"{provider_name} returned a malformed {field}.")
    return value


def _validated_usage(
    usage_data: Any,
    provider_name: str,
    prompt_field: str,
    completion_field: str,
    total_field: str | None = None,
) -> ModelUsage:
    usage = _require_mapping(usage_data, provider_name, "usage")
    if prompt_field not in usage or completion_field not in usage:
        raise ProviderError(f"{provider_name} omitted token usage metrics.")
    if total_field is not None and total_field not in usage:
        raise ProviderError(f"{provider_name} omitted total token usage.")
    prompt = usage.get(prompt_field, 0)
    completion = usage.get(completion_field, 0)
    total = usage.get(total_field, prompt + completion) if total_field else prompt + completion
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in (prompt, completion, total)
    ):
        raise ProviderError(f"{provider_name} returned invalid token usage.")
    if total < prompt + completion:
        raise ProviderError(f"{provider_name} returned inconsistent token usage.")
    return ModelUsage(prompt_tokens=prompt, completion_tokens=completion, total_tokens=total)


class LLMProvider(ABC):
    """Abstract base class for all LLM providers in Project Aegis."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name (e.g. 'mock', 'gemini', 'openai', 'anthropic')."""

    @property
    @abstractmethod
    def model_id(self) -> str:
        """Specific model identifier being queried."""

    @abstractmethod
    def generate(
        self,
        messages: list[ChatMessage],
        tools: list[ToolDefinition] | None = None,
    ) -> LLMResponse:
        """Generate a response given conversation history and optional tool definitions."""


class MockLLMProvider(LLMProvider):
    """Deterministic, zero-cost, network-free mock LLM provider for testing and offline research."""

    def __init__(
        self,
        model_id: str = "mock-aegis-research-v1",
        scripted_responses: list[LLMResponse | Exception] | None = None,
        generator_fn: Callable[[list[ChatMessage], list[ToolDefinition] | None], LLMResponse]
        | None = None,
    ) -> None:
        self._model_id = model_id
        self._scripted_responses: list[LLMResponse | Exception] = list(scripted_responses or [])
        self._generator_fn = generator_fn
        self.call_history: list[dict[str, Any]] = []

    @property
    def name(self) -> str:
        return "mock"

    @property
    def model_id(self) -> str:
        return self._model_id

    def add_response(self, response: LLMResponse | Exception) -> None:
        """Queue a scripted response or exception."""
        self._scripted_responses.append(response)

    def generate(
        self,
        messages: list[ChatMessage],
        tools: list[ToolDefinition] | None = None,
    ) -> LLMResponse:
        self.call_history.append({"messages": messages, "tools": tools})

        if self._scripted_responses:
            next_item = self._scripted_responses.pop(0)
            if isinstance(next_item, Exception):
                raise next_item
            return next_item

        if self._generator_fn is not None:
            return self._generator_fn(messages, tools)

        # Default intelligent response if no scripted responses provided
        last_message = messages[-1] if messages else None
        last_content = last_message.content if last_message else "No query provided."

        default_text = (
            f"Aegis Research Summary (Mock Provider): Processed query '{last_content[:80]}'. "
            "All quantitative indicators and market datasets were verified through deterministic Python modules."
        )

        return LLMResponse(
            message=ChatMessage(role=Role.ASSISTANT, content=default_text),
            usage=ModelUsage(prompt_tokens=50, completion_tokens=30, total_tokens=80),
            finish_reason="stop",
        )


class GeminiProvider(LLMProvider):
    """Google Gemini LLM provider using direct REST API via standard library."""

    def __init__(
        self,
        api_key: str | None = None,
        model_id: str = "gemini-2.0-flash",
        timeout_seconds: float = 30.0,
        max_output_tokens: int = 1024,
    ) -> None:
        _validate_provider_limits(timeout_seconds, max_output_tokens)
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        self._model_id = model_id
        self._timeout_seconds = timeout_seconds
        self._max_output_tokens = max_output_tokens

    @property
    def name(self) -> str:
        return "gemini"

    @property
    def model_id(self) -> str:
        return self._model_id

    def generate(
        self,
        messages: list[ChatMessage],
        tools: list[ToolDefinition] | None = None,
    ) -> LLMResponse:
        if not self._api_key:
            raise ProviderError(
                "Gemini API key is not configured. Set GEMINI_API_KEY environment variable."
            )

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self._model_id}:generateContent?key={self._api_key}"

        # Convert chat messages to Gemini contents format
        contents: list[dict[str, Any]] = []
        system_instruction: dict[str, Any] | None = None

        for msg in messages:
            if msg.role == Role.SYSTEM:
                system_instruction = {"parts": [{"text": msg.content}]}
            elif msg.role == Role.USER:
                contents.append({"role": "user", "parts": [{"text": msg.content}]})
            elif msg.role == Role.ASSISTANT:
                parts: list[dict[str, Any]] = []
                if msg.content:
                    parts.append({"text": msg.content})
                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        parts.append({"functionCall": {"name": tc.name, "args": tc.arguments}})
                contents.append({"role": "model", "parts": parts})
            elif msg.role == Role.TOOL:
                parts = [
                    {
                        "functionResponse": {
                            "name": msg.name or "tool_result",
                            "response": {"output": msg.content},
                        }
                    }
                ]
                contents.append({"role": "user", "parts": parts})

        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {"maxOutputTokens": self._max_output_tokens},
        }
        if system_instruction:
            payload["systemInstruction"] = system_instruction

        if tools:
            gemini_tools = [
                {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.to_dict()["parameters"],
                }
                for t in tools
            ]
            payload["tools"] = [{"functionDeclarations": gemini_tools}]

        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data_bytes,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self._timeout_seconds) as resp:
                resp_data = _read_response(resp, self.name)
        except TimeoutError as err:
            raise ProviderTimeoutError(
                f"Gemini API timed out after {self._timeout_seconds}s"
            ) from err
        except urllib.error.URLError as err:
            clean_err = redact_secrets(str(err))
            raise ProviderError(f"Gemini API request failed: {clean_err}") from err

        # Parse response
        candidates = _require_sequence(resp_data.get("candidates"), self.name, "candidates")
        if not candidates:
            raise ProviderError("Gemini returned empty candidate list.")

        first_cand = _require_mapping(candidates[0], self.name, "candidate")
        content_obj = _require_mapping(first_cand.get("content"), self.name, "content")
        parts = _require_sequence(content_obj.get("parts"), self.name, "content parts")

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for idx, part in enumerate(parts):
            part = _require_mapping(part, self.name, "content part")
            if "text" in part:
                if not isinstance(part["text"], str):
                    raise ProviderError("Gemini returned malformed text content.")
                text_parts.append(part["text"])
            if "functionCall" in part:
                fn_call = _require_mapping(part["functionCall"], self.name, "function call")
                name = fn_call.get("name")
                arguments = fn_call.get("args")
                if not isinstance(name, str) or not name or not isinstance(arguments, dict):
                    raise ProviderError("Gemini returned malformed tool-call arguments.")
                tool_calls.append(
                    ToolCall(
                        call_id=f"call_gemini_{idx}",
                        name=name,
                        arguments=arguments,
                    )
                )

        usage = _validated_usage(
            resp_data.get("usageMetadata", {}),
            self.name,
            "promptTokenCount",
            "candidatesTokenCount",
            "totalTokenCount",
        )

        return LLMResponse(
            message=ChatMessage(
                role=Role.ASSISTANT,
                content="\n".join(text_parts),
                tool_calls=tuple(tool_calls) if tool_calls else None,
            ),
            usage=usage,
            finish_reason=first_cand.get("finishReason", "stop"),
        )


class OpenAIProvider(LLMProvider):
    """OpenAI LLM provider using direct REST API via standard library."""

    def __init__(
        self,
        api_key: str | None = None,
        model_id: str = "gpt-4o",
        timeout_seconds: float = 30.0,
        max_output_tokens: int = 1024,
    ) -> None:
        _validate_provider_limits(timeout_seconds, max_output_tokens)
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._model_id = model_id
        self._timeout_seconds = timeout_seconds
        self._max_output_tokens = max_output_tokens

    @property
    def name(self) -> str:
        return "openai"

    @property
    def model_id(self) -> str:
        return self._model_id

    def generate(
        self,
        messages: list[ChatMessage],
        tools: list[ToolDefinition] | None = None,
    ) -> LLMResponse:
        if not self._api_key:
            raise ProviderError(
                "OpenAI API key is not configured. Set OPENAI_API_KEY environment variable."
            )

        url = "https://api.openai.com/v1/chat/completions"

        openai_messages: list[dict[str, Any]] = []
        for msg in messages:
            msg_dict: dict[str, Any] = {"role": msg.role.value, "content": msg.content}
            if msg.role == Role.ASSISTANT and msg.tool_calls:
                msg_dict["tool_calls"] = [
                    {
                        "id": tc.call_id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                    }
                    for tc in msg.tool_calls
                ]
            if msg.role == Role.TOOL:
                msg_dict["tool_call_id"] = msg.tool_call_id or ""
            openai_messages.append(msg_dict)

        payload: dict[str, Any] = {
            "model": self._model_id,
            "messages": openai_messages,
            "max_tokens": self._max_output_tokens,
        }

        if tools:
            payload["tools"] = [{"type": "function", "function": t.to_dict()} for t in tools]

        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data_bytes,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self._timeout_seconds) as resp:
                resp_data = _read_response(resp, self.name)
        except TimeoutError as err:
            raise ProviderTimeoutError(
                f"OpenAI API timed out after {self._timeout_seconds}s"
            ) from err
        except urllib.error.URLError as err:
            clean_err = redact_secrets(str(err))
            raise ProviderError(f"OpenAI API request failed: {clean_err}") from err

        choices = _require_sequence(resp_data.get("choices"), self.name, "choices")
        if not choices:
            raise ProviderError("OpenAI returned no choices.")

        first_choice = _require_mapping(choices[0], self.name, "choice")
        msg_obj = _require_mapping(first_choice.get("message"), self.name, "message")
        content = msg_obj.get("content")
        if content is not None and not isinstance(content, str):
            raise ProviderError("OpenAI returned malformed message content.")
        tool_calls_data = _require_sequence(msg_obj.get("tool_calls", []), self.name, "tool calls")

        tool_calls: list[ToolCall] = []
        for tc in tool_calls_data:
            tc = _require_mapping(tc, self.name, "tool call")
            fn = _require_mapping(tc.get("function"), self.name, "tool function")
            raw_args = fn.get("arguments", "{}")
            name = fn.get("name")
            call_id = tc.get("id")
            if not isinstance(name, str) or not name or not isinstance(call_id, str) or not call_id:
                raise ProviderError("OpenAI returned malformed tool-call identity.")
            if not isinstance(raw_args, str):
                raise ProviderError("OpenAI returned malformed tool-call arguments.")
            try:
                parsed_args = json.loads(raw_args)
            except json.JSONDecodeError:
                raise ProviderError("OpenAI returned invalid tool-call JSON.") from None
            if not isinstance(parsed_args, dict):
                raise ProviderError("OpenAI tool-call arguments must be an object.")
            tool_calls.append(
                ToolCall(
                    call_id=call_id,
                    name=name,
                    arguments=parsed_args,
                )
            )

        usage = _validated_usage(
            resp_data.get("usage", {}),
            self.name,
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
        )

        return LLMResponse(
            message=ChatMessage(
                role=Role.ASSISTANT,
                content=msg_obj.get("content") or "",
                tool_calls=tuple(tool_calls) if tool_calls else None,
            ),
            usage=usage,
            finish_reason=first_choice.get("finish_reason", "stop"),
        )


class AnthropicProvider(LLMProvider):
    """Anthropic Claude LLM provider using direct REST API via standard library."""

    def __init__(
        self,
        api_key: str | None = None,
        model_id: str = "claude-3-5-sonnet-20241022",
        timeout_seconds: float = 30.0,
        max_output_tokens: int = 1024,
    ) -> None:
        _validate_provider_limits(timeout_seconds, max_output_tokens)
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self._model_id = model_id
        self._timeout_seconds = timeout_seconds
        self._max_output_tokens = max_output_tokens

    @property
    def name(self) -> str:
        return "anthropic"

    @property
    def model_id(self) -> str:
        return self._model_id

    def generate(
        self,
        messages: list[ChatMessage],
        tools: list[ToolDefinition] | None = None,
    ) -> LLMResponse:
        if not self._api_key:
            raise ProviderError(
                "Anthropic API key is not configured. Set ANTHROPIC_API_KEY environment variable."
            )

        url = "https://api.anthropic.com/v1/messages"

        system_prompt = ""
        claude_messages: list[dict[str, Any]] = []

        for msg in messages:
            if msg.role == Role.SYSTEM:
                system_prompt = msg.content
            elif msg.role == Role.USER:
                claude_messages.append({"role": "user", "content": msg.content})
            elif msg.role == Role.ASSISTANT:
                content_blocks: list[dict[str, Any]] = []
                if msg.content:
                    content_blocks.append({"type": "text", "text": msg.content})
                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        content_blocks.append(
                            {
                                "type": "tool_use",
                                "id": tc.call_id,
                                "name": tc.name,
                                "input": tc.arguments,
                            }
                        )
                claude_messages.append({"role": "assistant", "content": content_blocks})
            elif msg.role == Role.TOOL:
                claude_messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": msg.tool_call_id or "",
                                "content": msg.content,
                            }
                        ],
                    }
                )

        payload: dict[str, Any] = {
            "model": self._model_id,
            "max_tokens": self._max_output_tokens,
            "messages": claude_messages,
        }
        if system_prompt:
            payload["system"] = system_prompt

        if tools:
            payload["tools"] = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.to_dict()["parameters"],
                }
                for t in tools
            ]

        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data_bytes,
            headers={
                "Content-Type": "application/json",
                "x-api-key": self._api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self._timeout_seconds) as resp:
                resp_data = _read_response(resp, self.name)
        except TimeoutError as err:
            raise ProviderTimeoutError(
                f"Anthropic API timed out after {self._timeout_seconds}s"
            ) from err
        except urllib.error.URLError as err:
            clean_err = redact_secrets(str(err))
            raise ProviderError(f"Anthropic API request failed: {clean_err}") from err

        content_blocks = _require_sequence(resp_data.get("content"), self.name, "content")
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in content_blocks:
            block = _require_mapping(block, self.name, "content block")
            if block.get("type") == "text":
                text = block.get("text")
                if not isinstance(text, str):
                    raise ProviderError("Anthropic returned malformed text content.")
                text_parts.append(text)
            elif block.get("type") == "tool_use":
                call_id = block.get("id")
                name = block.get("name")
                arguments = block.get("input")
                if (
                    not isinstance(call_id, str)
                    or not call_id
                    or not isinstance(name, str)
                    or not name
                    or not isinstance(arguments, dict)
                ):
                    raise ProviderError("Anthropic returned malformed tool-call data.")
                tool_calls.append(
                    ToolCall(
                        call_id=call_id,
                        name=name,
                        arguments=arguments,
                    )
                )

        usage = _validated_usage(
            resp_data.get("usage", {}),
            self.name,
            "input_tokens",
            "output_tokens",
        )

        return LLMResponse(
            message=ChatMessage(
                role=Role.ASSISTANT,
                content="\n".join(text_parts),
                tool_calls=tuple(tool_calls) if tool_calls else None,
            ),
            usage=usage,
            finish_reason=resp_data.get("stop_reason", "end_turn"),
        )
