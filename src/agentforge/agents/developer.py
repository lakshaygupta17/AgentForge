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
    "Finish after applying the intended patch and summarize the changes. AgentForge "
    "derives changed files from confirmed patch-tool evidence.",
    {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "changed_files": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["summary"],
        "additionalProperties": False,
    },
)
DEVELOPER_TOOLS = (
    DEVELOPER_READ_TOOL,
    APPLY_PATCH_TOOL,
    APPLY_UNIFIED_PATCH_TOOL,
    FINISH_DEVELOPMENT_TOOL,
)
EDIT_TOOL_NAMES = frozenset({APPLY_PATCH_TOOL.name, APPLY_UNIFIED_PATCH_TOOL.name})


class DeveloperAgent:
    name = "developer"

    def __init__(
        self,
        provider: LLMProvider,
        editor: RepositoryEditor,
        *,
        recorder: TrajectoryRecorder | None = None,
        maximum_iterations: int = 20,
        maximum_context_characters: int = 6_000,
        maximum_successful_edit_calls: int = 3,
    ) -> None:
        if maximum_iterations <= 0:
            raise ValueError("Maximum developer iterations must be greater than zero")
        if maximum_context_characters <= 0:
            raise ValueError("Developer context budget must be greater than zero")
        if maximum_successful_edit_calls <= 0:
            raise ValueError("Maximum successful edit calls must be greater than zero")
        self._provider = provider
        self._editor = editor
        self._recorder = recorder
        self._maximum_iterations = maximum_iterations
        self._maximum_context_characters = maximum_context_characters
        self._maximum_successful_edit_calls = maximum_successful_edit_calls

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
                "or unified patches. Treat the exploration report as leads rather than an "
                "exhaustive file list: trace imported helpers when the behavior crosses "
                "modules. Prefer small exact replacements over hand-counted unified hunks. "
                "After a rejected edit, reread the exact current file before retrying. Never "
                "claim a change that a patch tool did not confirm.",
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

        rejected_edit = False
        successful_edit_calls = 0
        finalization_requested = False
        attempt_changed_files: set[str] = set()
        for iteration in range(1, self._maximum_iterations + 1):
            calls = self._request_tools(state, iteration, messages, DEVELOPER_TOOLS)

            finish_calls = [call for call in calls if call.name == FINISH_DEVELOPMENT_TOOL.name]
            if finish_calls:
                if len(calls) != 1:
                    raise AgentProtocolError(
                        "finish_development cannot be combined with another tool call"
                    )
                return self._finish(
                    state,
                    finish_calls[0],
                    starting_patch_count,
                    attempt_changed_files,
                )

            for call in calls:
                tool_message, succeeded, changed_files = self._execute_tool(state, iteration, call)
                messages.append(tool_message)
                attempt_changed_files.update(changed_files)
                if call.name in EDIT_TOOL_NAMES and succeeded:
                    successful_edit_calls += 1
                if call.name in EDIT_TOOL_NAMES and not succeeded:
                    rejected_edit = True
                    messages.append(
                        ChatMessage(
                            "system",
                            "The previous edit was rejected and changed nothing. Reread the "
                            "target file, copy the smallest unique current text exactly, and "
                            "retry with apply_patch. Do not repeat a rejected patch.",
                        )
                    )
                    if self._editor.patch_count > starting_patch_count:
                        finalization_requested = True

            if self._editor.patch_count > starting_patch_count and (
                finalization_requested
                or successful_edit_calls >= self._maximum_successful_edit_calls
            ):
                break

        next_iteration = self._maximum_iterations + 1
        if self._editor.patch_count == starting_patch_count and rejected_edit:
            messages.append(
                ChatMessage(
                    "system",
                    "All regular development turns are exhausted. This is one bounded "
                    "correction-only retry. Using file content already observed, submit "
                    "exactly one minimal apply_patch or apply_unified_patch call. No read or "
                    "report tool is available on this turn.",
                )
            )
            record_agent_event(
                self._recorder,
                state,
                self.name,
                "development_correction_retry",
                {"iteration": next_iteration},
            )
            calls = self._request_tools(
                state,
                next_iteration,
                messages,
                (APPLY_PATCH_TOOL, APPLY_UNIFIED_PATCH_TOOL),
            )
            if len(calls) == 1 and calls[0].name in EDIT_TOOL_NAMES:
                tool_message, _, changed_files = self._execute_tool(state, next_iteration, calls[0])
                messages.append(tool_message)
                attempt_changed_files.update(changed_files)
            else:
                self._reject_unavailable_calls(
                    state,
                    next_iteration,
                    calls,
                    "Correction permits exactly one patch tool call",
                )
            next_iteration += 1

        if self._editor.patch_count > starting_patch_count:
            finalization_messages = [
                ChatMessage(
                    "system",
                    "A validated patch has been applied. This is the report-only finalization "
                    "turn. Call finish_development exactly once with a concise summary. "
                    "AgentForge will attach the files confirmed by the patch tool.",
                ),
                ChatMessage(
                    "user",
                    json.dumps(
                        {
                            "confirmed_changed_files": sorted(attempt_changed_files),
                            "confirmed_patch_calls": successful_edit_calls,
                        }
                    ),
                ),
            ]
            record_agent_event(
                self._recorder,
                state,
                self.name,
                "development_finalization_required",
                {"iteration": next_iteration},
            )
            calls = self._request_tools(
                state,
                next_iteration,
                finalization_messages,
                (FINISH_DEVELOPMENT_TOOL,),
            )
            if len(calls) == 1 and calls[0].name == FINISH_DEVELOPMENT_TOOL.name:
                return self._finish(
                    state,
                    calls[0],
                    starting_patch_count,
                    attempt_changed_files,
                )
            self._reject_unavailable_calls(
                state,
                next_iteration,
                calls,
                "Finalization permits exactly one finish_development call",
            )

        return AgentResult(
            False,
            "Developer reached its iteration limit without submitting a change report",
        )

    def _request_tools(
        self,
        state: WorkflowState,
        iteration: int,
        messages: list[ChatMessage],
        tools: tuple[ToolDefinition, ...],
    ) -> tuple[ToolCall, ...]:
        record_agent_event(
            self._recorder,
            state,
            self.name,
            "llm_requested",
            {"iteration": iteration, "message_count": len(messages)},
        )
        response = self._provider.chat(messages, tools=tools, tool_choice="required")
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
        return calls

    def _reject_unavailable_calls(
        self,
        state: WorkflowState,
        iteration: int,
        calls: tuple[ToolCall, ...],
        reason: str,
    ) -> None:
        for call in calls:
            record_agent_event(
                self._recorder,
                state,
                self.name,
                "tool_requested",
                {"iteration": iteration, "tool": call.name},
            )
            record_agent_event(
                self._recorder,
                state,
                self.name,
                "tool_rejected",
                {"iteration": iteration, "tool": call.name, "reason": reason},
            )

    def _execute_tool(
        self, state: WorkflowState, iteration: int, call: ToolCall
    ) -> tuple[ChatMessage, bool, frozenset[str]]:
        record_agent_event(
            self._recorder,
            state,
            self.name,
            "tool_requested",
            {"iteration": iteration, "tool": call.name},
        )
        confirmed_files: frozenset[str] = frozenset()
        try:
            if call.name == DEVELOPER_READ_TOOL.name:
                path = call.arguments.get("path")
                if not isinstance(path, str):
                    raise RepositoryToolError("'path' must be text")
                result: object = self._editor.inspector.read_file(path)
            elif call.name == APPLY_PATCH_TOOL.name:
                patch_result = self._editor.apply_patch(call.arguments)
                result = patch_result
                confirmed_files = self._patch_result_files(patch_result)
            elif call.name == APPLY_UNIFIED_PATCH_TOOL.name:
                patch_text = call.arguments.get("patch")
                if not isinstance(patch_text, str):
                    raise RepositoryEditError("Unified patch must be text")
                patch_result = self._editor.apply_unified_patch(patch_text)
                result = patch_result
                confirmed_files = self._patch_result_files(patch_result)
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
        return (
            ChatMessage("tool", json.dumps(payload, ensure_ascii=False), tool_call_id=call.id),
            bool(payload["ok"]),
            confirmed_files,
        )

    @staticmethod
    def _patch_result_files(result: dict[str, object]) -> frozenset[str]:
        raw_files = result.get("changed_files")
        if not isinstance(raw_files, list) or not all(
            isinstance(path, str) and path for path in raw_files
        ):
            raise RepositoryEditError("Patch tool returned invalid changed_files evidence")
        return frozenset(raw_files)

    def _finish(
        self,
        state: WorkflowState,
        call: ToolCall,
        starting_patch_count: int,
        attempt_changed_files: set[str],
    ) -> AgentResult:
        summary = call.arguments.get("summary")
        raw_files = call.arguments.get("changed_files")
        if not isinstance(summary, str) or not summary.strip():
            raise AgentProtocolError("Development summary must be non-empty text")
        if self._editor.patch_count == starting_patch_count:
            raise AgentProtocolError("Developer finished without an applied patch")
        if not attempt_changed_files:
            raise AgentProtocolError("Developer has no confirmed files for this attempt")
        if isinstance(raw_files, list):
            reported_files = {
                path.strip() for path in raw_files if isinstance(path, str) and path.strip()
            }
            if reported_files != attempt_changed_files:
                record_agent_event(
                    self._recorder,
                    state,
                    self.name,
                    "development_report_reconciled",
                    {
                        "reported_file_count": len(reported_files),
                        "confirmed_file_count": len(attempt_changed_files),
                    },
                )

        report = DevelopmentReport(
            summary=summary.strip(),
            changed_files=tuple(sorted(attempt_changed_files)),
            patch_count=self._editor.patch_count - starting_patch_count,
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
