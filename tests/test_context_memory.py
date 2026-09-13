from unittest import TestCase

from agentforge.context.budget import budget_json_payload
from agentforge.context.memory import RunContextMemory


class RunContextMemoryTests(TestCase):
    def test_ranks_relevant_high_priority_entries_within_a_hard_budget(self) -> None:
        memory = RunContextMemory(maximum_entry_characters=200)
        memory.remember("note", "old", "unrelated deployment detail", priority=20)
        relevant = memory.remember(
            "finding", "explorer", "checkout fails for an empty cart", priority=80
        )
        memory.remember("note", "new", "another unrelated observation", priority=30)

        view = memory.render("repair checkout cart", maximum_characters=80)

        self.assertEqual(view.included_entries[0], relevant)
        self.assertIn("checkout fails", view.text)
        self.assertLessEqual(view.used_characters, 80)
        self.assertGreater(view.omitted_entries, 0)

    def test_truncates_entries_and_evicts_the_oldest_low_priority_item(self) -> None:
        memory = RunContextMemory(maximum_entries=2, maximum_entry_characters=40)
        memory.remember("note", "first", "a" * 100, priority=10)
        kept = memory.remember("note", "second", "important", priority=90)
        newest = memory.remember("note", "third", "recent", priority=10)

        self.assertEqual(memory.entries, (kept, newest))

    def test_deduplicates_identical_observations(self) -> None:
        memory = RunContextMemory()
        first = memory.remember("tool", "search", "same result")
        second = memory.remember("tool", "search", "same result")

        self.assertEqual(first, second)
        self.assertEqual(len(memory.entries), 1)


class ContextBudgetTests(TestCase):
    def test_json_payload_never_exceeds_its_budget(self) -> None:
        result = budget_json_payload(
            {"ok": True, "result": "repository text " * 500}, 300
        )

        self.assertTrue(result.truncated)
        self.assertLessEqual(len(result.text), 300)
        self.assertGreater(result.original_characters, len(result.text))
