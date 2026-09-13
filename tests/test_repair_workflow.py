import shutil
import sys
from pathlib import Path
from unittest import TestCase

from agentforge.agents.developer import DEVELOPMENT_ARTIFACT, DeveloperAgent
from agentforge.agents.explorer import EXPLORATION_ARTIFACT, RepositoryExplorerAgent
from agentforge.agents.models import (
    DevelopmentReport,
    ExplorationReport,
)
from agentforge.agents.models import (
    TestReport as AgentTestReport,
)
from agentforge.agents.planner import PlannerAgent
from agentforge.agents.tester import TEST_ARTIFACT
from agentforge.agents.tester import TesterAgent as AgentTester
from agentforge.core.models import Task, WorkflowState
from agentforge.llm.mock import MockLLMProvider
from agentforge.llm.models import ChatMessage, ChatResponse, ToolCall
from agentforge.orchestration.runner import MultiAgentOrchestrator
from agentforge.policy.engine import SecurityPolicy
from agentforge.sandbox.local import LocalSandbox
from agentforge.telemetry.trajectory import InMemoryTrajectoryRecorder
from agentforge.tools.editor import RepositoryEditError, RepositoryEditor
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


class RepairWorkflowTests(TestCase):
    def test_full_workflow_repairs_and_tests_seeded_bug(self) -> None:
        with LocalSandbox() as sandbox:
            shutil.copytree(FIXTURE, sandbox.workspace, dirs_exist_ok=True)
            inspector = RepositoryInspector(sandbox.workspace)
            editor = RepositoryEditor(inspector)
            provider = MockLLMProvider(
                [
                    tool_response(
                        "plan",
                        "submit_plan",
                        {
                            "steps": ["Inspect is_even", "Correct the predicate", "Run tests"],
                            "rationale": "The behavior is isolated and testable.",
                        },
                    ),
                    tool_response(
                        "explore",
                        "finish_exploration",
                        {
                            "summary": "is_even returns true for odd values",
                            "relevant_files": ["calculator.py", "spec.py"],
                            "findings": ["calculator.py uses remainder == 1"],
                        },
                    ),
                    tool_response(
                        "patch",
                        "apply_patch",
                        {
                            "changes": [
                                {
                                    "path": "calculator.py",
                                    "old_text": "return value % 2 == 1",
                                    "new_text": "return value % 2 == 0",
                                }
                            ]
                        },
                    ),
                    tool_response(
                        "developed",
                        "finish_development",
                        {
                            "summary": "Corrected the even-number predicate",
                            "changed_files": ["calculator.py"],
                        },
                    ),
                    tool_response(
                        "test",
                        "run_tests",
                        {
                            "executable": sys.executable,
                            "arguments": ["-m", "unittest", "spec.py", "-q"],
                            "working_directory": ".",
                            "timeout_seconds": 10,
                        },
                    ),
                    tool_response(
                        "tested",
                        "finish_testing",
                        {"passed": True, "summary": "Both calculator tests pass"},
                    ),
                ]
            )
            recorder = InMemoryTrajectoryRecorder()
            orchestrator = MultiAgentOrchestrator(
                [
                    PlannerAgent(provider, recorder=recorder),
                    RepositoryExplorerAgent(provider, inspector, recorder=recorder),
                    DeveloperAgent(provider, editor, recorder=recorder),
                    AgentTester(
                        provider,
                        sandbox,
                        SecurityPolicy(),
                        recorder=recorder,
                    ),
                ],
                recorder,
            )

            state = orchestrator.run(Task("Fix is_even so the specification passes"))
            repaired_source = (sandbox.workspace / "calculator.py").read_text(
                encoding="utf-8"
            )

        self.assertFalse(state.failed)
        self.assertIn("== 0", repaired_source)
        self.assertIsInstance(state.artifacts[EXPLORATION_ARTIFACT], ExplorationReport)
        development = state.artifacts[DEVELOPMENT_ARTIFACT]
        tests = state.artifacts[TEST_ARTIFACT]
        self.assertIsInstance(development, DevelopmentReport)
        self.assertEqual(development.changed_files, ("calculator.py",))
        self.assertIsInstance(tests, AgentTestReport)
        self.assertTrue(tests.passed)
        self.assertEqual(
            state.completed_agents,
            ["planner", "repository_explorer", "developer", "tester"],
        )
        event_types = [event.event_type for event in recorder.events]
        self.assertIn("patch_applied", event_types)
        self.assertIn("test_execution_completed", event_types)
        self.assertIn("testing_completed", event_types)

    def test_test_agent_cannot_claim_a_failed_command_passed(self) -> None:
        with LocalSandbox() as sandbox:
            (sandbox.workspace / "file.py").write_text("value = 1", encoding="utf-8")
            inspector = RepositoryInspector(sandbox.workspace)
            editor = RepositoryEditor(inspector)
            editor.apply_patch(
                {
                    "changes": [
                        {"path": "file.py", "old_text": "1", "new_text": "2"}
                    ]
                }
            )
            state = WorkflowState(Task("test"))
            state.artifacts[DEVELOPMENT_ARTIFACT] = DevelopmentReport(
                "changed", ("file.py",), 1
            )
            provider = MockLLMProvider(
                [
                    tool_response(
                        "run",
                        "run_tests",
                        {
                            "executable": sys.executable,
                            "arguments": ["-c", "raise SystemExit(1)"],
                        },
                    ),
                    tool_response(
                        "finish",
                        "finish_testing",
                        {"passed": True, "summary": "Looks good"},
                    ),
                ]
            )
            agent = AgentTester(provider, sandbox, SecurityPolicy())

            with self.assertRaisesRegex(RuntimeError, "does not match"):
                agent.run(state)


class RepositoryEditorTests(TestCase):
    def test_enforces_configured_write_path_policy(self) -> None:
        with LocalSandbox() as sandbox:
            source = sandbox.workspace / "source.py"
            source.write_text("value = 1\n", encoding="utf-8")
            policy = SecurityPolicy(writable_paths=("tests/*",))
            editor = RepositoryEditor(
                RepositoryInspector(sandbox.workspace, policy=policy)
            )

            with self.assertRaisesRegex(RepositoryEditError, "allowlist"):
                editor.apply_patch(
                    {
                        "changes": [
                            {
                                "path": "source.py",
                                "old_text": "value = 1",
                                "new_text": "value = 2",
                            }
                        ]
                    }
                )

            self.assertEqual(source.read_text(encoding="utf-8"), "value = 1\n")

    def test_rejects_ambiguous_patch_without_changing_any_file(self) -> None:
        with LocalSandbox() as sandbox:
            first = sandbox.workspace / "first.py"
            second = sandbox.workspace / "second.py"
            first.write_text("value = 1\n", encoding="utf-8")
            second.write_text("same\nsame\n", encoding="utf-8")
            editor = RepositoryEditor(RepositoryInspector(sandbox.workspace))

            with self.assertRaisesRegex(RepositoryEditError, "found 2"):
                editor.apply_patch(
                    {
                        "changes": [
                            {
                                "path": "first.py",
                                "old_text": "value = 1",
                                "new_text": "value = 2",
                            },
                            {
                                "path": "second.py",
                                "old_text": "same",
                                "new_text": "changed",
                            },
                        ]
                    }
                )

            self.assertEqual(first.read_text(encoding="utf-8"), "value = 1\n")
            self.assertEqual(second.read_text(encoding="utf-8"), "same\nsame\n")

    def test_rejects_edits_to_sensitive_files(self) -> None:
        with LocalSandbox() as sandbox:
            secret = sandbox.workspace / ".env"
            secret.write_text("TOKEN=secret", encoding="utf-8")
            editor = RepositoryEditor(RepositoryInspector(sandbox.workspace))

            with self.assertRaisesRegex(RepositoryEditError, "credential"):
                editor.apply_patch(
                    {
                        "changes": [
                            {
                                "path": ".env",
                                "old_text": "secret",
                                "new_text": "stolen",
                            }
                        ]
                    }
                )

            self.assertEqual(secret.read_text(encoding="utf-8"), "TOKEN=secret")
