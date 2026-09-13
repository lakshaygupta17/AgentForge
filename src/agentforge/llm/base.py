"""The provider interface consumed by agents and orchestration."""

from collections.abc import Sequence
from typing import Protocol

from agentforge.llm.models import ChatMessage, ChatResponse, ToolChoice, ToolDefinition


class LLMProvider(Protocol):
    def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[ToolDefinition] = (),
        tool_choice: ToolChoice = "auto",
    ) -> ChatResponse:
        """Return one assistant turn, optionally containing structured tool calls."""
        ...

