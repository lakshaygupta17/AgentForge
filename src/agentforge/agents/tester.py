"""Tester agent: route structured test commands through policy and sandbox."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentforge.agents.base import AgentResult
from agentforge.agents.developer import DEVELOPMENT_ARTIFACT
from agentforge.agents.errors import AgentProtocolError
from agentforge.agents.models import DevelopmentReport, TestReport
from agentforge.agents.telemetry import record_agent_event
from agentforge.context.budget import budget_json_payload
from agentforge.core.models import ActionRequest, ExecutionResult, WorkflowState
from agentforge.llm.base import LLMProvider
from agentforge.llm.models import ChatMessage, ToolCall, ToolDefinition
from agentforge.policy.engine import SecurityPolicy
from agentforge.sandbox.base import Sandbox
from agentforge.telemetry.trajectory import TrajectoryRecorder

TEST_ARTIFACT = "tests"
RUN_TESTS_TOOL = ToolDefinition(
    "run_tests",
    "Run a test command through the policy-controlled sandbox.",
    {
        "type": "object",
        "properties": {
            "executable": {"type": "string"},
            "arguments": {"type": "array", "items": {"type": "string"}},
            "working_directory": {"type": "string", "default": "."},
            "timeout_seconds": {"type": "number", "default": 60},
        },
        "required": ["executable", "arguments"],
        "additionalProperties": False,
    },
)
FINISH_TESTING_TOOL = ToolDefinition(
    "finish_testing",
    "Report the verified test outcome. It must match the last command result.",
    {
        "type": "object",
        "properties": {
            "passed": {"type": "boolean"},
            "summary": {"type": "string"},
        },
        "required": ["passed", "summary"],
        "additionalProperties": False,
    },
)
TEST_EXECUTION_TOOLS = (RUN_TESTS_TOOL,)
TEST_REPORT_TOOLS = (FINISH_TESTING_TOOL,)


class TesterAgent:
    name = "tester"

    def __init__(
        self,
        provider: LLMProvider,
        sandbox: Sandbox,
        policy: SecurityPolicy,
        *,
        recorder: TrajectoryRecorder | None = None,
        maximum_iterations: int = 6,
        maximum_context_characters: int = 6_000,
        maximum_tool_observation_characters: int = 20_000,
    ) -> None:
        if maximum_iterations <= 0:
            raise ValueError("Maximum tester iterations must be greater than zero")
        if maximum_context_characters < 200:
            raise ValueError("Tester context budget must be at least 200 characters")
        if maximum_tool_observation_characters < 200:
            raise ValueError("Tester observation budget must be at least 200 characters")
        self._provider = provider
        self._sandbox = sandbox
        self._policy = policy
        self._recorder = recorder
        self._maximum_iterations = maximum_iterations
        self._maximum_context_characters = maximum_context_characters
        self._maximum_tool_observation_characters = (
            maximum_tool_observation_characters
        )

    def run(self, state: WorkflowState) -> AgentResult:
        development = state.artifacts.get(DEVELOPMENT_ARTIFACT)
        if not isinstance(development, DevelopmentReport):
            raise AgentProtocolError("Tester requires a DevelopmentReport artifact")

        memory = state.memory.render(
            state.task.description,
            maximum_characters=max(1, self._maximum_context_characters // 2),
        )
        context = budget_json_payload(
            {
                "task": state.task.description,
                "changed_files": development.changed_files,
                "run_memory": memory.text,
                "omitted_memory_entries": memory.omitted_entries,
            },
            self._maximum_context_characters,
        )
        if context.truncated:
            record_agent_event(
                self._recorder,
                state,
                self.name,
                "model_context_truncated",
                {
                    "original_characters": context.original_characters,
                    "included_characters": len(context.text),
                },
            )

        messages: list[ChatMessage] = [
            ChatMessage(
                "system",
                "You are the Tester. Run the broadest relevant repository acceptance suite "
                "through run_tests, following test instructions or project configuration "
                "when available. Do not replace a failing suite with a narrower or unrelated "
                "command merely to obtain exit code zero. Never infer a pass: finish_testing "
                "must agree with the completed execution result.",
            ),
            ChatMessage(
                "user",
                context.text,
            ),
        ]
        last_request: ActionRequest | None = None
        last_result: ExecutionResult | None = None

        for iteration in range(1, self._maximum_iterations + 1):
            record_agent_event(
                self._recorder,
                state,
                self.name,
                "llm_requested",
                {"iteration": iteration, "message_count": len(messages)},
            )
            response = self._provider.chat(
                messages, tools=TEST_EXECUTION_TOOLS, tool_choice="required"
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
            if len(calls) != 1:
                raise AgentProtocolError("Tester must make exactly one tool call per turn")
            call = calls[0]
            messages.append(response.message)

            if call.name != RUN_TESTS_TOOL.name:
                raise AgentProtocolError(f"Tester called unexpected tool '{call.name}'")

            try:
                last_request = self._request_from_call(call)
                last_result = self._sandbox.execute(last_request, self._policy)
                result_payload: dict[str, Any] = {
                    "ok": True,
                    "result": {
                        "exit_code": last_result.exit_code,
                        "stdout": last_result.stdout,
                        "stderr": last_result.stderr,
                        "timed_out": last_result.timed_out,
                        "duration_seconds": last_result.duration_seconds,
                        "output_truncated": last_result.output_truncated,
                    },
                }
                record_agent_event(
                    self._recorder,
                    state,
                    self.name,
                    "test_execution_completed",
                    {
                        "iteration": iteration,
                        "exit_code": last_result.exit_code,
                        "timed_out": last_result.timed_out,
                        "succeeded": last_result.succeeded,
                    },
                )
            except (OSError, PermissionError, RuntimeError, ValueError) as error:
                last_request = None
                last_result = None
                result_payload = {"ok": False, "error": str(error)}
                record_agent_event(
                    self._recorder,
                    state,
                    self.name,
                    "test_execution_rejected",
                    {"iteration": iteration, "reason": str(error)},
                )
            budgeted = budget_json_payload(
                result_payload, self._maximum_tool_observation_characters
            )
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
            if last_request is not None and last_result is not None:
                state.memory.remember(
                    "test_evidence",
                    self.name,
                    json.dumps(
                        {
                            "command": last_request.argv,
                            "exit_code": last_result.exit_code,
                            "timed_out": last_result.timed_out,
                            "stdout": last_result.stdout,
                            "stderr": last_result.stderr,
                        },
                        ensure_ascii=False,
                    ),
                    priority=95,
                )
            messages.append(
                ChatMessage(
                    "tool",
                    budgeted.text,
                    tool_call_id=call.id,
                )
            )
            if last_request is not None and last_result is not None:
                return self._request_final_report(
                    state,
                    messages,
                    iteration + 1,
                    last_request,
                    last_result,
                )

        return AgentResult(
            False,
            "Tester reached its iteration limit without reporting a verified result",
        )

    def _request_final_report(
        self,
        state: WorkflowState,
        messages: list[ChatMessage],
        iteration: int,
        request: ActionRequest,
        result: ExecutionResult,
    ) -> AgentResult:
        messages.append(
            ChatMessage(
                "system",
                "The test command completed. Report that exact result now with one "
                "finish_testing call. No additional or narrower test command is available.",
            )
        )
        record_agent_event(
            self._recorder,
            state,
            self.name,
            "testing_finalization_required",
            {"iteration": iteration},
        )
        record_agent_event(
            self._recorder,
            state,
            self.name,
            "llm_requested",
            {"iteration": iteration, "message_count": len(messages)},
        )
        response = self._provider.chat(
            messages,
            tools=TEST_REPORT_TOOLS,
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
        if len(calls) != 1 or calls[0].name != FINISH_TESTING_TOOL.name:
            reason = "Testing finalization permits exactly one finish_testing call"
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
            return AgentResult(False, reason)
        return self._finish(state, calls[0], request, result)

    def _request_from_call(self, call: ToolCall) -> ActionRequest:
        executable = call.arguments.get("executable")
        arguments = call.arguments.get("arguments")
        working_directory = call.arguments.get("working_directory", ".")
        timeout_seconds = call.arguments.get("timeout_seconds", 60)
        if not isinstance(executable, str) or not executable.strip():
            raise AgentProtocolError("Test executable must be non-empty text")
        if not isinstance(arguments, list) or not all(
            isinstance(argument, str) for argument in arguments
        ):
            raise AgentProtocolError("Test arguments must be a list of text values")
        if not isinstance(working_directory, str) or not working_directory.strip():
            raise AgentProtocolError("Test working directory must be non-empty text")
        if (
            not isinstance(timeout_seconds, (int, float))
            or isinstance(timeout_seconds, bool)
            or timeout_seconds <= 0
        ):
            raise AgentProtocolError("Test timeout must be a positive number")
        return ActionRequest(
            executable.strip(),
            tuple(arguments),
            Path(working_directory),
            float(timeout_seconds),
        )

    def _finish(
        self,
        state: WorkflowState,
        call: ToolCall,
        request: ActionRequest | None,
        result: ExecutionResult | None,
    ) -> AgentResult:
        if request is None or result is None:
            raise AgentProtocolError("Tester cannot finish before a completed test execution")
        passed = call.arguments.get("passed")
        summary = call.arguments.get("summary")
        if not isinstance(passed, bool):
            raise AgentProtocolError("Test passed value must be boolean")
        if not isinstance(summary, str) or not summary.strip():
            raise AgentProtocolError("Test summary must be non-empty text")
        if passed != result.succeeded:
            raise AgentProtocolError(
                "Reported test status does not match the last command result"
            )

        report = TestReport(
            passed=passed,
            summary=summary.strip(),
            command=request.argv,
            exit_code=result.exit_code,
            timed_out=result.timed_out,
            stdout=result.stdout,
            stderr=result.stderr,
        )
        record_agent_event(
            self._recorder,
            state,
            self.name,
            "testing_completed",
            {
                "passed": report.passed,
                "exit_code": report.exit_code,
                "summary": report.summary,
                "command": list(report.command),
                "timed_out": report.timed_out,
            },
        )
        return AgentResult(
            passed,
            report.summary,
            {TEST_ARTIFACT: report},
        )
