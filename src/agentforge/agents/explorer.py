"""Repository Explorer agent with a bounded read-only tool loop."""

from __future__ import annotations

import json
from typing import Any

from agentforge.agents.base import AgentResult
from agentforge.agents.errors import AgentProtocolError
from agentforge.agents.models import ExplorationReport, Plan
from agentforge.agents.planner import PLAN_ARTIFACT
from agentforge.agents.telemetry import record_agent_event
from agentforge.context.budget import budget_json_payload
from agentforge.core.models import WorkflowState
from agentforge.llm.base import LLMProvider
from agentforge.llm.models import ChatMessage, ToolCall, ToolDefinition
from agentforge.telemetry.trajectory import TrajectoryRecorder
from agentforge.tools.index import PythonSymbolIndex
from agentforge.tools.repository import RepositoryInspector, RepositoryToolError
from agentforge.tools.semantic import RepositoryEmbeddingIndex

EXPLORATION_ARTIFACT = "exploration"
LIST_FILES_TOOL = ToolDefinition(
    "list_files",
    "List repository files recursively below a relative directory.",
    {
        "type": "object",
        "properties": {"path": {"type": "string", "default": "."}},
        "additionalProperties": False,
    },
)
READ_FILE_TOOL = ToolDefinition(
    "read_file",
    "Read one text file using a repository-relative path.",
    {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
        "additionalProperties": False,
    },
)
SEARCH_CODE_TOOL = ToolDefinition(
    "search_code",
    "Find case-insensitive text matches in repository files.",
    {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "path": {"type": "string", "default": "."},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
)
FIND_SYMBOL_TOOL = ToolDefinition(
    "find_symbol",
    "Find Python classes, functions, or methods by parsed symbol name.",
    {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "kind": {
                "type": "string",
                "enum": ["class", "function", "method"],
            },
            "path": {"type": "string", "default": "."},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
)
SEMANTIC_SEARCH_TOOL = ToolDefinition(
    "semantic_search",
    "Rank repository files by vector similarity to a natural-language query.",
    {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "path": {"type": "string", "default": "."},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
)
FINISH_EXPLORATION_TOOL = ToolDefinition(
    "finish_exploration",
    "Submit grounded repository findings after enough inspection.",
    {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "relevant_files": {"type": "array", "items": {"type": "string"}},
            "findings": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["summary", "relevant_files", "findings"],
        "additionalProperties": False,
    },
)
EXPLORER_TOOLS = (
    LIST_FILES_TOOL,
    READ_FILE_TOOL,
    SEARCH_CODE_TOOL,
    FIND_SYMBOL_TOOL,
    FINISH_EXPLORATION_TOOL,
)


class RepositoryExplorerAgent:
    name = "repository_explorer"

    def __init__(
        self,
        provider: LLMProvider,
        inspector: RepositoryInspector,
        *,
        recorder: TrajectoryRecorder | None = None,
        maximum_iterations: int = 12,
        maximum_tool_observation_characters: int = 20_000,
        embedding_index: RepositoryEmbeddingIndex | None = None,
    ) -> None:
        if maximum_iterations <= 0:
            raise ValueError("Maximum explorer iterations must be greater than zero")
        if maximum_tool_observation_characters < 200:
            raise ValueError("Explorer observation budget must be at least 200 characters")
        self._provider = provider
        self._inspector = inspector
        self._symbol_index = PythonSymbolIndex(inspector)
        self._embedding_index = embedding_index
        self._tools = (
            EXPLORER_TOOLS
            if embedding_index is None
            else (*EXPLORER_TOOLS[:-1], SEMANTIC_SEARCH_TOOL, EXPLORER_TOOLS[-1])
        )
        self._recorder = recorder
        self._maximum_iterations = maximum_iterations
        self._maximum_tool_observation_characters = maximum_tool_observation_characters

    def run(self, state: WorkflowState) -> AgentResult:
        plan = state.artifacts.get(PLAN_ARTIFACT)
        if not isinstance(plan, Plan):
            raise AgentProtocolError("Repository Explorer requires a validated Plan artifact")

        messages: list[ChatMessage] = [
            ChatMessage(
                "system",
                "You are the Repository Explorer. Use only the supplied read-only tools. "
                "Ground every finding in files you actually inspect. Never invent paths. "
                "Trace imports and function calls from the primary implementation into "
                "helper modules, and inspect the task brief and tests when present. Include "
                "every inspected file that may require a change in relevant_files; do not "
                "assume the first failing or coordinating file contains every defect. "
                "Finish with exactly one finish_exploration call.",
            ),
            ChatMessage(
                "user",
                "Task: " + state.task.description + "\nPlan: " + json.dumps(list(plan.steps)),
            ),
        ]
        observations: list[str] = []
        seen_tool_requests: set[str] = set()
        verified_paths: set[str] = set()
        rejected_report_paths: set[str] = set()
        valid_report_candidates: set[str] = set()
        duplicate_requests = 0
        finalization_attempts = 0

        finalization_retry_iteration = self._maximum_iterations + 1
        for iteration in range(1, finalization_retry_iteration + 1):
            tools: tuple[ToolDefinition, ...] = self._tools
            finalization_required = iteration >= self._maximum_iterations or duplicate_requests >= 2
            if finalization_required:
                if finalization_attempts >= 2:
                    break
                finalization_attempts += 1
                if finalization_attempts == 1:
                    instruction = (
                        "This is the final exploration turn. Stop inspecting and call "
                        "finish_exploration now using only evidence already observed."
                    )
                    event_type = "exploration_finalization_required"
                else:
                    instruction = (
                        "Your previous finalization response did not call exactly one "
                        "finish_exploration tool. This is the single correction retry. "
                        "Call finish_exploration now; no other tool is permitted."
                    )
                    event_type = "exploration_finalization_retry"
                messages.append(
                    ChatMessage(
                        "system",
                        instruction,
                    )
                )
                tools = (FINISH_EXPLORATION_TOOL,)
                record_agent_event(
                    self._recorder,
                    state,
                    self.name,
                    event_type,
                    {"iteration": iteration},
                )
            request_messages = messages
            if finalization_required:
                correction = (
                    {
                        "allowed_relevant_files": sorted(verified_paths | valid_report_candidates),
                        "forbidden_nonexistent_files": sorted(rejected_report_paths),
                        "instruction": (
                            "Use only allowed_relevant_files in relevant_files. "
                            "Do not repeat any forbidden_nonexistent_files."
                        ),
                    }
                    if finalization_attempts > 1 and rejected_report_paths
                    else None
                )
                evidence = budget_json_payload(
                    (
                        {
                            "observations": observations,
                            "report_correction": correction,
                        }
                        if correction is not None
                        else {
                            "task": state.task.description,
                            "plan": list(plan.steps),
                            "observations": observations,
                        }
                    ),
                    self._maximum_tool_observation_characters,
                )
                request_messages = [
                    ChatMessage(
                        "system",
                        "You are the Repository Explorer in report-only mode. Using only "
                        "the supplied repository observations, call finish_exploration "
                        "exactly once. No inspection tool is available. Do not request "
                        "more evidence and never invent a path.",
                    ),
                    ChatMessage("user", evidence.text),
                    ChatMessage("system", instruction),
                ]
                if evidence.truncated:
                    record_agent_event(
                        self._recorder,
                        state,
                        self.name,
                        "exploration_finalization_context_truncated",
                        {
                            "iteration": iteration,
                            "original_characters": evidence.original_characters,
                            "included_characters": len(evidence.text),
                        },
                    )
            record_agent_event(
                self._recorder,
                state,
                self.name,
                "llm_requested",
                {"iteration": iteration, "message_count": len(request_messages)},
            )
            response = self._provider.chat(
                request_messages,
                tools=tools,
                tool_choice="required",
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
            if not calls and not finalization_required:
                raise AgentProtocolError("Repository Explorer must call a tool")
            if not finalization_required:
                messages.append(response.message)

            finish_calls = [call for call in calls if call.name == FINISH_EXPLORATION_TOOL.name]
            if finish_calls:
                if len(calls) != 1:
                    if not finalization_required:
                        raise AgentProtocolError(
                            "finish_exploration cannot be combined with another tool call"
                        )
                else:
                    try:
                        report = self._build_report(finish_calls[0])
                    except AgentProtocolError as error:
                        reason = str(error)
                        valid_paths, invalid_paths = self._partition_report_paths(finish_calls[0])
                        valid_report_candidates.update(valid_paths)
                        rejected_report_paths.update(invalid_paths)
                        record_agent_event(
                            self._recorder,
                            state,
                            self.name,
                            "tool_rejected",
                            {
                                "iteration": iteration,
                                "tool": finish_calls[0].name,
                                "reason": reason,
                            },
                        )
                        observations.append(
                            json.dumps(
                                {"invalid_exploration_report": reason},
                                ensure_ascii=False,
                            )
                        )
                        if not finalization_required:
                            messages.append(
                                ChatMessage(
                                    "tool",
                                    json.dumps({"ok": False, "error": reason}),
                                    tool_call_id=finish_calls[0].id,
                                )
                            )
                            duplicate_requests = 2
                        continue
                    state.memory.remember(
                        "exploration",
                        self.name,
                        report.summary + "\n" + "\n".join(report.findings),
                        priority=90,
                    )
                    record_agent_event(
                        self._recorder,
                        state,
                        self.name,
                        "exploration_completed",
                        {
                            "iteration": iteration,
                            "relevant_file_count": len(report.relevant_files),
                            "finding_count": len(report.findings),
                            "summary": report.summary,
                            "relevant_files": list(report.relevant_files),
                            "findings": list(report.findings),
                        },
                    )
                    return AgentResult(
                        success=True,
                        summary=(
                            "Repository Explorer identified "
                            f"{len(report.relevant_files)} relevant files"
                        ),
                        artifacts={EXPLORATION_ARTIFACT: report},
                    )

            if finalization_required:
                for call in calls:
                    self._reject_finalization_tool(state, iteration, call)
                continue

            for call in calls:
                request_key = json.dumps(
                    {"tool": call.name, "arguments": call.arguments},
                    sort_keys=True,
                    separators=(",", ":"),
                )
                if request_key in seen_tool_requests:
                    duplicate_requests += 1
                    reason = "Duplicate repository tool request; use existing evidence"
                    record_agent_event(
                        self._recorder,
                        state,
                        self.name,
                        "tool_rejected",
                        {"iteration": iteration, "tool": call.name, "reason": reason},
                    )
                    tool_message = ChatMessage(
                        "tool",
                        json.dumps({"ok": False, "error": reason}),
                        tool_call_id=call.id,
                    )
                else:
                    seen_tool_requests.add(request_key)
                    tool_message = self._execute_tool(state, iteration, call)
                    if call.name == READ_FILE_TOOL.name:
                        try:
                            outcome = json.loads(tool_message.content or "{}")
                        except json.JSONDecodeError:
                            outcome = {}
                        path = call.arguments.get("path")
                        if outcome.get("ok") is True and isinstance(path, str):
                            verified_paths.add(path)
                messages.append(tool_message)
                observations.append(
                    json.dumps(
                        {
                            "tool": call.name,
                            "arguments": call.arguments,
                            "result": tool_message.content,
                        },
                        ensure_ascii=False,
                    )
                )

        record_agent_event(
            self._recorder,
            state,
            self.name,
            "exploration_limit_reached",
            {
                "maximum_iterations": self._maximum_iterations,
                "finalization_attempts": 2,
            },
        )
        return AgentResult(
            success=False,
            summary=("Repository Explorer reached its iteration limit without submitting a report"),
        )

    def _reject_finalization_tool(
        self, state: WorkflowState, iteration: int, call: ToolCall
    ) -> ChatMessage:
        reason = "Finalization permits only one finish_exploration call"
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
        return ChatMessage(
            "tool",
            json.dumps({"ok": False, "error": reason}),
            tool_call_id=call.id,
        )

    def _execute_tool(self, state: WorkflowState, iteration: int, call: ToolCall) -> ChatMessage:
        record_agent_event(
            self._recorder,
            state,
            self.name,
            "tool_requested",
            {"iteration": iteration, "tool": call.name},
        )
        try:
            result: object
            if call.name == SEMANTIC_SEARCH_TOOL.name:
                if self._embedding_index is None:
                    raise RepositoryToolError("Semantic search is not configured")
                query = call.arguments.get("query")
                path = call.arguments.get("path", ".")
                if not isinstance(query, str):
                    raise RepositoryToolError("'query' must be text")
                if not isinstance(path, str):
                    raise RepositoryToolError("'path' must be text")
                result = self._embedding_index.search(query, path=path)
            elif call.name == FIND_SYMBOL_TOOL.name:
                query = call.arguments.get("query")
                kind = call.arguments.get("kind")
                path = call.arguments.get("path", ".")
                if not isinstance(query, str):
                    raise RepositoryToolError("'query' must be text")
                if kind is not None and not isinstance(kind, str):
                    raise RepositoryToolError("'kind' must be text")
                if not isinstance(path, str):
                    raise RepositoryToolError("'path' must be text")
                result = self._symbol_index.find(query, kind=kind, path=path)
            else:
                result = self._inspector.execute(call.name, call.arguments)
            payload: dict[str, Any] = {"ok": True, "result": result}
            serialized_result = json.dumps(result, ensure_ascii=False, default=str)
            state.memory.remember(
                "repository_observation",
                call.name,
                serialized_result,
                priority=60,
            )
            record_agent_event(
                self._recorder,
                state,
                self.name,
                "tool_completed",
                {
                    "iteration": iteration,
                    "tool": call.name,
                    "result_size": len(serialized_result),
                },
            )
        except RepositoryToolError as error:
            payload = {"ok": False, "error": str(error)}
            record_agent_event(
                self._recorder,
                state,
                self.name,
                "tool_rejected",
                {"iteration": iteration, "tool": call.name, "reason": str(error)},
            )
        budgeted = budget_json_payload(payload, self._maximum_tool_observation_characters)
        if budgeted.truncated:
            record_agent_event(
                self._recorder,
                state,
                self.name,
                "tool_observation_truncated",
                {
                    "iteration": iteration,
                    "tool": call.name,
                    "original_characters": budgeted.original_characters,
                    "included_characters": len(budgeted.text),
                },
            )
        return ChatMessage("tool", budgeted.text, tool_call_id=call.id)

    def _build_report(self, call: ToolCall) -> ExplorationReport:
        summary = call.arguments.get("summary")
        raw_files = call.arguments.get("relevant_files")
        raw_findings = call.arguments.get("findings")
        if not isinstance(summary, str) or not summary.strip():
            raise AgentProtocolError("Exploration summary must be non-empty text")
        if not isinstance(raw_files, list) or not all(
            isinstance(path, str) and path.strip() for path in raw_files
        ):
            raise AgentProtocolError("Relevant files must be a list of paths")
        if not isinstance(raw_findings, list) or not all(
            isinstance(finding, str) and finding.strip() for finding in raw_findings
        ):
            raise AgentProtocolError("Findings must be a list of text entries")

        try:
            files = tuple(
                self._inspector.validate_file_reference(path.strip()) for path in raw_files
            )
        except RepositoryToolError as error:
            raise AgentProtocolError(
                f"Exploration report references an invalid file: {error}"
            ) from error
        return ExplorationReport(
            summary=summary.strip(),
            relevant_files=files,
            findings=tuple(finding.strip() for finding in raw_findings),
        )

    def _partition_report_paths(self, call: ToolCall) -> tuple[set[str], set[str]]:
        raw_files = call.arguments.get("relevant_files")
        if not isinstance(raw_files, list):
            return set(), set()
        valid: set[str] = set()
        invalid: set[str] = set()
        for raw_path in raw_files:
            if not isinstance(raw_path, str) or not raw_path.strip():
                continue
            path = raw_path.strip()
            try:
                valid.add(self._inspector.validate_file_reference(path))
            except RepositoryToolError:
                invalid.add(path)
        return valid, invalid
