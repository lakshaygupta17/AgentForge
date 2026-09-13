from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from agentforge.agents.debugger import DebuggerAgent
from agentforge.agents.developer import DEVELOPMENT_ARTIFACT
from agentforge.agents.errors import AgentProtocolError
from agentforge.agents.models import DevelopmentReport
from agentforge.agents.models import TestReport as EvidenceTestReport
from agentforge.agents.reviewer import ReviewerAgent
from agentforge.agents.tester import TEST_ARTIFACT
from agentforge.agents.tester import TesterAgent as AgentTester
from agentforge.core.models import ActionRequest, ExecutionResult, Task, WorkflowState
from agentforge.llm.mock import MockLLMProvider
from agentforge.llm.models import ChatMessage, ChatResponse, ToolCall
from agentforge.policy.engine import SecurityPolicy
from agentforge.telemetry.trajectory import InMemoryTrajectoryRecorder
from agentforge.tools.editor import RepositoryEditor
from agentforge.tools.repository import RepositoryInspector


def tool_response(
    call_id: str, name: str, arguments: dict[str, object]
) -> ChatResponse:
    return ChatResponse(
        ChatMessage(
            "assistant",
            None,
            tool_calls=(ToolCall(call_id, name, arguments),),
        ),
        "mock-coder",
    )


class StaticSandbox:
    workspace = Path(".")

    def execute(
        self, request: ActionRequest, policy: SecurityPolicy
    ) -> ExecutionResult:
        return ExecutionResult(0, "x" * 4_000, "", 0.01)

    def close(self) -> None:
        return None


class EvidenceBudgetingTests(TestCase):
    def test_tester_bounds_command_output_and_remembers_full_evidence(self) -> None:
        state = WorkflowState(Task("Run the focused checkout tests"))
        state.artifacts[DEVELOPMENT_ARTIFACT] = DevelopmentReport(
            "Changed checkout", ("checkout.py",), 1
        )
        provider = MockLLMProvider(
            [
                tool_response(
                    "run",
                    "run_tests",
                    {"executable": "python", "arguments": ["-m", "unittest"]},
                ),
                tool_response(
                    "finish",
                    "finish_testing",
                    {"passed": True, "summary": "Focused tests passed"},
                ),
            ]
        )
        recorder = InMemoryTrajectoryRecorder()

        result = AgentTester(
            provider,
            StaticSandbox(),
            SecurityPolicy(),
            recorder=recorder,
            maximum_tool_observation_characters=300,
        ).run(state)

        tool_messages = [
            message
            for message in provider.requests[1].messages
            if message.role == "tool"
        ]
        observation = tool_messages[-1].content or ""
        self.assertTrue(result.success)
        self.assertLessEqual(len(observation), 300)
        self.assertIn('"truncated": true', observation)
        self.assertTrue(
            any(entry.category == "test_evidence" for entry in state.memory.entries)
        )
        self.assertIn(
            "tool_observation_truncated",
            [event.event_type for event in recorder.events],
        )

    def test_debugger_bounds_failure_evidence_and_remembers_diagnosis(self) -> None:
        state = WorkflowState(Task("Repair checkout"))
        state.artifacts[DEVELOPMENT_ARTIFACT] = DevelopmentReport(
            "Changed checkout", ("checkout.py",), 1
        )
        state.artifacts[TEST_ARTIFACT] = EvidenceTestReport(
            False,
            "Failed",
            ("python", "-m", "unittest"),
            1,
            False,
            "failure output " * 500,
            "traceback " * 500,
        )
        provider = MockLLMProvider(
            [
                tool_response(
                    "diagnose",
                    "submit_diagnosis",
                    {
                        "hypothesis": "The condition is inverted",
                        "evidence": ["The focused assertion failed"],
                        "recommended_changes": ["Invert the condition"],
                    },
                )
            ]
        )
        recorder = InMemoryTrajectoryRecorder()

        result = DebuggerAgent(
            provider, recorder=recorder, maximum_context_characters=300
        ).run(state)

        context = provider.requests[0].messages[-1].content or ""
        self.assertTrue(result.success)
        self.assertLessEqual(len(context), 300)
        self.assertIn('"truncated": true', context)
        self.assertTrue(
            any(entry.category == "diagnosis" for entry in state.memory.entries)
        )
        self.assertIn(
            "model_context_truncated",
            [event.event_type for event in recorder.events],
        )

    def test_reviewer_refuses_to_review_an_incomplete_bounded_diff(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            original = "value = '" + "x" * 1_000 + "'"
            changed = "value = '" + "y" * 1_000 + "'"
            (root / "large.py").write_text(original, encoding="utf-8")
            editor = RepositoryEditor(RepositoryInspector(root))
            editor.apply_patch(
                {
                    "changes": [
                        {
                            "path": "large.py",
                            "old_text": original,
                            "new_text": changed,
                        }
                    ]
                }
            )
            state = WorkflowState(Task("Change the large value"))
            state.artifacts[DEVELOPMENT_ARTIFACT] = DevelopmentReport(
                "Changed the value", ("large.py",), 1
            )
            state.artifacts[TEST_ARTIFACT] = EvidenceTestReport(
                True, "Passed", ("python", "spec.py"), 0, False, "", ""
            )
            provider = MockLLMProvider([])
            recorder = InMemoryTrajectoryRecorder()

            with self.assertRaisesRegex(
                AgentProtocolError, "exceeds the configured context budget"
            ):
                ReviewerAgent(
                    provider,
                    editor,
                    recorder=recorder,
                    maximum_context_characters=300,
                ).run(state)

        self.assertEqual(provider.requests, [])
        self.assertIn(
            "review_context_rejected",
            [event.event_type for event in recorder.events],
        )
