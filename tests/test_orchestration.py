from dataclasses import dataclass
from unittest import TestCase

from agentforge.agents.base import AgentResult
from agentforge.core.approval import ApprovalCheckpoint
from agentforge.core.models import Task, WorkflowState
from agentforge.orchestration.runner import MultiAgentOrchestrator
from agentforge.telemetry.trajectory import InMemoryTrajectoryRecorder


@dataclass
class FakeAgent:
    name: str
    succeeds: bool = True

    def run(self, state: WorkflowState) -> AgentResult:
        return AgentResult(
            success=self.succeeds,
            summary=f"{self.name} finished" if self.succeeds else f"{self.name} rejected work",
            artifacts={self.name: len(state.completed_agents)},
        )


class MultiAgentOrchestratorTests(TestCase):
    def test_agents_receive_state_in_order_and_events_are_recorded(self) -> None:
        recorder = InMemoryTrajectoryRecorder()
        orchestrator = MultiAgentOrchestrator(
            [FakeAgent("planner"), FakeAgent("explorer"), FakeAgent("reviewer")],
            recorder,
        )

        state = orchestrator.run(Task("Fix the seeded bug"))

        self.assertFalse(state.failed)
        self.assertEqual(state.completed_agents, ["planner", "explorer", "reviewer"])
        self.assertEqual(state.artifacts, {"planner": 0, "explorer": 1, "reviewer": 2})
        self.assertEqual(
            [entry.source for entry in state.memory.entries],
            ["planner", "explorer", "reviewer"],
        )
        self.assertEqual(recorder.events[0].event_type, "workflow_started")
        self.assertEqual(recorder.events[-1].event_type, "workflow_completed")

    def test_records_only_granted_approval_capability_names(self) -> None:
        recorder = InMemoryTrajectoryRecorder()
        orchestrator = MultiAgentOrchestrator([FakeAgent("planner")], recorder)
        approval = ApprovalCheckpoint(code_transmission=True)

        state = orchestrator.run(Task("Inspect safely"), approval=approval)

        event = next(
            item
            for item in recorder.events
            if item.event_type == "human_approval_recorded"
        )
        self.assertIs(state.approval, approval)
        self.assertEqual(event.actor, "user")
        self.assertEqual(
            event.data, {"granted_capabilities": ["code_transmission"]}
        )

    def test_failure_stops_later_agents(self) -> None:
        recorder = InMemoryTrajectoryRecorder()
        orchestrator = MultiAgentOrchestrator(
            [FakeAgent("planner"), FakeAgent("tester", succeeds=False), FakeAgent("reviewer")],
            recorder,
        )

        state = orchestrator.run(Task("Fix the seeded bug"))

        self.assertTrue(state.failed)
        self.assertEqual(state.completed_agents, ["planner"])
        self.assertNotIn("reviewer", state.artifacts)
        self.assertEqual(recorder.events[-1].event_type, "workflow_failed")

    def test_agent_exceptions_become_a_failed_workflow(self) -> None:
        class BrokenAgent:
            name = "broken"

            def run(self, state: WorkflowState) -> AgentResult:
                raise RuntimeError("model unavailable")

        recorder = InMemoryTrajectoryRecorder()
        orchestrator = MultiAgentOrchestrator([BrokenAgent()], recorder)

        state = orchestrator.run(Task("Fix the seeded bug"))

        self.assertTrue(state.failed)
        self.assertIn("model unavailable", state.failure_reason or "")

    def test_each_workflow_gets_independent_run_memory(self) -> None:
        recorder = InMemoryTrajectoryRecorder()
        orchestrator = MultiAgentOrchestrator([FakeAgent("planner")], recorder)

        first = orchestrator.run(Task("First task"))
        second = orchestrator.run(Task("Second task"))

        self.assertIsNot(first.memory, second.memory)
        self.assertEqual(len(first.memory.entries), 1)
        self.assertEqual(len(second.memory.entries), 1)
