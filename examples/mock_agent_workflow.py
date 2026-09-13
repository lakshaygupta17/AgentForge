"""Run Planner -> Repository Explorer without a network call or API token."""

from pathlib import Path

from agentforge.agents.explorer import EXPLORATION_ARTIFACT, RepositoryExplorerAgent
from agentforge.agents.planner import PLAN_ARTIFACT, PlannerAgent
from agentforge.core.models import Task
from agentforge.llm.mock import MockLLMProvider
from agentforge.llm.models import ChatMessage, ChatResponse, ToolCall
from agentforge.orchestration.runner import MultiAgentOrchestrator
from agentforge.telemetry.trajectory import InMemoryTrajectoryRecorder
from agentforge.tools.repository import RepositoryInspector


def tool_response(call_id: str, name: str, arguments: dict[str, object]) -> ChatResponse:
    return ChatResponse(
        message=ChatMessage(
            role="assistant",
            content=None,
            tool_calls=(ToolCall(call_id, name, arguments),),
        ),
        model="mock-coder",
    )


def main() -> None:
    workspace = Path.cwd()
    provider = MockLLMProvider(
        [
            tool_response(
                "plan-1",
                "submit_plan",
                {
                    "steps": ["Inspect the project guide", "Summarize the safety design"],
                    "rationale": "The guide is the clearest source of design intent.",
                },
            ),
            tool_response("search-1", "search_code", {"query": "policy engine"}),
            tool_response(
                "finish-1",
                "finish_exploration",
                {
                    "summary": "AgentForge places policy checks before sandbox execution.",
                    "relevant_files": ["PROJECT_GUIDE.md"],
                    "findings": [
                        "PROJECT_GUIDE.md explains policy and sandboxing as separate layers."
                    ],
                },
            ),
        ]
    )
    recorder = InMemoryTrajectoryRecorder()
    orchestrator = MultiAgentOrchestrator(
        [
            PlannerAgent(provider, recorder=recorder),
            RepositoryExplorerAgent(
                provider,
                RepositoryInspector(workspace),
                recorder=recorder,
            ),
        ],
        recorder,
    )

    state = orchestrator.run(Task("Explain AgentForge's safety design"))
    print(state.artifacts[PLAN_ARTIFACT])
    print(state.artifacts[EXPLORATION_ARTIFACT])
    print(f"Recorded {len(recorder.events)} trajectory events for run {state.run_id}")


if __name__ == "__main__":
    main()

