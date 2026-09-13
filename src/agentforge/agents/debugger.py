"""Debugger agent: convert failed test evidence into a structured diagnosis."""

from __future__ import annotations

import json

from agentforge.agents.base import AgentResult
from agentforge.agents.errors import AgentProtocolError
from agentforge.agents.models import DevelopmentReport, Diagnosis, TestReport
from agentforge.agents.telemetry import record_agent_event
from agentforge.agents.tester import TEST_ARTIFACT
from agentforge.context.budget import budget_json_payload
from agentforge.core.models import WorkflowState
from agentforge.llm.base import LLMProvider
from agentforge.llm.models import ChatMessage, ToolDefinition
from agentforge.telemetry.trajectory import TrajectoryRecorder

DIAGNOSIS_ARTIFACT = "diagnosis"
SUBMIT_DIAGNOSIS_TOOL = ToolDefinition(
    "submit_diagnosis",
    "Explain a failed test and recommend a focused next change.",
    {
        "type": "object",
        "properties": {
            "hypothesis": {"type": "string"},
            "evidence": {"type": "array", "items": {"type": "string"}},
            "recommended_changes": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": ["hypothesis", "evidence", "recommended_changes"],
        "additionalProperties": False,
    },
)


class DebuggerAgent:
    name = "debugger"

    def __init__(
        self,
        provider: LLMProvider,
        *,
        recorder: TrajectoryRecorder | None = None,
        maximum_context_characters: int = 12_000,
    ) -> None:
        if maximum_context_characters < 200:
            raise ValueError("Debugger context budget must be at least 200 characters")
        self._provider = provider
        self._recorder = recorder
        self._maximum_context_characters = maximum_context_characters

    def run(self, state: WorkflowState) -> AgentResult:
        tests = state.artifacts.get(TEST_ARTIFACT)
        development = state.artifacts.get("development")
        if not isinstance(tests, TestReport) or tests.passed:
            raise AgentProtocolError("Debugger requires a failed TestReport")
        if not isinstance(development, DevelopmentReport):
            raise AgentProtocolError("Debugger requires a DevelopmentReport")

        memory = state.memory.render(
            state.task.description,
            maximum_characters=max(1, self._maximum_context_characters // 3),
        )
        context = budget_json_payload(
            {
                "task": state.task.description,
                "changed_files": development.changed_files,
                "test_command": tests.command,
                "exit_code": tests.exit_code,
                "timed_out": tests.timed_out,
                "stdout": tests.stdout,
                "stderr": tests.stderr,
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

        messages = (
            ChatMessage(
                "system",
                "You are the Debugger. Explain the failure using only the supplied test "
                "evidence and recommend the smallest next change. Submit one diagnosis.",
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
            {"message_count": len(messages), "tools": [SUBMIT_DIAGNOSIS_TOOL.name]},
        )
        response = self._provider.chat(
            messages,
            tools=(SUBMIT_DIAGNOSIS_TOOL,),
            tool_choice="required",
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
            raise AgentProtocolError("Debugger must submit exactly one diagnosis")
        call = response.message.tool_calls[0]
        if call.name != SUBMIT_DIAGNOSIS_TOOL.name:
            raise AgentProtocolError(f"Debugger called unexpected tool '{call.name}'")

        hypothesis = call.arguments.get("hypothesis")
        evidence = call.arguments.get("evidence")
        changes = call.arguments.get("recommended_changes")
        if not isinstance(hypothesis, str) or not hypothesis.strip():
            raise AgentProtocolError("Diagnosis hypothesis must be non-empty text")
        if not isinstance(evidence, list) or not evidence or not all(
            isinstance(item, str) and item.strip() for item in evidence
        ):
            raise AgentProtocolError("Diagnosis evidence must contain non-empty text")
        if not isinstance(changes, list) or not changes or not all(
            isinstance(item, str) and item.strip() for item in changes
        ):
            raise AgentProtocolError("Diagnosis recommendations must contain non-empty text")

        diagnosis = Diagnosis(
            hypothesis.strip(),
            tuple(item.strip() for item in evidence),
            tuple(item.strip() for item in changes),
        )
        state.memory.remember(
            "diagnosis",
            self.name,
            json.dumps(
                {
                    "hypothesis": diagnosis.hypothesis,
                    "evidence": diagnosis.evidence,
                    "recommended_changes": diagnosis.recommended_changes,
                },
                ensure_ascii=False,
            ),
            priority=95,
        )
        record_agent_event(
            self._recorder,
            state,
            self.name,
            "diagnosis_created",
            {
                "evidence_count": len(diagnosis.evidence),
                "recommendation_count": len(diagnosis.recommended_changes),
            },
        )
        return AgentResult(
            True,
            "Debugger produced a grounded diagnosis",
            {DIAGNOSIS_ARTIFACT: diagnosis},
        )
