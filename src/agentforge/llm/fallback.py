"""Fail over between model providers only for transient infrastructure failures."""

from __future__ import annotations

import threading
from collections.abc import Sequence

from agentforge.llm.base import LLMProvider
from agentforge.llm.errors import ProviderConnectionError, ProviderRateLimitError
from agentforge.llm.models import ChatMessage, ChatResponse, ToolChoice, ToolDefinition

TransientProviderError = (ProviderConnectionError, ProviderRateLimitError)


class TransientFallbackProvider:
    """Use a secondary provider after bounded connection or rate-limit failures.

    Authentication, budget, malformed-response, and semantic failures deliberately
    propagate. Falling back for those conditions could hide a configuration error or
    turn an invalid answer into unbounded extra inference.
    """

    def __init__(
        self,
        primary: LLMProvider,
        fallback: LLMProvider,
        *,
        failure_threshold: int = 1,
        primary_enabled: bool = True,
    ) -> None:
        if failure_threshold <= 0:
            raise ValueError("Fallback failure threshold must be greater than zero")
        self._primary = primary
        self._fallback = fallback
        self._failure_threshold = failure_threshold
        self._lock = threading.Lock()
        self._consecutive_failures = 0
        self._circuit_open = not primary_enabled
        self._fallback_count = 0

    @property
    def circuit_open(self) -> bool:
        with self._lock:
            return self._circuit_open

    @property
    def fallback_count(self) -> int:
        with self._lock:
            return self._fallback_count

    def probe_primary(self, messages: Sequence[ChatMessage]) -> bool:
        """Check the preferred route and open its circuit after a transient failure."""

        if self.circuit_open:
            return False
        try:
            self._primary.chat(messages, tool_choice="none")
        except TransientProviderError:
            self._record_failure()
            return False
        self._record_success()
        return True

    def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[ToolDefinition] = (),
        tool_choice: ToolChoice = "auto",
    ) -> ChatResponse:
        if not self.circuit_open:
            try:
                response = self._primary.chat(
                    messages,
                    tools=tools,
                    tool_choice=tool_choice,
                )
            except TransientProviderError:
                self._record_failure()
            else:
                self._record_success()
                return response
        with self._lock:
            self._fallback_count += 1
        return self._fallback.chat(messages, tools=tools, tool_choice=tool_choice)

    def _record_failure(self) -> None:
        with self._lock:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self._failure_threshold:
                self._circuit_open = True

    def _record_success(self) -> None:
        with self._lock:
            self._consecutive_failures = 0
