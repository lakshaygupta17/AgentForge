"""Developer agent limited to grounded reads and validated text patches."""

from __future__ import annotations

import json
from typing import Any

from agentforge.agents.base import AgentResult
from agentforge.agents.errors import AgentProtocolError
from agentforge.agents.explorer import EXPLORATION_ARTIFACT
from agentforge.agents.models import DevelopmentReport, Diagnosis, ExplorationReport, Plan
from agentforge.agents.planner import PLAN_ARTIFACT
from agentforge.agents.telemetry import record_agent_event
from agentforge.core.models import WorkflowState
from agentforge.llm.base import LLMProvider
from agentforge.llm.models import ChatMessage, ToolCall, ToolDefinition
from agentforge.telemetry.trajectory import TrajectoryRecorder
from agentforge.tools.editor import RepositoryEditError, RepositoryEditor
from agentforge.tools.repository import RepositoryToolError

DEVELOPMENT_ARTIFACT = "development"
DEVELOPER_READ_TOOL = ToolDefinition(
    "read_file",
    "Read one repository text file before proposing a change.",
    {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
        "additionalProperties": False,
    },
)
APPLY_PATCH_TOOL = ToolDefinition(
    "apply_patch",
    "Atomically apply exact text replacements to existing repository files.",
    {
        "type": "object",
        "properties": {
            "changes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "old_text": {"type": "string"},
                        "new_text": {"type": "string"},
                    },
                    "required": ["path", "old_text", "new_text"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["changes"],
        "additionalProperties": False,
    },
)
APPLY_UNIFIED_PATCH_TOOL = ToolDefinition(
    "apply_unified_patch",
    "Apply a standard unified diff to existing repository text files.",
    {
        "type": "object",
        "properties": {"patch": {"type": "string"}},
        "required": ["patch"],
        "additionalProperties": False,
    },
)
FINISH_DEVELOPMENT_TOOL = ToolDefinition(
    "finish_development",
    "Finish after applying the intended patch and summarize the changes.",
    {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "changed_files": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["summary", "changed_files"],
        "additionalProperties": False,
    },
)
DEVELOPER_TOOLS = (
    DEVELOPER_READ_TOOL,
    APPLY_PATCH_TOOL,
    APPLY_UNIFIED_PATCH_TOOL,
    FINISH_DEVELOPMENT_TOOL,
)


class DeveloperAgent:
    name = "developer"

    def __init__(
        self,
        provider: LLMProvider,
        editor: RepositoryEditor,
        *,
        recorder: TrajectoryRecorder | None = None,
        maximum_iterations: int = 8,
        maximum_context_characters: int = 6_000,
    ) -> None:
        if maximum_iterations <= 0:
            raise ValueError("Maximum developer iterations must be greater than zero")
        if maximum_context_characters <= 0:
            raise ValueError("Developer context budget must be greater than zero")
        self._provider = provider
        self._editor = editor
        self._recorder = recorder
        self._maximum_iterations = maximum_iterations
        self._maximum_context_characters = maximum_context_characters

    def run(self, state: WorkflowState) -> AgentResult:
        plan = state.artifacts.get(PLAN_ARTIFACT)
        exploration = state.artifacts.get(EXPLORATION_ARTIFACT)
        if not isinstance(plan, Plan) or not isinstance(exploration, ExplorationReport):
            raise AgentProtocolError("Developer requires Plan and ExplorationReport artifacts")
        starting_patch_count = self._editor.patch_count
        diagnosis = state.artifacts.get("diagnosis")
        memory = state.memory.render(
            state.task.description,
            maximum_characters=self._maximum_context_characters,
        )

        messages: list[ChatMessage] = [
            ChatMessage(
                "system",
                "You are the Developer. Make the smallest grounded change that addresses "
                "the task. You may only read files and submit validated exact replacements "
                "or unified patches. Never claim a change that a patch tool did not confirm.",
            ),
            ChatMessage(
                "user",
                "Task: "
                + state.task.description
                + "\nPlan: "
                + json.dumps(list(plan.steps))
                + "\nExploration: "
                + json.dumps(
                    {
                        "summary": exploration.summary,
                        "relevant_files": exploration.relevant_files,
                        "findings": exploration.findings,
                    }
                )
                + (
                    "\nDebugger diagnosis: "
                    + json.dumps(
                        {
                            "hypothesis": diagnosis.hypothesis,
                            "evidence": diagnosis.evidence,
                            "recommended_changes": diagnosis.recommended_changes,
                        }
                    )
                    if isinstance(diagnosis, Diagnosis)
                    else ""
                )
                + ("\nRun memory:\n" + memory.text if memory.text else ""),
            ),
        ]

        for iteration in range(1, self._maximum_iterations + 1):
            record_agent_event(
                self._recorder,
                state,
                self.name,
                "llm_requested",
                {"iteration": iteration, "message_count": len(messages)},
            )
            response = self._provider.chat(
                messages, tools=DEVELOPER_TOOLS, tool_choice="required"
            )
            calls = response.message.tool_calls
            record_agent_event(
                self._recorder,
                state,
                self.name,
                "llm_responded",
                {
                    "iteration": iteration,
                    "model": response.model,
                    "tool_call_count": len(calls),
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                    "total_tokens": response.usage.total_tokens,
                },
            )
            if not calls:
                raise AgentProtocolError("Developer must call a tool")
            messages.append(response.message)

            finish_calls = [call for call in calls if call.name == FINISH_DEVELOPMENT_TOOL.name]
            if finish_calls:
                if len(calls) != 1:
                    raise AgentProtocolError(
                        "finish_development cannot be combined with another tool call"
                    )
                return self._finish(state, finish_calls[0], starting_patch_count)

            for call in calls:
                messages.append(self._execute_tool(state, iteration, call))

        return AgentResult(
            False,
            "Developer reached its iteration limit without submitting a change report",
        )

    def _execute_tool(
        self, state: WorkflowState, iteration: int, call: ToolCall
    ) -> ChatMessage:
        record_agent_event(
            self._recorder,
            state,
            self.name,
            "tool_requested",
            {"iteration": iteration, "tool": call.name},
        )
        try:
            if call.name == DEVELOPER_READ_TOOL.name:
                path = call.arguments.get("path")
                if not isinstance(path, str):
                    raise RepositoryToolError("'path' must be text")
                result: object = self._editor.inspector.read_file(path)
            elif call.name == APPLY_PATCH_TOOL.name:
                result = self._editor.apply_patch(call.arguments)
            elif call.name == APPLY_UNIFIED_PATCH_TOOL.name:
                patch_text = call.arguments.get("patch")
                if not isinstance(patch_text, str):
                    raise RepositoryEditError("Unified patch must be text")
                result = self._editor.apply_unified_patch(patch_text)
            else:
                raise RepositoryEditError(f"Unknown developer tool '{call.name}'")
            payload: dict[str, Any] = {"ok": True, "result": result}
            event_type = (
                "patch_applied"
                if call.name in {APPLY_PATCH_TOOL.name, APPLY_UNIFIED_PATCH_TOOL.name}
                else "tool_completed"
            )
            record_agent_event(
                self._recorder,
                state,
                self.name,
                event_type,
                {"iteration": iteration, "tool": call.name},
            )
        except (RepositoryEditError, RepositoryToolError) as error:
            payload = {"ok": False, "error": str(error)}
            record_agent_event(
                self._recorder,
                state,
                self.name,
                "tool_rejected",
                {"iteration": iteration, "tool": call.name, "reason": str(error)},
            )
        return ChatMessage(
            "tool", json.dumps(payload, ensure_ascii=False), tool_call_id=call.id
        )

    def _finish(
        self, state: WorkflowState, call: ToolCall, starting_patch_count: int
    ) -> AgentResult:
        summary = call.arguments.get("summary")
        raw_files = call.arguments.get("changed_files")
        if not isinstance(summary, str) or not summary.strip():
            raise AgentProtocolError("Development summary must be non-empty text")
        if not isinstance(raw_files, list) or not all(
            isinstance(path, str) and path.strip() for path in raw_files
        ):
            raise AgentProtocolError("changed_files must be a list of paths")
        reported_files = {path.strip() for path in raw_files}
        if self._editor.patch_count == starting_patch_count:
            raise AgentProtocolError("Developer finished without an applied patch")
        if not self._editor.changed_files:
            raise AgentProtocolError("Developer's patches leave no effective changes")
        if reported_files != self._editor.changed_files:
            raise AgentProtocolError(
                "Developer report does not match the files changed by apply_patch"
            )

        report = DevelopmentReport(
            summary=summary.strip(),
            changed_files=tuple(sorted(reported_files)),
            patch_count=self._editor.patch_count,
        )
        record_agent_event(
            self._recorder,
            state,
            self.name,
            "development_completed",
            {
                "changed_file_count": len(report.changed_files),
                "patch_count": report.patch_count,
                "summary": report.summary,
                "changed_files": list(report.changed_files),
            },
        )
        return AgentResult(
            True,
            f"Developer changed {len(report.changed_files)} files",
            {DEVELOPMENT_ARTIFACT: report},
        )
