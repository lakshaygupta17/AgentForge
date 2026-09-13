from unittest import TestCase

from agentforge.agents.errors import AgentProtocolError
from agentforge.context.memory import RunContextMemory
from agentforge.context.summarizer import LLMMemorySummarizer
from agentforge.core.approval import ApprovalCheckpoint, ApprovalRequiredError
from agentforge.llm.mock import MockLLMProvider
from agentforge.llm.models import ChatMessage, ChatResponse, ToolCall


def summary_response(summary: str) -> ChatResponse:
    return ChatResponse(
        ChatMessage(
            "assistant",
            None,
            tool_calls=(
                ToolCall("summary", "submit_memory_summary", {"summary": summary}),
            ),
        ),
        "mock-summarizer",
    )


class MemorySummarizerTests(TestCase):
    @staticmethod
    def _memory() -> RunContextMemory:
        memory = RunContextMemory(maximum_entries=10)
        memory.remember("plan", "planner", "Inspect the parser", priority=80)
        memory.remember("finding", "explorer", "Parser fails on empty input", priority=90)
        memory.remember("test", "tester", "Empty input test failed", priority=95)
        memory.remember("recent", "developer", "Preparing a focused patch", priority=70)
        return memory

    def test_compacts_old_entries_and_preserves_recent_entry(self) -> None:
        memory = self._memory()
        provider = MockLLMProvider(
            [summary_response("The parser and its empty-input failure need repair.")]
        )
        summarizer = LLMMemorySummarizer(
            provider, ApprovalCheckpoint(code_transmission=True)
        )

        summary = memory.compact(summarizer, keep_recent_entries=1)

        self.assertIsNotNone(summary)
        self.assertEqual(len(memory.entries), 2)
        self.assertEqual(memory.entries[0].category, "recent")
        self.assertEqual(memory.entries[1].category, "compacted_summary")
        self.assertIn("empty-input failure", memory.entries[1].content)
        self.assertEqual(len(provider.requests), 1)

    def test_missing_transmission_approval_leaves_memory_unchanged(self) -> None:
        memory = self._memory()
        provider = MockLLMProvider([summary_response("unused")])
        summarizer = LLMMemorySummarizer(provider, ApprovalCheckpoint())

        with self.assertRaises(ApprovalRequiredError):
            memory.compact(summarizer, keep_recent_entries=1)

        self.assertEqual(len(memory.entries), 4)
        self.assertEqual(provider.requests, [])

    def test_rejects_oversized_model_summary_without_deleting_entries(self) -> None:
        memory = self._memory()
        summarizer = LLMMemorySummarizer(
            MockLLMProvider([summary_response("too long")]),
            ApprovalCheckpoint(code_transmission=True),
            maximum_summary_characters=3,
        )

        with self.assertRaisesRegex(AgentProtocolError, "exceeds"):
            memory.compact(summarizer, keep_recent_entries=1)

        self.assertEqual(len(memory.entries), 4)
