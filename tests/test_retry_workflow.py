import shutil
import sys
from pathlib import Path
from unittest import TestCase

from agentforge.agents.base import AgentResult
from agentforge.agents.debugger import DIAGNOSIS_ARTIFACT, DebuggerAgent
from agentforge.agents.developer import DeveloperAgent
from agentforge.agents.explorer import RepositoryExplorerAgent
from agentforge.agents.models import Diagnosis, ReviewReport
from agentforge.agents.planner import PlannerAgent
from agentforge.agents.reviewer import REVIEW_ARTIFACT, ReviewerAgent
from agentforge.agents.tester import TesterAgent as AgentTester
from agentforge.core.models import Task, WorkflowState
from agentforge.llm.mock import MockLLMProvider
from agentforge.llm.models import ChatMessage, ChatResponse, ToolCall
from agentforge.orchestration.repair import RepairOrchestrator
from agentforge.policy.engine import SecurityPolicy
from agentforge.sandbox.local import LocalSandbox
from agentforge.telemetry.trajectory import InMemoryTrajectoryRecorder
from agentforge.tools.editor import RepositoryEditor
from agentforge.tools.repository import RepositoryInspector

FIXTURE = Path(__file__).parent / "fixtures" / "buggy_calculator"


def tool_response(call_id: str, name: str, arguments: dict[str, object]) -> ChatResponse:
    return ChatResponse(
        ChatMessage(
            "assistant",
            None,
            tool_calls=(ToolCall(call_id, name, arguments),),
        ),
        "mock-coder",
    )


class RepairRetryWorkflowTests(TestCase):
    def test_failed_tests_trigger_debug_fix_retest_and_review(self) -> None:
        with LocalSandbox() as sandbox:
            shutil.copytree(FIXTURE, sandbox.workspace, dirs_exist_ok=True)
            inspector = RepositoryInspector(sandbox.workspace)
            editor = RepositoryEditor(inspector)
            test_call = {
                "executable": sys.executable,
                "arguments": ["-B", "-m", "unittest", "spec.py", "-q"],
                "timeout_seconds": 10,
            }
            provider = MockLLMProvider(
                [
                    tool_response(
                        "plan",
                        "submit_plan",
                        {
                            "steps": ["Inspect predicate", "Patch it", "Run specification"],
                            "rationale": "The specification directly describes the behavior.",
                        },
                    ),
                    tool_response(
                        "explore",
                        "finish_exploration",
                        {
                            "summary": "The remainder comparison is incorrect",
                            "relevant_files": ["calculator.py", "spec.py"],
                            "findings": ["is_even compares the remainder with one"],
                        },
                    ),
                    tool_response(
                        "bad-patch",
                        "apply_patch",
                        {
                            "changes": [
                                {
                                    "path": "calculator.py",
                                    "old_text": "return value % 2 == 1",
                                    "new_text": "return value % 2 == 2",
                                }
                            ]
                        },
                    ),
                    tool_response(
                        "bad-development",
                        "finish_development",
                        {
                            "summary": "Changed the remainder comparison",
                            "changed_files": ["calculator.py"],
                        },
                    ),
                    tool_response("failed-run", "run_tests", test_call),
                    tool_response(
                        "failed-report",
                        "finish_testing",
                        {"passed": False, "summary": "The parity tests still fail"},
                    ),
                    tool_response(
                        "diagnosis",
                        "submit_diagnosis",
                        {
                            "hypothesis": "A remainder modulo two can never equal two",
                            "evidence": ["Both parity assertions failed after the first patch"],
                            "recommended_changes": ["Compare the remainder with zero"],
                        },
                    ),
                    tool_response(
                        "good-patch",
                        "apply_patch",
                        {
                            "changes": [
                                {
                                    "path": "calculator.py",
                                    "old_text": "return value % 2 == 2",
                                    "new_text": "return value % 2 == 0",
                                }
                            ]
                        },
                    ),
                    tool_response(
                        "good-development",
                        "finish_development",
                        {
                            "summary": "Applied the debugger's corrected comparison",
                            "changed_files": ["calculator.py"],
                        },
                    ),
                    tool_response("passing-run", "run_tests", test_call),
                    tool_response(
                        "passing-report",
                        "finish_testing",
                        {"passed": True, "summary": "Both parity tests pass"},
                    ),
                    tool_response(
                        "review",
                        "submit_review",
                        {
                            "accepted": True,
                            "summary": "The minimal patch matches the tested specification",
                            "issues": [],
                        },
                    ),
                ]
            )
            recorder = InMemoryTrajectoryRecorder()
            workflow = RepairOrchestrator(
                planner=PlannerAgent(provider, recorder=recorder),
                explorer=RepositoryExplorerAgent(provider, inspector, recorder=recorder),
                developer=DeveloperAgent(provider, editor, recorder=recorder),
                tester=AgentTester(
                    provider, sandbox, SecurityPolicy(), recorder=recorder
                ),
                debugger=DebuggerAgent(provider, recorder=recorder),
                reviewer=ReviewerAgent(provider, editor, recorder=recorder),
                recorder=recorder,
                maximum_repair_attempts=2,
            )

            state = workflow.run(Task("Fix is_even so its specification passes"))

        self.assertFalse(state.failed, state.failure_reason)
        self.assertIsInstance(state.artifacts[DIAGNOSIS_ARTIFACT], Diagnosis)
        review = state.artifacts[REVIEW_ARTIFACT]
        self.assertIsInstance(review, ReviewReport)
        self.assertTrue(review.accepted)
        self.assertIn("-    return value % 2 == 1", review.diff)
        self.assertIn("+    return value % 2 == 0", review.diff)
        self.assertEqual(state.completed_agents.count("developer"), 2)
        event_types = [event.event_type for event in recorder.events]
        self.assertIn("tests_failed", event_types)
        self.assertIn("diagnosis_created", event_types)
        self.assertIn("repair_retry_scheduled", event_types)
        self.assertIn("review_completed", event_types)
        self.assertEqual(event_types[-1], "workflow_completed")

    def test_retry_budget_stops_without_calling_debugger_on_last_attempt(self) -> None:
        class SuccessfulAgent:
            def __init__(self, name: str) -> None:
                self.name = name

            def run(self, state: object) -> object:
                from agentforge.agents.base import AgentResult

                return AgentResult(True, f"{self.name} complete")

        class FailedTester:
            name = "tester"

            def run(self, state: object) -> object:
                from agentforge.agents.base import AgentResult

                return AgentResult(False, "tests failed")

        class MustNotRun:
            name = "debugger"

            def run(self, state: object) -> object:
                raise AssertionError("Debugger should not run after budget exhaustion")

        recorder = InMemoryTrajectoryRecorder()
        workflow = RepairOrchestrator(
            planner=SuccessfulAgent("planner"),  # type: ignore[arg-type]
            explorer=SuccessfulAgent("explorer"),  # type: ignore[arg-type]
            developer=SuccessfulAgent("developer"),  # type: ignore[arg-type]
            tester=FailedTester(),  # type: ignore[arg-type]
            debugger=MustNotRun(),  # type: ignore[arg-type]
            reviewer=MustNotRun(),  # type: ignore[arg-type]
            recorder=recorder,
            maximum_repair_attempts=1,
        )

        state = workflow.run(Task("Never pass"))

        self.assertTrue(state.failed)
        self.assertIn("after 1 repair attempts", state.failure_reason or "")

    def test_rejected_review_feedback_reaches_next_development_attempt(self) -> None:
        class SuccessfulAgent:
            def __init__(self, name: str) -> None:
                self.name = name

            def run(self, state: WorkflowState) -> AgentResult:
                return AgentResult(True, f"{self.name} complete")

        class FeedbackAwareDeveloper:
            name = "developer"

            def __init__(self) -> None:
                self.calls = 0
                self.saw_feedback = False

            def run(self, state: WorkflowState) -> AgentResult:
                self.calls += 1
                if self.calls == 2:
                    memory = state.memory.render("review", maximum_characters=1_000)
                    self.saw_feedback = "Handle negative values" in memory.text
                return AgentResult(True, "development complete")

        class RejectThenAcceptReviewer:
            name = "reviewer"

            def __init__(self) -> None:
                self.calls = 0

            def run(self, state: WorkflowState) -> AgentResult:
                self.calls += 1
                accepted = self.calls == 2
                report = ReviewReport(
                    accepted,
                    "Review accepted" if accepted else "Repair is incomplete",
                    () if accepted else ("Handle negative values",),
                    "diff",
                )
                return AgentResult(accepted, report.summary, {REVIEW_ARTIFACT: report})

        developer = FeedbackAwareDeveloper()
        reviewer = RejectThenAcceptReviewer()
        recorder = InMemoryTrajectoryRecorder()
        workflow = RepairOrchestrator(
            planner=SuccessfulAgent("planner"),
            explorer=SuccessfulAgent("explorer"),
            developer=developer,
            tester=SuccessfulAgent("tester"),
            debugger=SuccessfulAgent("debugger"),
            reviewer=reviewer,
            recorder=recorder,
            maximum_repair_attempts=2,
        )

        state = workflow.run(Task("Apply a fully reviewed repair"))

        self.assertFalse(state.failed, state.failure_reason)
        self.assertEqual(developer.calls, 2)
        self.assertEqual(reviewer.calls, 2)
        self.assertTrue(developer.saw_feedback)
        event_types = [event.event_type for event in recorder.events]
        self.assertIn("review_rejected", event_types)
        self.assertIn("repair_retry_scheduled", event_types)

    def test_rejected_review_fails_when_retry_budget_is_exhausted(self) -> None:
        class SuccessfulAgent:
            def __init__(self, name: str) -> None:
                self.name = name

            def run(self, state: WorkflowState) -> AgentResult:
                return AgentResult(True, f"{self.name} complete")

        class RejectingReviewer:
            name = "reviewer"

            def run(self, state: WorkflowState) -> AgentResult:
                report = ReviewReport(False, "Unsafe edge case", ("Fix it",), "diff")
                return AgentResult(False, report.summary, {REVIEW_ARTIFACT: report})

        recorder = InMemoryTrajectoryRecorder()
        workflow = RepairOrchestrator(
            planner=SuccessfulAgent("planner"),
            explorer=SuccessfulAgent("explorer"),
            developer=SuccessfulAgent("developer"),
            tester=SuccessfulAgent("tester"),
            debugger=SuccessfulAgent("debugger"),
            reviewer=RejectingReviewer(),
            recorder=recorder,
            maximum_repair_attempts=1,
        )

        state = workflow.run(Task("Never accept the review"))

        self.assertTrue(state.failed)
        self.assertIn("Reviewer rejected patch", state.failure_reason or "")
        event_types = [event.event_type for event in recorder.events]
        self.assertIn("review_rejected", event_types)
        self.assertNotIn("repair_retry_scheduled", event_types)
