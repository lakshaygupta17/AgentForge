"""Contracts implemented by every specialist agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from agentforge.core.models import WorkflowState


@dataclass(frozen=True, slots=True)
class AgentResult:
    success: bool
    summary: str
    artifacts: dict[str, Any] = field(default_factory=dict)


class Agent(Protocol):
    name: str

    def run(self, state: WorkflowState) -> AgentResult:
        """Perform one role's work without directly controlling orchestration."""
        ...

