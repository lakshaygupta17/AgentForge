"""Deterministic provider for tests, demos, and agent development."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from agentforge.llm.models import (
    ChatMessage,
    ChatResponse,
    ToolChoice,
    ToolDefinition,
)


@dataclass(frozen=True, slots=True)
class RecordedChatRequest:
    messages: tuple[ChatMessage, ...]
    tools: tuple[ToolDefinition, ...]
    tool_choice: ToolChoice


class MockLLMProvider:
    def __init__(self, responses: Iterable[ChatResponse]) -> None:
        self._responses = deque(responses)
        self.requests: list[RecordedChatRequest] = []

    def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[ToolDefinition] = (),
        tool_choice: ToolChoice = "auto",
    ) -> ChatResponse:
        self.requests.append(
            RecordedChatRequest(tuple(messages), tuple(tools), tool_choice)
        )
        if not self._responses:
            raise RuntimeError("MockLLMProvider has no responses remaining")
        return self._responses.popleft()

