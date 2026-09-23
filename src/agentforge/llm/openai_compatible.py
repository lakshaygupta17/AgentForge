"""Dependency-free adapter for OpenAI-compatible chat-completion APIs."""

from __future__ import annotations

import json
import time
import urllib.error
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from agentforge.llm.errors import (
    ProviderAuthenticationError,
    ProviderConnectionError,
    ProviderRateLimitError,
    ProviderResponseError,
)
from agentforge.llm.models import (
    ChatMessage,
    ChatResponse,
    TokenUsage,
    ToolCall,
    ToolChoice,
    ToolDefinition,
)
from agentforge.llm.transport import HttpResponse, HttpTransport, UrllibHttpTransport
from agentforge.telemetry.trajectory import redact_sensitive_text


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    maximum_attempts: int = 3
    initial_delay_seconds: float = 0.5
    maximum_delay_seconds: float = 4.0

    def __post_init__(self) -> None:
        if self.maximum_attempts <= 0:
            raise ValueError("Maximum attempts must be greater than zero")
        if self.initial_delay_seconds < 0 or self.maximum_delay_seconds < 0:
            raise ValueError("Retry delays cannot be negative")


class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 60.0,
        maximum_output_tokens: int | None = None,
        retry_policy: RetryPolicy | None = None,
        transport: HttpTransport | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not base_url.startswith("https://"):
            raise ValueError("Model-provider base URL must use HTTPS")
        if not api_key.strip():
            raise ValueError("Model-provider API key cannot be empty")
        if not model.strip():
            raise ValueError("Model name cannot be empty")
        if timeout_seconds <= 0:
            raise ValueError("Provider timeout must be greater than zero")
        if maximum_output_tokens is not None and maximum_output_tokens <= 0:
            raise ValueError("Maximum output tokens must be greater than zero")

        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.maximum_output_tokens = maximum_output_tokens
        self.retry_policy = retry_policy or RetryPolicy()
        self._api_key = api_key
        self._transport = transport or UrllibHttpTransport()
        self._sleeper = sleeper

    def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[ToolDefinition] = (),
        tool_choice: ToolChoice = "auto",
    ) -> ChatResponse:
        if not messages:
            raise ValueError("At least one chat message is required")
        if tool_choice == "required" and not tools:
            raise ValueError("Required tool choice needs at least one tool definition")

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [self._message_payload(message) for message in messages],
            "stream": False,
        }
        if self.maximum_output_tokens is not None:
            payload["max_tokens"] = self.maximum_output_tokens
        if tools:
            payload["tools"] = [self._tool_payload(tool) for tool in tools]
            payload["tool_choice"] = tool_choice

        encoded_payload = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        for response_attempt in range(2):
            response = self._post_with_retries(encoded_payload)
            try:
                return self._parse_response(response)
            except ProviderResponseError:
                if response_attempt == 1:
                    raise
                self._sleep(self.retry_policy.initial_delay_seconds)
        raise AssertionError("response retry loop ended unexpectedly")

    def _post_with_retries(self, body: bytes) -> HttpResponse:
        delay = self.retry_policy.initial_delay_seconds
        last_connection_error: Exception | None = None

        for attempt in range(1, self.retry_policy.maximum_attempts + 1):
            try:
                response = self._transport.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                        "User-Agent": "AgentForge/0.1",
                    },
                    body=body,
                    timeout_seconds=self.timeout_seconds,
                )
            except (OSError, TimeoutError, urllib.error.URLError) as error:
                last_connection_error = error
                if attempt == self.retry_policy.maximum_attempts:
                    break
                self._sleep(delay)
                delay = min(delay * 2 or 0, self.retry_policy.maximum_delay_seconds)
                continue

            if response.status_code in {401, 403}:
                raise ProviderAuthenticationError(self._error_message(response))
            if response.status_code == 429:
                if attempt == self.retry_policy.maximum_attempts:
                    raise ProviderRateLimitError(self._error_message(response))
                self._sleep(delay)
                delay = min(delay * 2 or 0, self.retry_policy.maximum_delay_seconds)
                continue
            if response.status_code >= 500:
                if attempt == self.retry_policy.maximum_attempts:
                    raise ProviderConnectionError(self._error_message(response))
                self._sleep(delay)
                delay = min(delay * 2 or 0, self.retry_policy.maximum_delay_seconds)
                continue
            if response.status_code >= 400:
                raise ProviderResponseError(self._error_message(response))
            return response

        raise ProviderConnectionError(
            redact_sensitive_text(f"Model provider could not be reached: {last_connection_error}")
        ) from last_connection_error

    def _parse_response(self, response: HttpResponse) -> ChatResponse:
        try:
            document = json.loads(response.body)
            choice = document["choices"][0]
            raw_message = choice["message"]
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError) as error:
            raise ProviderResponseError("Provider returned malformed chat JSON") from error

        tool_calls: list[ToolCall] = []
        try:
            for raw_call in raw_message.get("tool_calls") or []:
                raw_arguments = raw_call["function"]["arguments"]
                arguments = (
                    json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
                )
                if not isinstance(arguments, dict):
                    raise TypeError("Tool arguments must be an object")
                tool_calls.append(
                    ToolCall(
                        id=raw_call["id"],
                        name=raw_call["function"]["name"],
                        arguments=arguments,
                    )
                )
        except (KeyError, TypeError, json.JSONDecodeError, ValueError) as error:
            raise ProviderResponseError("Provider returned a malformed tool call") from error

        usage_document = document.get("usage") or {}
        input_tokens = self._integer(usage_document.get("prompt_tokens", 0))
        output_tokens = self._integer(usage_document.get("completion_tokens", 0))
        total_tokens = self._integer(usage_document.get("total_tokens", 0))
        usage = TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens or input_tokens + output_tokens,
        )
        try:
            message = ChatMessage(
                role="assistant",
                content=raw_message.get("content"),
                tool_calls=tuple(tool_calls),
            )
        except ValueError as error:
            raise ProviderResponseError(str(error)) from error

        return ChatResponse(
            message=message,
            model=str(document.get("model") or self.model),
            finish_reason=choice.get("finish_reason"),
            usage=usage,
            request_id=str(document["id"]) if document.get("id") is not None else None,
        )

    def _message_payload(self, message: ChatMessage) -> dict[str, Any]:
        payload: dict[str, Any] = {"role": message.role, "content": message.content}
        if message.tool_call_id is not None:
            payload["tool_call_id"] = message.tool_call_id
        if message.tool_calls:
            payload["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments, separators=(",", ":")),
                    },
                }
                for call in message.tool_calls
            ]
        return payload

    def _tool_payload(self, tool: ToolDefinition) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }

    def _error_message(self, response: HttpResponse) -> str:
        try:
            document = json.loads(response.body)
            error = document.get("error", document)
            if isinstance(error, dict) and error.get("message"):
                return redact_sensitive_text(
                    f"Provider returned HTTP {response.status_code}: {error['message']}"
                )
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
            pass
        return f"Provider returned HTTP {response.status_code}"

    def _integer(self, value: object) -> int:
        return value if isinstance(value, int) and not isinstance(value, bool) else 0

    def _sleep(self, delay: float) -> None:
        if delay > 0:
            self._sleeper(delay)
