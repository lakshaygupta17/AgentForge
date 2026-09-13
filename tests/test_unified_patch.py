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
                editor.apply_unified_patch(
                    "--- /dev/null\n+++ b/new.txt\n@@ -0,0 +1 @@\n+new\n"
                )
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
                        {"summary": "Enabled the feature", "changed_files": ["app.py"]},
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

