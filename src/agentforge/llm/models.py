"""Provider-neutral chat, tool-call, and usage models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, TypeAlias

MessageRole: TypeAlias = Literal["system", "user", "assistant", "tool"]
ToolChoice: TypeAlias = Literal["auto", "none", "required"]


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("Tool-call id cannot be empty")
        if not self.name.strip():
            raise ValueError("Tool-call name cannot be empty")


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: MessageRole
    content: str | None
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None

    def __post_init__(self) -> None:
        if self.role == "assistant" and self.content is None and not self.tool_calls:
            raise ValueError("Assistant message needs content or at least one tool call")
        if self.role == "tool" and not self.tool_call_id:
            raise ValueError("Tool messages need the matching tool-call id")
        if self.role != "tool" and self.tool_call_id is not None:
            raise ValueError("Only tool messages can have a tool-call id")
        if self.role != "assistant" and self.tool_calls:
            raise ValueError("Only assistant messages can contain tool calls")


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any] = field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Tool name cannot be empty")
        if self.parameters.get("type") != "object":
            raise ValueError("Tool parameter schema must describe a JSON object")


@dataclass(frozen=True, slots=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    def __post_init__(self) -> None:
        if min(self.input_tokens, self.output_tokens, self.total_tokens) < 0:
            raise ValueError("Token counts cannot be negative")


@dataclass(frozen=True, slots=True)
class ChatResponse:
    message: ChatMessage
    model: str
    finish_reason: str | None = None
    usage: TokenUsage = TokenUsage()
    request_id: str | None = None

