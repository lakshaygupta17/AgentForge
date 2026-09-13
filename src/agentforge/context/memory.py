"""Bounded, run-scoped memory shared by cooperating agents."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

_WORD_PATTERN = re.compile(r"[A-Za-z0-9_]{2,}")
_TRUNCATION_MARKER = "\n[entry truncated]"


@dataclass(frozen=True, slots=True)
class MemoryEntry:
    sequence: int
    category: str
    source: str
    content: str
    priority: int


@dataclass(frozen=True, slots=True)
class MemoryView:
    text: str
    included_entries: tuple[MemoryEntry, ...]
    omitted_entries: int
    used_characters: int


class MemorySummarizer(Protocol):
    def summarize(self, entries: tuple[MemoryEntry, ...]) -> str: ...


class RunContextMemory:
    """Keep high-value observations while enforcing deterministic context limits."""

    def __init__(
        self,
        *,
        maximum_entries: int = 128,
        maximum_entry_characters: int = 8_000,
        default_view_characters: int = 12_000,
    ) -> None:
        if maximum_entries <= 0:
            raise ValueError("Maximum memory entries must be greater than zero")
        if maximum_entry_characters < len(_TRUNCATION_MARKER) + 1:
            raise ValueError("Maximum memory entry size is too small")
        if default_view_characters <= 0:
            raise ValueError("Default memory view size must be greater than zero")
        self.maximum_entries = maximum_entries
        self.maximum_entry_characters = maximum_entry_characters
        self.default_view_characters = default_view_characters
        self._entries: list[MemoryEntry] = []
        self._next_sequence = 1

    @property
    def entries(self) -> tuple[MemoryEntry, ...]:
        return tuple(self._entries)

    def remember(
        self,
        category: str,
        source: str,
        content: str,
        *,
        priority: int = 50,
    ) -> MemoryEntry:
        category = category.strip()
        source = source.strip()
        content = content.strip()
        if not category or not source or not content:
            raise ValueError("Memory category, source, and content must be non-empty")
        if not 0 <= priority <= 100:
            raise ValueError("Memory priority must be between 0 and 100")
        if len(content) > self.maximum_entry_characters:
            kept = self.maximum_entry_characters - len(_TRUNCATION_MARKER)
            content = content[:kept] + _TRUNCATION_MARKER

        duplicate = next(
            (
                entry
                for entry in self._entries
                if entry.category == category
                and entry.source == source
                and entry.content == content
            ),
            None,
        )
        if duplicate is not None:
            return duplicate

        entry = MemoryEntry(
            self._next_sequence, category, source, content, priority
        )
        self._next_sequence += 1
        self._entries.append(entry)
        if len(self._entries) > self.maximum_entries:
            victim = min(self._entries, key=lambda item: (item.priority, item.sequence))
            self._entries.remove(victim)
        return entry

    def render(
        self, query: str = "", *, maximum_characters: int | None = None
    ) -> MemoryView:
        budget = (
            self.default_view_characters
            if maximum_characters is None
            else maximum_characters
        )
        if budget <= 0:
            raise ValueError("Memory view budget must be greater than zero")
        query_words = self._words(query)
        ranked = sorted(
            self._entries,
            key=lambda entry: (
                -(entry.priority + 10 * len(query_words & self._words(entry.content))),
                -entry.sequence,
            ),
        )
        fragments: list[str] = []
        included: list[MemoryEntry] = []
        used = 0
        for entry in ranked:
            fragment = f"[{entry.category}:{entry.source}]\n{entry.content}"
            separator = 2 if fragments else 0
            remaining = budget - used - separator
            if remaining <= 0:
                break
            if len(fragment) > remaining:
                if not fragments:
                    fragment = fragment[:remaining]
                else:
                    continue
            fragments.append(fragment)
            included.append(entry)
            used += separator + len(fragment)
        return MemoryView(
            "\n\n".join(fragments),
            tuple(included),
            len(self._entries) - len(included),
            used,
        )

    def compact(
        self,
        summarizer: MemorySummarizer,
        *,
        keep_recent_entries: int = 16,
    ) -> MemoryEntry | None:
        """Replace older entries with one bounded summary while retaining recent facts."""

        if keep_recent_entries < 0:
            raise ValueError("Recent memory count cannot be negative")
        if len(self._entries) <= keep_recent_entries + 1:
            return None
        split = len(self._entries) - keep_recent_entries
        selected = tuple(self._entries[:split])
        summary = summarizer.summarize(selected).strip()
        if not summary:
            raise ValueError("Memory summarizer returned empty text")
        self._entries = self._entries[split:]
        return self.remember(
            "compacted_summary",
            "memory_summarizer",
            summary,
            priority=max(entry.priority for entry in selected),
        )

    @staticmethod
    def _words(value: str) -> set[str]:
        return {match.group(0).casefold() for match in _WORD_PATTERN.finditer(value)}
