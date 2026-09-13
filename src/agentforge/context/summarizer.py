"""Explicit-consent model-assisted compaction for run-scoped memory."""

from __future__ import annotations

import json

from agentforge.agents.errors import AgentProtocolError
from agentforge.context.budget import budget_json_payload
from agentforge.context.memory import MemoryEntry
from agentforge.core.approval import ApprovalCapability, ApprovalCheckpoint
from agentforge.llm.base import LLMProvider
from agentforge.llm.models import ChatMessage, ToolDefinition

SUBMIT_MEMORY_SUMMARY = ToolDefinition(
    "submit_memory_summary",
    "Return a concise factual summary of older run memory.",
    {
        "type": "object",
        "properties": {"summary": {"type": "string"}},
        "required": ["summary"],
        "additionalProperties": False,
    },
)


class LLMMemorySummarizer:
    """Use a configured provider only after code-transmission approval."""

    def __init__(
        self,
        provider: LLMProvider,
        approval: ApprovalCheckpoint,
        *,
        maximum_context_characters: int = 12_000,
        maximum_summary_characters: int = 2_000,
    ) -> None:
        if maximum_context_characters < 200 or maximum_summary_characters <= 0:
            raise ValueError("Memory summarization budgets are too small")
        self._provider = provider
        self._approval = approval
        self._maximum_context_characters = maximum_context_characters
        self._maximum_summary_characters = maximum_summary_characters

    def summarize(self, entries: tuple[MemoryEntry, ...]) -> str:
        if not entries:
            raise ValueError("At least one memory entry is required")
        self._approval.require(ApprovalCapability.CODE_TRANSMISSION)
        payload = budget_json_payload(
            {
                "entries": [
                    {
                        "category": entry.category,
                        "source": entry.source,
                        "content": entry.content,
                    }
                    for entry in entries
                ]
            },
            self._maximum_context_characters,
        )
        if payload.truncated:
            raise AgentProtocolError(
                "Memory entries exceed the summarizer context budget"
            )
        response = self._provider.chat(
            (
                ChatMessage(
                    "system",
                    "Summarize only the supplied run facts. Preserve failures, decisions, "
                    "paths, and unresolved work. Do not invent facts.",
                ),
                ChatMessage("user", payload.text),
            ),
            tools=(SUBMIT_MEMORY_SUMMARY,),
            tool_choice="required",
        )
        if len(response.message.tool_calls) != 1:
            raise AgentProtocolError("Memory summarizer must submit exactly one summary")
        call = response.message.tool_calls[0]
        if call.name != SUBMIT_MEMORY_SUMMARY.name:
            raise AgentProtocolError("Memory summarizer called an unexpected tool")
        summary = call.arguments.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            raise AgentProtocolError("Memory summary must be non-empty text")
        result = summary.strip()
        if len(result) > self._maximum_summary_characters:
            raise AgentProtocolError(
                f"Memory summary exceeds {self._maximum_summary_characters} characters"
            )
        json.dumps(result, ensure_ascii=False)
        return result
