"""Planner agent: convert a task into a bounded, structured plan."""

from __future__ import annotations

from agentforge.agents.base import AgentResult
from agentforge.agents.errors import AgentProtocolError
from agentforge.agents.models import Plan
from agentforge.agents.telemetry import record_agent_event
from agentforge.core.models import WorkflowState
from agentforge.llm.base import LLMProvider
from agentforge.llm.models import ChatMessage, ToolDefinition
from agentforge.telemetry.trajectory import TrajectoryRecorder

PLAN_ARTIFACT = "plan"
SUBMIT_PLAN_TOOL = ToolDefinition(
    name="submit_plan",
    description="Submit the ordered software-engineering plan for this task.",
    parameters={
        "type": "object",
        "properties": {
            "steps": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Concrete ordered steps that later agents can follow.",
            },
            "rationale": {
                "type": "string",
                "description": "A short explanation of why this plan is appropriate.",
            },
        },
        "required": ["steps", "rationale"],
        "additionalProperties": False,
    },
)


class PlannerAgent:
    name = "planner"

    def __init__(
        self,
        provider: LLMProvider,
        *,
        recorder: TrajectoryRecorder | None = None,
        maximum_steps: int = 12,
    ) -> None:
        if maximum_steps <= 0:
            raise ValueError("Maximum plan steps must be greater than zero")
        self._provider = provider
        self._recorder = recorder
        self._maximum_steps = maximum_steps

    def run(self, state: WorkflowState) -> AgentResult:
        messages = (
            ChatMessage(
                "system",
                "You are the Planner for a secure software-engineering agent team. "
                "Create a concise plan. Do not claim to have inspected files. Finish by "
                "calling submit_plan exactly once.",
            ),
            ChatMessage(
                "user",
                f"Task: {state.task.description}\nRepository: "
                f"{state.task.repository or 'already-provided workspace'}",
            ),
        )
        record_agent_event(
            self._recorder,
            state,
            self.name,
            "llm_requested",
            {"message_count": len(messages), "tools": [SUBMIT_PLAN_TOOL.name]},
        )
        response = self._provider.chat(
            messages,
            tools=(SUBMIT_PLAN_TOOL,),
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
            raise AgentProtocolError("Planner must make exactly one submit_plan call")
        call = response.message.tool_calls[0]
        if call.name != SUBMIT_PLAN_TOOL.name:
            raise AgentProtocolError(f"Planner called unexpected tool '{call.name}'")

        raw_steps = call.arguments.get("steps")
        rationale = call.arguments.get("rationale")
        if not isinstance(raw_steps, list) or not raw_steps:
            raise AgentProtocolError("Planner returned no plan steps")
        if len(raw_steps) > self._maximum_steps:
            raise AgentProtocolError(
                f"Planner exceeded the {self._maximum_steps}-step limit"
            )
        if not all(isinstance(step, str) and step.strip() for step in raw_steps):
            raise AgentProtocolError("Every plan step must be non-empty text")
        if not isinstance(rationale, str) or not rationale.strip():
            raise AgentProtocolError("Planner rationale must be non-empty text")

        plan = Plan(
            steps=tuple(step.strip() for step in raw_steps),
            rationale=rationale.strip(),
        )
        record_agent_event(
            self._recorder,
            state,
            self.name,
            "plan_created",
            {"step_count": len(plan.steps)},
        )
        state.memory.remember(
            "plan",
            self.name,
            plan.rationale + "\n" + "\n".join(f"- {step}" for step in plan.steps),
            priority=85,
        )
        return AgentResult(
            success=True,
            summary=f"Planner created a {len(plan.steps)}-step plan",
            artifacts={PLAN_ARTIFACT: plan},
        )
