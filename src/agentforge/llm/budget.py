"""Thread-safe, provider-neutral limits for paid model inference."""

from __future__ import annotations

import json
import threading
from collections.abc import Sequence
from dataclasses import dataclass

from agentforge.llm.base import LLMProvider
from agentforge.llm.errors import ProviderBudgetExceededError
from agentforge.llm.models import ChatMessage, ChatResponse, ToolChoice, ToolDefinition


@dataclass(frozen=True, slots=True)
class ModelPricing:
    input_per_million_usd: float
    output_per_million_usd: float

    def __post_init__(self) -> None:
        if self.input_per_million_usd < 0 or self.output_per_million_usd < 0:
            raise ValueError("Model prices cannot be negative")


@dataclass(frozen=True, slots=True)
class InferenceBudgetSnapshot:
    requests: int
    tokens: int
    estimated_cost_usd: float
    exhausted: bool


@dataclass(frozen=True, slots=True)
class _Reservation:
    tokens: int
    cost_usd: float


class SharedInferenceBudget:
    """Reserve conservative capacity before requests and settle actual usage after."""

    def __init__(
        self,
        *,
        maximum_requests: int,
        maximum_tokens: int,
        maximum_cost_usd: float,
    ) -> None:
        if maximum_requests <= 0 or maximum_tokens <= 0 or maximum_cost_usd <= 0:
            raise ValueError("Inference budget limits must be greater than zero")
        self.maximum_requests = maximum_requests
        self.maximum_tokens = maximum_tokens
        self.maximum_cost_usd = maximum_cost_usd
        self._lock = threading.Lock()
        self._requests = 0
        self._tokens = 0
        self._cost_usd = 0.0
        self._reserved_tokens = 0
        self._reserved_cost_usd = 0.0
        self._blocked = False

    def reserve(
        self,
        *,
        maximum_input_tokens: int,
        maximum_output_tokens: int,
        pricing: ModelPricing,
    ) -> _Reservation:
        tokens = maximum_input_tokens + maximum_output_tokens
        cost = (
            maximum_input_tokens * pricing.input_per_million_usd
            + maximum_output_tokens * pricing.output_per_million_usd
        ) / 1_000_000
        with self._lock:
            reason: str | None = None
            if self._requests + 1 > self.maximum_requests:
                reason = f"request limit {self.maximum_requests} reached"
            elif self._tokens + self._reserved_tokens + tokens > self.maximum_tokens:
                reason = f"token limit {self.maximum_tokens} would be exceeded"
            elif self._cost_usd + self._reserved_cost_usd + cost > self.maximum_cost_usd:
                reason = f"cost limit ${self.maximum_cost_usd:.4f} would be exceeded"
            if reason is not None:
                self._blocked = True
                raise ProviderBudgetExceededError("Inference budget stopped the run: " + reason)
            self._requests += 1
            self._reserved_tokens += tokens
            self._reserved_cost_usd += cost
            return _Reservation(tokens, cost)

    def settle(
        self,
        reservation: _Reservation,
        response: ChatResponse | None,
        pricing: ModelPricing,
    ) -> None:
        with self._lock:
            self._reserved_tokens -= reservation.tokens
            self._reserved_cost_usd -= reservation.cost_usd
            if response is None:
                return
            usage = response.usage
            self._tokens += usage.total_tokens
            self._cost_usd += (
                usage.input_tokens * pricing.input_per_million_usd
                + usage.output_tokens * pricing.output_per_million_usd
            ) / 1_000_000
            if (
                self._tokens >= self.maximum_tokens
                or self._cost_usd >= self.maximum_cost_usd
                or self._requests >= self.maximum_requests
            ):
                self._blocked = True

    def snapshot(self) -> InferenceBudgetSnapshot:
        with self._lock:
            return InferenceBudgetSnapshot(
                self._requests,
                self._tokens,
                self._cost_usd,
                self._blocked,
            )


class BudgetedProvider:
    """Apply one shared budget to any provider implementing ``LLMProvider``."""

    def __init__(
        self,
        provider: LLMProvider,
        budget: SharedInferenceBudget,
        *,
        pricing: ModelPricing,
        maximum_output_tokens: int,
    ) -> None:
        if maximum_output_tokens <= 0:
            raise ValueError("Maximum output tokens must be greater than zero")
        self._provider = provider
        self._budget = budget
        self._pricing = pricing
        self._maximum_output_tokens = maximum_output_tokens

    def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[ToolDefinition] = (),
        tool_choice: ToolChoice = "auto",
    ) -> ChatResponse:
        # UTF-8 bytes are a deliberately conservative upper bound for normal BPE
        # input tokens. The fixed allowance covers message and request framing.
        serialized = json.dumps(
            {
                "messages": [
                    {
                        "role": message.role,
                        "content": message.content,
                        "tool_calls": [call.name for call in message.tool_calls],
                    }
                    for message in messages
                ],
                "tools": [tool.parameters for tool in tools],
            },
            ensure_ascii=False,
        ).encode("utf-8")
        reservation = self._budget.reserve(
            maximum_input_tokens=len(serialized) + 1_024,
            maximum_output_tokens=self._maximum_output_tokens,
            pricing=self._pricing,
        )
        try:
            response = self._provider.chat(messages, tools=tools, tool_choice=tool_choice)
        except Exception:
            self._budget.settle(reservation, None, self._pricing)
            raise
        self._budget.settle(reservation, response, self._pricing)
        return response
