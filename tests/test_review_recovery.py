from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from agentforge.agents.errors import AgentProtocolError
from agentforge.agents.models import DevelopmentReport
from agentforge.agents.models import TestReport as EvidenceReport
from agentforge.agents.reviewer import ReviewerAgent
from agentforge.core.models import Task, WorkflowState
from agentforge.llm.mock import MockLLMProvider
from agentforge.llm.models import ChatMessage, ChatResponse, ToolCall
from agentforge.tools.editor import RepositoryEditor
from agentforge.tools.repository import RepositoryInspector


class ReviewRecoveryTests(TestCase):
    def test_invalid_issues_get_one_correction_with_same_evidence(self) -> None:
        for final_issues in ([], [""], ["Missing edge case"]):
            with self.subTest(issues=final_issues), TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "app.py").write_text("value = 1\n")
                editor = RepositoryEditor(RepositoryInspector(root))
                editor.apply_patch({"changes": [
                    {"path": "app.py", "old_text": "1", "new_text": "2"}
                ]})
                state = WorkflowState(Task("Update value"))
                state.artifacts["development"] = DevelopmentReport("Updated", ("app.py",), 1)
                state.artifacts["tests"] = EvidenceReport(
                    True, "Passed", ("python", "spec.py"), 0, False, "OK", ""
                )
                responses = []
                for index, issues in enumerate(([""], final_issues)):
                    responses.append(ChatResponse(ChatMessage("assistant", None, tool_calls=(
                        ToolCall(str(index), "submit_review", {
                            "accepted": not bool(final_issues), "summary": "Reviewed",
                            "issues": issues,
                        }),
                    )), "mock"))
                provider = MockLLMProvider(responses)
                reviewer = ReviewerAgent(provider, editor)
                if final_issues == [""]:
                    with self.assertRaises(AgentProtocolError):
                        reviewer.run(state)
                else:
                    result = reviewer.run(state)
                    self.assertEqual(result.success, not bool(final_issues))
                self.assertEqual(len(provider.requests), 2)
                self.assertEqual(provider.requests[0].messages[1],
                                 provider.requests[1].messages[1])
                self.assertEqual([tool.name for tool in provider.requests[1].tools],
                                 ["submit_review"])
