from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from agentforge.agents.explorer import EXPLORATION_ARTIFACT, RepositoryExplorerAgent
from agentforge.agents.models import ExplorationReport, Plan
from agentforge.agents.planner import PLAN_ARTIFACT, PlannerAgent
from agentforge.core.models import Task, WorkflowState
from agentforge.llm.mock import MockLLMProvider
from agentforge.llm.models import ChatMessage, ChatResponse, ToolCall
from agentforge.orchestration.runner import MultiAgentOrchestrator
from agentforge.policy.engine import SecurityPolicy
from agentforge.telemetry.trajectory import InMemoryTrajectoryRecorder
from agentforge.tools.repository import RepositoryInspector, RepositoryToolError


def tool_response(call_id: str, name: str, arguments: dict[str, object]) -> ChatResponse:
    return ChatResponse(
        ChatMessage(
            "assistant",
            None,
            tool_calls=(ToolCall(call_id, name, arguments),),
        ),
        "mock-coder",
    )


class PlannerAndExplorerWorkflowTests(TestCase):
    def test_plans_then_inspects_a_repository_with_recorded_tool_events(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "app.py").write_text(
                "def checkout(cart):\n    return cart[0]\n", encoding="utf-8"
            )
            (root / "README.md").write_text("Example shop", encoding="utf-8")
            provider = MockLLMProvider(
                [
                    tool_response(
                        "plan-1",
                        "submit_plan",
                        {
                            "steps": ["Inspect checkout code", "Identify empty-cart behavior"],
                            "rationale": "The failure is likely near cart access.",
                        },
                    ),
                    tool_response("explore-1", "list_files", {"path": "."}),
                    tool_response("explore-2", "read_file", {"path": "app.py"}),
                    tool_response(
                        "explore-3",
                        "finish_exploration",
                        {
                            "summary": "checkout indexes the cart without an empty check",
                            "relevant_files": ["app.py"],
                            "findings": ["app.py:2 directly accesses cart[0]"],
                        },
                    ),
                ]
            )
            recorder = InMemoryTrajectoryRecorder()
            orchestrator = MultiAgentOrchestrator(
                [
                    PlannerAgent(provider, recorder=recorder),
                    RepositoryExplorerAgent(
                        provider,
                        RepositoryInspector(root),
                        recorder=recorder,
                    ),
                ],
                recorder,
            )

            state = orchestrator.run(Task("Handle an empty cart without crashing"))

        self.assertFalse(state.failed)
        self.assertIsInstance(state.artifacts[PLAN_ARTIFACT], Plan)
        report = state.artifacts[EXPLORATION_ARTIFACT]
        self.assertIsInstance(report, ExplorationReport)
        self.assertEqual(report.relevant_files, ("app.py",))
        self.assertEqual(len(provider.requests), 4)
        second_explorer_request = provider.requests[2]
        self.assertEqual(second_explorer_request.messages[-1].role, "tool")
        self.assertIn("app.py", second_explorer_request.messages[-1].content or "")

        event_types = [event.event_type for event in recorder.events]
        self.assertIn("plan_created", event_types)
        self.assertEqual(event_types.count("tool_completed"), 2)
        self.assertIn("exploration_completed", event_types)
        self.assertTrue(all(event.run_id == state.run_id for event in recorder.events))

    def test_invalid_plan_fails_before_repository_exploration(self) -> None:
        provider = MockLLMProvider(
            [
                tool_response(
                    "plan-1",
                    "submit_plan",
                    {"steps": [], "rationale": "No work needed"},
                )
            ]
        )
        recorder = InMemoryTrajectoryRecorder()
        with TemporaryDirectory() as temporary_directory:
            orchestrator = MultiAgentOrchestrator(
                [
                    PlannerAgent(provider, recorder=recorder),
                    RepositoryExplorerAgent(
                        provider,
                        RepositoryInspector(Path(temporary_directory)),
                        recorder=recorder,
                    ),
                ],
                recorder,
            )

            state = orchestrator.run(Task("Do something"))

        self.assertTrue(state.failed)
        self.assertIn("no plan steps", state.failure_reason or "")
        self.assertEqual(state.completed_agents, [])
        self.assertEqual(len(provider.requests), 1)

    def test_explorer_can_find_a_parsed_python_symbol_before_reading_its_file(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "service.py").write_text(
                "class CheckoutService:\n"
                "    def checkout(self, cart):\n"
                "        return cart[0]\n",
                encoding="utf-8",
            )
            provider = MockLLMProvider(
                [
                    tool_response(
                        "plan",
                        "submit_plan",
                        {"steps": ["Find checkout"], "rationale": "Locate behavior"},
                    ),
                    tool_response(
                        "symbol",
                        "find_symbol",
                        {"query": "checkout", "kind": "method"},
                    ),
                    tool_response("read", "read_file", {"path": "service.py"}),
                    tool_response(
                        "finish",
                        "finish_exploration",
                        {
                            "summary": "Found checkout method",
                            "relevant_files": ["service.py"],
                            "findings": ["CheckoutService.checkout starts on line 2"],
                        },
                    ),
                ]
            )
            state = MultiAgentOrchestrator(
                [
                    PlannerAgent(provider),
                    RepositoryExplorerAgent(provider, RepositoryInspector(root)),
                ],
                InMemoryTrajectoryRecorder(),
            ).run(Task("Inspect checkout"))

        self.assertFalse(state.failed)
        symbol_observation = provider.requests[3].messages[-3].content or ""
        self.assertIn("CheckoutService.checkout", symbol_observation)
        self.assertIn('"line": 2', symbol_observation)

    def test_rejected_tool_request_is_returned_to_the_model_for_recovery(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "safe.py").write_text("value = 1\n", encoding="utf-8")
            provider = MockLLMProvider(
                [
                    tool_response(
                        "plan-1",
                        "submit_plan",
                        {"steps": ["Inspect files"], "rationale": "Need context"},
                    ),
                    tool_response("bad-1", "read_file", {"path": "../secret.txt"}),
                    tool_response(
                        "finish-1",
                        "finish_exploration",
                        {
                            "summary": "No relevant defect was found",
                            "relevant_files": [],
                            "findings": [],
                        },
                    ),
                ]
            )
            recorder = InMemoryTrajectoryRecorder()
            orchestrator = MultiAgentOrchestrator(
                [
                    PlannerAgent(provider, recorder=recorder),
                    RepositoryExplorerAgent(
                        provider, RepositoryInspector(root), recorder=recorder
                    ),
                ],
                recorder,
            )

            state = orchestrator.run(Task("Inspect safely"))

        self.assertFalse(state.failed)
        recovery_request = provider.requests[-1]
        self.assertIn("escapes", recovery_request.messages[-1].content or "")
        self.assertIn(
            "tool_rejected", [event.event_type for event in recorder.events]
        )

    def test_final_explorer_turn_offers_only_the_required_report_tool(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "app.py").write_text("value = 1\n", encoding="utf-8")
            provider = MockLLMProvider(
                [
                    tool_response("list", "list_files", {"path": "."}),
                    tool_response(
                        "finish",
                        "finish_exploration",
                        {
                            "summary": "Inspected the small repository",
                            "relevant_files": ["app.py"],
                            "findings": ["app.py contains the implementation"],
                        },
                    ),
                ]
            )
            state = WorkflowState(
                task=Task("Inspect app"),
                artifacts={
                    PLAN_ARTIFACT: Plan(("Inspect app.py",), "Find the implementation")
                },
            )
            recorder = InMemoryTrajectoryRecorder()

            result = RepositoryExplorerAgent(
                provider,
                RepositoryInspector(root),
                recorder=recorder,
                maximum_iterations=2,
            ).run(state)

        self.assertTrue(result.success)
        self.assertIn(
            "Trace imports and function calls",
            provider.requests[0].messages[0].content or "",
        )
        self.assertEqual(
            tuple(tool.name for tool in provider.requests[-1].tools),
            ("finish_exploration",),
        )
        self.assertEqual(provider.requests[-1].messages[-1].role, "system")
        self.assertIn(
            "exploration_finalization_required",
            [event.event_type for event in recorder.events],
        )

    def test_explorer_recovers_when_final_turn_calls_wrong_tool(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "app.py").write_text("value = 1\n", encoding="utf-8")
            provider = MockLLMProvider(
                [
                    tool_response("wrong", "run_tests", {}),
                    tool_response(
                        "finish",
                        "finish_exploration",
                        {
                            "summary": "Inspected the small repository",
                            "relevant_files": ["app.py"],
                            "findings": ["app.py contains the implementation"],
                        },
                    ),
                ]
            )
            state = WorkflowState(
                task=Task("Inspect app"),
                artifacts={
                    PLAN_ARTIFACT: Plan(("Inspect app.py",), "Find the implementation")
                },
            )
            recorder = InMemoryTrajectoryRecorder()

            result = RepositoryExplorerAgent(
                provider,
                RepositoryInspector(root),
                recorder=recorder,
                maximum_iterations=1,
            ).run(state)

        self.assertTrue(result.success)
        self.assertEqual(len(provider.requests), 2)
        self.assertEqual(
            tuple(tool.name for tool in provider.requests[-1].tools),
            ("finish_exploration",),
        )
        event_types = [event.event_type for event in recorder.events]
        self.assertIn("exploration_finalization_retry", event_types)
        self.assertIn("tool_rejected", event_types)

    def test_explorer_fails_closed_when_correction_retry_calls_wrong_tool(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "app.py").write_text("value = 1\n", encoding="utf-8")
            provider = MockLLMProvider(
                [
                    tool_response("wrong-1", "run_tests", {}),
                    tool_response("wrong-2", "read_file", {"path": "app.py"}),
                ]
            )
            state = WorkflowState(
                task=Task("Inspect app"),
                artifacts={
                    PLAN_ARTIFACT: Plan(("Inspect app.py",), "Find the implementation")
                },
            )
            recorder = InMemoryTrajectoryRecorder()

            result = RepositoryExplorerAgent(
                provider,
                RepositoryInspector(root),
                recorder=recorder,
                maximum_iterations=1,
            ).run(state)

        self.assertFalse(result.success)
        self.assertEqual(len(provider.requests), 2)
        event_types = [event.event_type for event in recorder.events]
        self.assertEqual(event_types.count("tool_rejected"), 2)
        self.assertIn("exploration_finalization_retry", event_types)
        self.assertIn("exploration_limit_reached", event_types)

    def test_explorer_bounds_large_tool_observations_sent_back_to_model(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "large.py").write_text("value = '" + "x" * 2_000 + "'\n")
            provider = MockLLMProvider(
                [
                    tool_response(
                        "plan",
                        "submit_plan",
                        {"steps": ["Read file"], "rationale": "Need evidence"},
                    ),
                    tool_response("read", "read_file", {"path": "large.py"}),
                    tool_response(
                        "finish",
                        "finish_exploration",
                        {
                            "summary": "Large file inspected",
                            "relevant_files": ["large.py"],
                            "findings": ["The observation was bounded"],
                        },
                    ),
                ]
            )
            recorder = InMemoryTrajectoryRecorder()
            state = MultiAgentOrchestrator(
                [
                    PlannerAgent(provider),
                    RepositoryExplorerAgent(
                        provider,
                        RepositoryInspector(root),
                        recorder=recorder,
                        maximum_tool_observation_characters=300,
                    ),
                ],
                recorder,
            ).run(Task("Inspect the large file"))

        observation = provider.requests[-1].messages[-1].content or ""
        self.assertFalse(state.failed)
        self.assertLessEqual(len(observation), 300)
        self.assertIn('"truncated": true', observation)
        self.assertIn(
            "tool_observation_truncated",
            [event.event_type for event in recorder.events],
        )
        self.assertTrue(
            any(
                entry.category == "repository_observation"
                for entry in state.memory.entries
            )
        )


class RepositoryInspectorTests(TestCase):
    def test_enforces_configured_read_path_policy(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "public.py").write_text("visible = True\n", encoding="utf-8")
            (root / "private.py").write_text("hidden = True\n", encoding="utf-8")
            inspector = RepositoryInspector(
                root, policy=SecurityPolicy(readable_paths=("public.py",))
            )

            self.assertEqual(inspector.list_files(), ["public.py"])
            self.assertIn("visible", inspector.read_file("public.py"))
            with self.assertRaisesRegex(RepositoryToolError, "allowlist"):
                inspector.read_file("private.py")

    def test_lists_reads_and_searches_text_files_while_ignoring_environment(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "src").mkdir()
            (root / "src" / "service.py").write_text(
                "def checkout():\n    return 'ok'\n", encoding="utf-8"
            )
            (root / ".venv").mkdir()
            (root / ".venv" / "secret.py").write_text("checkout = None", encoding="utf-8")
            inspector = RepositoryInspector(root)

            files = inspector.list_files()
            content = inspector.read_file("src/service.py")
            matches = inspector.search_code("CHECKOUT")

        self.assertEqual(files, ["src/service.py"])
        self.assertIn("return 'ok'", content)
        self.assertEqual(matches[0]["path"], "src/service.py")
        self.assertEqual(matches[0]["line"], 1)

    def test_rejects_paths_outside_the_repository(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            inspector = RepositoryInspector(Path(temporary_directory))

            with self.assertRaisesRegex(RepositoryToolError, "escapes"):
                inspector.read_file("../outside.txt")

    def test_hides_credentials_from_listing_and_direct_reads(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / ".env").write_text("HF_TOKEN=secret", encoding="utf-8")
            (root / "server.pem").write_text("private key", encoding="utf-8")
            (root / ".env.example").write_text("HF_TOKEN=placeholder", encoding="utf-8")
            inspector = RepositoryInspector(root)

            files = inspector.list_files()

            self.assertEqual(files, [".env.example"])
            with self.assertRaisesRegex(RepositoryToolError, "credential"):
                inspector.read_file(".env")
            with self.assertRaisesRegex(RepositoryToolError, "credential"):
                inspector.read_file("server.pem")
