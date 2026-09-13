"""Explicit repair state machine with test/debug/retry edges."""

from __future__ import annotations

from agentforge.agents.base import Agent, AgentResult
from agentforge.agents.models import ReviewReport, TestReport
from agentforge.agents.reviewer import REVIEW_ARTIFACT
from agentforge.agents.tester import TEST_ARTIFACT
from agentforge.core.approval import ApprovalCheckpoint
from agentforge.core.models import Task, WorkflowState
from agentforge.telemetry.trajectory import TrajectoryEvent, TrajectoryRecorder


class RepairOrchestrator:
    """Run the first complete repair graph without coupling it to LangGraph."""

    def __init__(
        self,
        *,
        planner: Agent,
        explorer: Agent,
        developer: Agent,
        tester: Agent,
        debugger: Agent,
        reviewer: Agent,
        recorder: TrajectoryRecorder,
        maximum_repair_attempts: int = 3,
    ) -> None:
        if maximum_repair_attempts <= 0:
            raise ValueError("Maximum repair attempts must be greater than zero")
        self._planner = planner
        self._explorer = explorer
        self._developer = developer
        self._tester = tester
        self._debugger = debugger
        self._reviewer = reviewer
        self._recorder = recorder
        self._maximum_repair_attempts = maximum_repair_attempts

    def run(
        self, task: Task, *, approval: ApprovalCheckpoint | None = None
    ) -> WorkflowState:
        state = WorkflowState(task, approval=approval or ApprovalCheckpoint())
        self._record(state, "workflow_started", "orchestrator", {"task_id": task.id})
        if approval is not None:
            self._record(
                state,
                "human_approval_recorded",
                "user",
                {"granted_capabilities": list(approval.granted_capabilities())},
            )

        for agent in (self._planner, self._explorer):
            result = self._run_agent(state, agent)
            if result is None:
                return self._finish(state)
            if not result.success:
                return self._fail(state, result.summary)

        for attempt in range(1, self._maximum_repair_attempts + 1):
            self._record(
                state,
                "repair_attempt_started",
                "orchestrator",
                {"attempt": attempt},
            )
            development = self._run_agent(state, self._developer)
            if development is None:
                return self._finish(state)
            if not development.success:
                return self._fail(state, development.summary)

            testing = self._run_agent(state, self._tester)
            if testing is None:
                return self._finish(state)
            if testing.success:
                self._record(
                    state,
                    "tests_passed",
                    "orchestrator",
                    {"attempt": attempt},
                )
                review = self._run_agent(state, self._reviewer)
                if review is None:
                    return self._finish(state)
                if not review.success:
                    self._record(
                        state,
                        "review_rejected",
                        "orchestrator",
                        {"attempt": attempt, "reason": review.summary},
                    )
                    if attempt == self._maximum_repair_attempts:
                        return self._fail(
                            state, f"Reviewer rejected patch: {review.summary}"
                        )
                    report = state.artifacts.get(REVIEW_ARTIFACT)
                    feedback = review.summary
                    if isinstance(report, ReviewReport):
                        feedback += " Issues: " + "; ".join(report.issues)
                    state.memory.remember(
                        "review_feedback", "reviewer", feedback, priority=95
                    )
                    self._record(
                        state,
                        "repair_retry_scheduled",
                        "orchestrator",
                        {"next_attempt": attempt + 1, "reason": "review_rejected"},
                    )
                    continue
                return self._finish(state)

            test_report = testing.artifacts.get(TEST_ARTIFACT)
            if not isinstance(test_report, TestReport) or test_report.passed:
                reason = (
                    "Tester stopped without a verified failed TestReport; "
                    "Debugger was not started"
                )
                self._record(
                    state,
                    "testing_protocol_failed",
                    "orchestrator",
                    {"attempt": attempt, "reason": reason},
                )
                return self._fail(state, reason)

            self._record(
                state,
                "tests_failed",
                "orchestrator",
                {"attempt": attempt, "reason": testing.summary},
            )
            if attempt == self._maximum_repair_attempts:
                return self._fail(
                    state,
                    f"Tests still failed after {attempt} repair attempts: {testing.summary}",
                )

            diagnosis = self._run_agent(state, self._debugger)
            if diagnosis is None:
                return self._finish(state)
            if not diagnosis.success:
                return self._fail(state, diagnosis.summary)
            self._record(
                state,
                "repair_retry_scheduled",
                "orchestrator",
                {"next_attempt": attempt + 1},
            )

        return self._fail(state, "Repair workflow ended unexpectedly")

    def _run_agent(self, state: WorkflowState, agent: Agent) -> AgentResult | None:
        self._record(state, "agent_started", agent.name)
        try:
            result = agent.run(state)
        except Exception as error:
            state.failed = True
            state.failure_reason = f"{agent.name} raised {type(error).__name__}: {error}"
            self._record(
                state,
                "agent_failed",
                agent.name,
                {"reason": state.failure_reason},
            )
            return None

        state.notes.append(result.summary)
        state.memory.remember(
            "agent_summary", agent.name, result.summary, priority=70
        )
        state.artifacts.update(result.artifacts)
        if result.success:
            state.completed_agents.append(agent.name)
            self._record(
                state,
                "agent_completed",
                agent.name,
                {"summary": result.summary},
            )
        else:
            self._record(
                state,
                "agent_reported_failure",
                agent.name,
                {"summary": result.summary},
            )
        return result

    def _fail(self, state: WorkflowState, reason: str) -> WorkflowState:
        state.failed = True
        state.failure_reason = reason
        return self._finish(state)

    def _finish(self, state: WorkflowState) -> WorkflowState:
        self._record(
            state,
            "workflow_failed" if state.failed else "workflow_completed",
            "orchestrator",
            {
                "completed_agents": state.completed_agents,
                "failure_reason": state.failure_reason,
            },
        )
        return state

    def _record(
        self,
        state: WorkflowState,
        event_type: str,
        actor: str,
        data: dict[str, object] | None = None,
    ) -> None:
        self._recorder.record(
            TrajectoryEvent(state.run_id, event_type, actor, data or {})
        )
