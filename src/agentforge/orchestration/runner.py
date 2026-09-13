"""A deterministic orchestration foundation for future LangGraph workflows."""

from __future__ import annotations

from collections.abc import Iterable

from agentforge.agents.base import Agent
from agentforge.core.approval import ApprovalCheckpoint
from agentforge.core.models import Task, WorkflowState
from agentforge.telemetry.trajectory import TrajectoryEvent, TrajectoryRecorder


class MultiAgentOrchestrator:
    def __init__(self, agents: Iterable[Agent], recorder: TrajectoryRecorder) -> None:
        self._agents = tuple(agents)
        if not self._agents:
            raise ValueError("At least one agent is required")
        names = [agent.name for agent in self._agents]
        if len(names) != len(set(names)):
            raise ValueError("Agent names must be unique")
        self._recorder = recorder

    def run(
        self, task: Task, *, approval: ApprovalCheckpoint | None = None
    ) -> WorkflowState:
        state = WorkflowState(task=task, approval=approval or ApprovalCheckpoint())
        self._record(state.run_id, "workflow_started", "orchestrator", {"task_id": task.id})
        if approval is not None:
            self._record(
                state.run_id,
                "human_approval_recorded",
                "user",
                {"granted_capabilities": list(approval.granted_capabilities())},
            )

        for agent in self._agents:
            self._record(state.run_id, "agent_started", agent.name)
            try:
                result = agent.run(state)
            except Exception as error:
                state.failed = True
                state.failure_reason = f"{agent.name} raised {type(error).__name__}: {error}"
                self._record(
                    state.run_id,
                    "agent_failed",
                    agent.name,
                    {"reason": state.failure_reason},
                )
                break

            state.notes.append(result.summary)
            state.memory.remember(
                "agent_summary", agent.name, result.summary, priority=70
            )
            state.artifacts.update(result.artifacts)
            if result.success:
                state.completed_agents.append(agent.name)
                self._record(
                    state.run_id,
                    "agent_completed",
                    agent.name,
                    {"summary": result.summary},
                )
                continue

            state.failed = True
            state.failure_reason = result.summary
            self._record(
                state.run_id,
                "agent_failed",
                agent.name,
                {"reason": result.summary},
            )
            break

        self._record(
            state.run_id,
            "workflow_failed" if state.failed else "workflow_completed",
            "orchestrator",
            {"completed_agents": state.completed_agents},
        )
        return state

    def _record(
        self,
        run_id: str,
        event_type: str,
        actor: str,
        data: dict[str, object] | None = None,
    ) -> None:
        self._recorder.record(
            TrajectoryEvent(
                run_id=run_id,
                event_type=event_type,
                actor=actor,
                data=data or {},
            )
        )
