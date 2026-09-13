import json
import math
from pathlib import Path
from unittest import TestCase

from agentforge.agents.explorer import RepositoryExplorerAgent
from agentforge.agents.models import Plan
from agentforge.agents.planner import PLAN_ARTIFACT
from agentforge.core.approval import ApprovalCheckpoint, ApprovalRequiredError
from agentforge.core.models import Task, WorkflowState
from agentforge.evaluation.retrieval import RetrievalCase, RetrievalEvaluator
from agentforge.llm.errors import ProviderResponseError
from agentforge.llm.mock import MockLLMProvider
from agentforge.llm.models import ChatMessage, ChatResponse, ToolCall
from agentforge.llm.transport import HttpResponse
from agentforge.tools.repository import RepositoryInspector
from agentforge.tools.semantic import (
    HashingEmbeddingProvider,
    OpenAICompatibleEmbeddingProvider,
    RepositoryEmbeddingIndex,
)

FIXTURE = Path(__file__).parent / "fixtures" / "retrieval_project"


def tool_response(
    call_id: str, name: str, arguments: dict[str, object]
) -> ChatResponse:
    return ChatResponse(
        ChatMessage(
            "assistant",
            None,
            tool_calls=(ToolCall(call_id, name, arguments),),
        ),
        "mock-coder",
    )


class SemanticRetrievalTests(TestCase):
    def test_learned_embedding_adapter_requires_approval_and_validates_vectors(
        self,
    ) -> None:
        class Transport:
            def __init__(self) -> None:
                self.calls = 0

            def post(self, *args: object, **kwargs: object) -> HttpResponse:
                self.calls += 1
                body = json.dumps(
                    {
                        "data": [
                            {"index": 1, "embedding": [0.0, 1.0]},
                            {"index": 0, "embedding": [1.0, 0.0]},
                        ]
                    }
                ).encode()
                return HttpResponse(200, body, {})

        blocked_transport = Transport()
        blocked = OpenAICompatibleEmbeddingProvider(
            base_url="https://embedding.example/v1",
            api_key="private",
            model="embedding-model",
            dimensions=2,
            approval=ApprovalCheckpoint(),
            transport=blocked_transport,
        )
        with self.assertRaises(ApprovalRequiredError):
            blocked.embed(("source text",))
        self.assertEqual(blocked_transport.calls, 0)

        transport = Transport()
        approved = OpenAICompatibleEmbeddingProvider(
            base_url="https://embedding.example/v1",
            api_key="private",
            model="embedding-model",
            dimensions=2,
            approval=ApprovalCheckpoint(code_transmission=True),
            transport=transport,
        )
        vectors = approved.embed(("first", "second"))

        self.assertEqual(vectors, ((1.0, 0.0), (0.0, 1.0)))
        self.assertEqual(transport.calls, 1)

    def test_learned_embedding_adapter_rejects_wrong_dimensions(self) -> None:
        class Transport:
            def post(self, *args: object, **kwargs: object) -> HttpResponse:
                return HttpResponse(
                    200,
                    b'{"data":[{"index":0,"embedding":[1.0]}]}',
                    {},
                )

        provider = OpenAICompatibleEmbeddingProvider(
            base_url="https://embedding.example/v1",
            api_key="private",
            model="embedding-model",
            dimensions=2,
            approval=ApprovalCheckpoint(code_transmission=True),
            transport=Transport(),
        )

        with self.assertRaisesRegex(ProviderResponseError, "dimensions"):
            provider.embed(("text",))

    def test_hashing_vectors_are_deterministic_and_normalized(self) -> None:
        provider = HashingEmbeddingProvider(dimensions=64)

        first, second = provider.embed(("calculate_total", "calculate total"))

        self.assertEqual(first, second)
        self.assertAlmostEqual(math.sqrt(sum(value * value for value in first)), 1.0)

    def test_repository_index_ranks_the_relevant_file(self) -> None:
        inspector = RepositoryInspector(FIXTURE)
        index = RepositoryEmbeddingIndex(
            inspector, HashingEmbeddingProvider(), maximum_results=1
        )

        results = index.search("calculate shopping total")

        self.assertEqual(results[0]["path"], "checkout.py")
        self.assertGreater(results[0]["score"], 0)

    def test_embedding_baseline_improves_paraphrased_fixture_queries(self) -> None:
        inspector = RepositoryInspector(FIXTURE)
        evaluator = RetrievalEvaluator(
            inspector,
            top_k=1,
            embedding_index=RepositoryEmbeddingIndex(
                inspector, HashingEmbeddingProvider(), maximum_results=1
            ),
        )
        cases = (
            RetrievalCase("calculate shopping total", ("checkout.py",)),
            RetrievalCase("reserve requested quantity", ("inventory.py",)),
            RetrievalCase("validate discount coupon", ("checkout.py",)),
        )

        comparison = evaluator.compare_text_and_embeddings(cases)

        self.assertEqual(comparison.text.hit_rate, 0.0)
        self.assertEqual(comparison.embedding.hit_rate, 1.0)
        self.assertEqual(comparison.hit_rate_change, 1.0)
        self.assertEqual(comparison.mean_reciprocal_rank_change, 1.0)

    def test_explorer_exposes_semantic_search_only_when_configured(self) -> None:
        inspector = RepositoryInspector(FIXTURE)
        provider = MockLLMProvider(
            [
                tool_response(
                    "semantic",
                    "semantic_search",
                    {"query": "calculate shopping total"},
                ),
                tool_response(
                    "finish",
                    "finish_exploration",
                    {
                        "summary": "Located checkout calculation",
                        "relevant_files": ["checkout.py"],
                        "findings": ["checkout.py contains the total calculation"],
                    },
                ),
            ]
        )
        state = WorkflowState(Task("Find total calculation"))
        state.artifacts[PLAN_ARTIFACT] = Plan(("Locate the calculation",), "Search")
        index = RepositoryEmbeddingIndex(inspector, HashingEmbeddingProvider())

        result = RepositoryExplorerAgent(
            provider, inspector, embedding_index=index
        ).run(state)

        observation = provider.requests[1].messages[-1].content or ""
        self.assertTrue(result.success)
        self.assertIn("checkout.py", observation)
        self.assertIn(
            "semantic_search",
            [tool.name for tool in provider.requests[0].tools],
        )
