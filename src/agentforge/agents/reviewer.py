"""Independent final review of machine evidence and the complete patch."""

from __future__ import annotations

from agentforge.agents.base import AgentResult
from agentforge.agents.developer import DEVELOPMENT_ARTIFACT
from agentforge.agents.errors import AgentProtocolError
from agentforge.agents.models import DevelopmentReport, ReviewReport, TestReport
from agentforge.agents.telemetry import record_agent_event
from agentforge.agents.tester import TEST_ARTIFACT
from agentforge.context.budget import budget_json_payload
from agentforge.core.models import WorkflowState
from agentforge.llm.base import LLMProvider
from agentforge.llm.models import ChatMessage, ToolDefinition
from agentforge.telemetry.trajectory import TrajectoryRecorder
from agentforge.tools.editor import RepositoryEditor

REVIEW_ARTIFACT = "review"
SUBMIT_REVIEW_TOOL = ToolDefinition(
    "submit_review",
    "Accept or reject the tested patch with concrete review findings.",
    {
        "type": "object",
        "properties": {
            "accepted": {"type": "boolean"},
            "summary": {"type": "string"},
            "issues": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["accepted", "summary", "issues"],
        "additionalProperties": False,
    },
)


class ReviewerAgent:
    name = "reviewer"

    def __init__(
        self,
        provider: LLMProvider,
        editor: RepositoryEditor,
        *,
        recorder: TrajectoryRecorder | None = None,
        maximum_context_characters: int = 30_000,
    ) -> None:
        if maximum_context_characters < 200:
            raise ValueError("Reviewer context budget must be at least 200 characters")
        self._provider = provider
        self._editor = editor
        self._recorder = recorder
        self._maximum_context_characters = maximum_context_characters

    def run(self, state: WorkflowState) -> AgentResult:
        development = state.artifacts.get(DEVELOPMENT_ARTIFACT)
        tests = state.artifacts.get(TEST_ARTIFACT)
        if not isinstance(development, DevelopmentReport):
            raise AgentProtocolError("Reviewer requires a DevelopmentReport")
        if not isinstance(tests, TestReport) or not tests.passed:
            raise AgentProtocolError("Reviewer requires passing machine-verified tests")
        diff = self._editor.unified_diff()
        if not diff:
            raise AgentProtocolError("Reviewer requires a non-empty effective diff")

        memory = state.memory.render(
            state.task.description,
            maximum_characters=max(1, self._maximum_context_characters // 4),
        )
        review_payload = {
            "task": state.task.description,
            "development_summary": development.summary,
            "test_summary": tests.summary,
            "test_command": tests.command,
            "run_memory": memory.text,
            "omitted_memory_entries": memory.omitted_entries,
            "diff": diff,
        }
        context = budget_json_payload(
            review_payload, self._maximum_context_characters
        )
        if context.truncated:
            record_agent_event(
                self._recorder,
                state,
                self.name,
                "review_context_rejected",
                {
                    "original_characters": context.original_characters,
                    "maximum_characters": self._maximum_context_characters,
                },
            )
            raise AgentProtocolError(
                "Complete review evidence exceeds the configured context budget"
            )

        messages = (
            ChatMessage(
                "system",
                "You are the independent Reviewer. Check that the complete diff addresses "
                "the task and is supported by passing test evidence. Submit one verdict.",
            ),
            ChatMessage(
                "user",
                context.text,
            ),
        )
        record_agent_event(
            self._recorder,
            state,
            self.name,
            "llm_requested",
            {"message_count": len(messages), "diff_bytes": len(diff.encode("utf-8"))},
        )
        response = self._provider.chat(
            messages, tools=(SUBMIT_REVIEW_TOOL,), tool_choice="required"
        )
        record_agent_event(
            self._recorder,
            state,
            self.name,
            "llm_responded",
            {
                "model": response.model,
                "tool_call_count": len(response.message.tool_calls),
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "total_tokens": response.usage.total_tokens,
            },
        )
        if len(response.message.tool_calls) != 1:
            raise AgentProtocolError("Reviewer must submit exactly one verdict")
        call = response.message.tool_calls[0]
        if call.name != SUBMIT_REVIEW_TOOL.name:
            raise AgentProtocolError(f"Reviewer called unexpected tool '{call.name}'")

        accepted = call.arguments.get("accepted")
        summary = call.arguments.get("summary")
        issues = call.arguments.get("issues")
        if not isinstance(accepted, bool):
            raise AgentProtocolError("Review accepted value must be boolean")
        if not isinstance(summary, str) or not summary.strip():
            raise AgentProtocolError("Review summary must be non-empty text")
        if not isinstance(issues, list) or not all(
            isinstance(issue, str) and issue.strip() for issue in issues
        ):
            raise AgentProtocolError("Review issues must be a list of non-empty text")
        if not accepted and not issues:
            raise AgentProtocolError("A rejected review must explain at least one issue")

        report = ReviewReport(
            accepted,
            summary.strip(),
            tuple(issue.strip() for issue in issues),
            diff,
        )
        record_agent_event(
            self._recorder,
            state,
            self.name,
            "review_completed",
            {
                "accepted": accepted,
                "issue_count": len(report.issues),
                "summary": report.summary,
                "issues": list(report.issues),
            },
        )
        return AgentResult(
            accepted,
            report.summary,
            {REVIEW_ARTIFACT: report},
        )
