from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from agentforge.agents.developer import DEVELOPMENT_ARTIFACT, DeveloperAgent
from agentforge.agents.explorer import EXPLORATION_ARTIFACT
from agentforge.agents.models import ExplorationReport, Plan
from agentforge.agents.planner import PLAN_ARTIFACT
from agentforge.core.models import Task, WorkflowState
from agentforge.llm.mock import MockLLMProvider
from agentforge.llm.models import ChatMessage, ChatResponse, ToolCall
from agentforge.telemetry.trajectory import InMemoryTrajectoryRecorder
from agentforge.tools.editor import RepositoryEditError, RepositoryEditor
from agentforge.tools.repository import RepositoryInspector


def tool_response(call_id: str, name: str, arguments: dict[str, object]) -> ChatResponse:
    return ChatResponse(
        ChatMessage(
            "assistant",
            None,
            tool_calls=(ToolCall(call_id, name, arguments),),
        ),
        "mock-coder",
    )


class UnifiedPatchTests(TestCase):
    def test_applies_standard_git_style_patch_and_preserves_crlf(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "module.py"
            target.write_bytes(b"alpha\r\nbeta\r\ngamma\r\n")
            editor = RepositoryEditor(RepositoryInspector(root))
            patch = (
                "diff --git a/module.py b/module.py\n"
                "--- a/module.py\n"
                "+++ b/module.py\n"
                "@@ -1,3 +1,3 @@\n"
                " alpha\n"
                "-beta\n"
                "+fixed\n"
                " gamma\n"
            )

            result = editor.apply_unified_patch(patch)

            self.assertEqual(target.read_bytes(), b"alpha\r\nfixed\r\ngamma\r\n")
            self.assertEqual(result["hunk_count"], 1)
            self.assertIn("-beta", editor.unified_diff())
            self.assertIn("+fixed", editor.unified_diff())

    def test_invalid_second_file_keeps_first_file_unchanged(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            first = root / "first.txt"
            second = root / "second.txt"
            first.write_text("one\n", encoding="utf-8")
            second.write_text("two\n", encoding="utf-8")
            editor = RepositoryEditor(RepositoryInspector(root))
            patch = (
                "--- a/first.txt\n+++ b/first.txt\n@@ -1 +1 @@\n-one\n+changed\n"
                "--- a/second.txt\n+++ b/second.txt\n@@ -1 +1 @@\n-missing\n+changed\n"
            )

            with self.assertRaisesRegex(RepositoryEditError, "does not match"):
                editor.apply_unified_patch(patch)

            self.assertEqual(first.read_text(encoding="utf-8"), "one\n")
            self.assertEqual(second.read_text(encoding="utf-8"), "two\n")

    def test_rejects_creation_deletion_and_renames(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "old.txt").write_text("old\n", encoding="utf-8")
            editor = RepositoryEditor(RepositoryInspector(root))

            with self.assertRaisesRegex(RepositoryEditError, "creation and deletion"):
                editor.apply_unified_patch("--- /dev/null\n+++ b/new.txt\n@@ -0,0 +1 @@\n+new\n")
            with self.assertRaisesRegex(RepositoryEditError, "renames"):
                editor.apply_unified_patch(
                    "--- a/old.txt\n+++ b/new.txt\n@@ -1 +1 @@\n-old\n+new\n"
                )

    def test_developer_can_use_unified_patch_tool(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "app.py"
            target.write_text("enabled = False\n", encoding="utf-8")
            editor = RepositoryEditor(RepositoryInspector(root))
            provider = MockLLMProvider(
                [
                    tool_response(
                        "patch",
                        "apply_unified_patch",
                        {
                            "patch": (
                                "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n"
                                "-enabled = False\n+enabled = True\n"
                            )
                        },
                    ),
                    tool_response(
                        "finish",
                        "finish_development",
                        {"summary": "Enabled the feature"},
                    ),
                ]
            )
            state = WorkflowState(Task("Enable the feature"))
            state.artifacts[PLAN_ARTIFACT] = Plan(("Edit app.py",), "Direct change")
            state.artifacts[EXPLORATION_ARTIFACT] = ExplorationReport(
                "Found flag", ("app.py",), ("Feature is disabled",)
            )

            result = DeveloperAgent(provider, editor).run(state)

            self.assertTrue(result.success)
            self.assertIn(DEVELOPMENT_ARTIFACT, result.artifacts)
            self.assertEqual(target.read_text(encoding="utf-8"), "enabled = True\n")

    def test_developer_gets_one_patch_correction_then_report_only_turn(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "app.py"
            target.write_bytes(b"enabled = False\r\n")
            editor = RepositoryEditor(RepositoryInspector(root))
            provider = MockLLMProvider(
                [
                    tool_response(
                        "bad-patch",
                        "apply_patch",
                        {
                            "changes": [
                                {
                                    "path": "app.py",
                                    "old_text": "missing = False",
                                    "new_text": "enabled = True",
                                }
                            ]
                        },
                    ),
                    tool_response(
                        "corrected-patch",
                        "apply_patch",
                        {
                            "changes": [
                                {
                                    "path": "app.py",
                                    "old_text": "enabled = False\n",
                                    "new_text": "enabled = True\n",
                                }
                            ]
                        },
                    ),
                    tool_response(
                        "finish",
                        "finish_development",
                        {"summary": "Enabled the feature", "changed_files": ["app.py"]},
                    ),
                ]
            )
            state = WorkflowState(Task("Enable the feature"))
            state.artifacts[PLAN_ARTIFACT] = Plan(("Edit app.py",), "Direct change")
            state.artifacts[EXPLORATION_ARTIFACT] = ExplorationReport(
                "Found flag", ("app.py",), ("Feature is disabled",)
            )
            recorder = InMemoryTrajectoryRecorder()

            result = DeveloperAgent(
                provider,
                editor,
                recorder=recorder,
                maximum_iterations=1,
            ).run(state)

            self.assertTrue(result.success)
            self.assertEqual(target.read_bytes(), b"enabled = True\r\n")
            self.assertEqual(
                tuple(tool.name for tool in provider.requests[1].tools),
                ("apply_patch", "apply_unified_patch"),
            )
            self.assertEqual(
                tuple(tool.name for tool in provider.requests[2].tools),
                ("finish_development",),
            )
            event_types = [event.event_type for event in recorder.events]
            self.assertIn("development_correction_retry", event_types)
            self.assertIn("development_finalization_required", event_types)

    def test_developer_stops_after_invalid_correction_retry(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "app.py"
            target.write_text("enabled = False\n", encoding="utf-8")
            editor = RepositoryEditor(RepositoryInspector(root))
            rejected_change = {
                "changes": [
                    {
                        "path": "app.py",
                        "old_text": "not present",
                        "new_text": "enabled = True",
                    }
                ]
            }
            provider = MockLLMProvider(
                [
                    tool_response("bad-patch", "apply_patch", rejected_change),
                    tool_response("bad-retry", "apply_patch", rejected_change),
                ]
            )
            state = WorkflowState(Task("Enable the feature"))
            state.artifacts[PLAN_ARTIFACT] = Plan(("Edit app.py",), "Direct change")
            state.artifacts[EXPLORATION_ARTIFACT] = ExplorationReport(
                "Found flag", ("app.py",), ("Feature is disabled",)
            )

            result = DeveloperAgent(
                provider,
                editor,
                maximum_iterations=1,
            ).run(state)

            self.assertFalse(result.success)
            self.assertEqual(len(provider.requests), 2)
            self.assertEqual(target.read_text(encoding="utf-8"), "enabled = False\n")

    def test_developer_finalizes_after_rejected_followup_to_a_valid_patch(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "app.py"
            target.write_text("enabled = False\n", encoding="utf-8")
            editor = RepositoryEditor(RepositoryInspector(root))
            provider = MockLLMProvider(
                [
                    tool_response(
                        "valid-patch",
                        "apply_patch",
                        {
                            "changes": [
                                {
                                    "path": "app.py",
                                    "old_text": "enabled = False",
                                    "new_text": "enabled = True",
                                }
                            ]
                        },
                    ),
                    tool_response(
                        "invalid-followup",
                        "apply_patch",
                        {
                            "changes": [
                                {
                                    "path": "",
                                    "old_text": "old",
                                    "new_text": "new",
                                }
                            ]
                        },
                    ),
                    tool_response(
                        "finish",
                        "finish_development",
                        {"summary": "Enabled the feature"},
                    ),
                ]
            )
            state = WorkflowState(Task("Enable the feature"))
            state.artifacts[PLAN_ARTIFACT] = Plan(("Edit app.py",), "Direct change")
            state.artifacts[EXPLORATION_ARTIFACT] = ExplorationReport(
                "Found flag", ("app.py",), ("Feature is disabled",)
            )

            result = DeveloperAgent(provider, editor).run(state)

            self.assertTrue(result.success)
            self.assertEqual(len(provider.requests), 3)
            self.assertEqual(
                tuple(tool.name for tool in provider.requests[-1].tools),
                ("finish_development",),
            )
            self.assertLess(len(provider.requests[-1].messages), 4)

    def test_developer_report_is_scoped_to_the_current_repair_attempt(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "shipping.py").write_text("free = False\n", encoding="utf-8")
            (root / "discounts.py").write_text("rounded = False\n", encoding="utf-8")
            editor = RepositoryEditor(RepositoryInspector(root))
            state = WorkflowState(Task("Repair checkout behavior"))
            state.artifacts[PLAN_ARTIFACT] = Plan(
                ("Repair both modules",), "Multiple attempts may be needed"
            )
            state.artifacts[EXPLORATION_ARTIFACT] = ExplorationReport(
                "Found checkout helpers",
                ("shipping.py", "discounts.py"),
                ("Both flags are incorrect",),
            )
            first_provider = MockLLMProvider(
                [
                    tool_response(
                        "shipping-patch",
                        "apply_patch",
                        {
                            "changes": [
                                {
                                    "path": "shipping.py",
                                    "old_text": "free = False",
                                    "new_text": "free = True",
                                }
                            ]
                        },
                    ),
                    tool_response(
                        "shipping-report",
                        "finish_development",
                        {
                            "summary": "Repaired shipping",
                            "changed_files": ["shipping.py"],
                        },
                    ),
                ]
            )
            second_provider = MockLLMProvider(
                [
                    tool_response(
                        "discount-patch",
                        "apply_patch",
                        {
                            "changes": [
                                {
                                    "path": "discounts.py",
                                    "old_text": "rounded = False",
                                    "new_text": "rounded = True",
                                }
                            ]
                        },
                    ),
                    tool_response(
                        "discount-report",
                        "finish_development",
                        {
                            "summary": "Repaired rounding",
                            "changed_files": ["shipping.py"],
                        },
                    ),
                ]
            )
            recorder = InMemoryTrajectoryRecorder()

            first = DeveloperAgent(first_provider, editor).run(state)
            second = DeveloperAgent(second_provider, editor, recorder=recorder).run(state)

            self.assertTrue(first.success)
            self.assertTrue(second.success)
            first_report = first.artifacts[DEVELOPMENT_ARTIFACT]
            second_report = second.artifacts[DEVELOPMENT_ARTIFACT]
            self.assertEqual(first_report.changed_files, ("shipping.py",))
            self.assertEqual(second_report.changed_files, ("discounts.py",))
            self.assertEqual(first_report.patch_count, 1)
            self.assertEqual(second_report.patch_count, 1)
            self.assertEqual(editor.changed_files, {"shipping.py", "discounts.py"})
            self.assertIn(
                "development_report_reconciled",
                [event.event_type for event in recorder.events],
            )
