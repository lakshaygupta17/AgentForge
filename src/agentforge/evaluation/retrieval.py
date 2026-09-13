"""Small deterministic benchmark for repository retrieval quality."""

from __future__ import annotations

from dataclasses import dataclass

from agentforge.tools.index import PythonSymbolIndex
from agentforge.tools.repository import RepositoryInspector
from agentforge.tools.semantic import RepositoryEmbeddingIndex


@dataclass(frozen=True, slots=True)
class RetrievalCase:
    query: str
    expected_paths: tuple[str, ...]
    mode: str = "text"
    kind: str | None = None

    def __post_init__(self) -> None:
        if not self.query.strip():
            raise ValueError("Retrieval query cannot be empty")
        if not self.expected_paths:
            raise ValueError("Retrieval case needs at least one expected path")
        if self.mode not in {"embedding", "symbol", "text"}:
            raise ValueError("Retrieval mode must be 'embedding', 'symbol', or 'text'")


@dataclass(frozen=True, slots=True)
class RetrievalEvaluation:
    case_count: int
    hit_count: int
    hit_rate: float
    mean_reciprocal_rank: float
    missed_queries: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RetrievalComparison:
    text: RetrievalEvaluation
    embedding: RetrievalEvaluation

    @property
    def hit_rate_change(self) -> float:
        return self.embedding.hit_rate - self.text.hit_rate

    @property
    def mean_reciprocal_rank_change(self) -> float:
        return self.embedding.mean_reciprocal_rank - self.text.mean_reciprocal_rank


class RetrievalEvaluator:
    """Measure whether expected files appear near the top of retrieval results."""

    def __init__(
        self,
        inspector: RepositoryInspector,
        *,
        top_k: int = 5,
        embedding_index: RepositoryEmbeddingIndex | None = None,
    ) -> None:
        if top_k <= 0:
            raise ValueError("Retrieval top_k must be greater than zero")
        self._inspector = inspector
        self._symbols = PythonSymbolIndex(inspector, maximum_results=top_k)
        self._embeddings = embedding_index
        self.top_k = top_k

    def evaluate(self, cases: tuple[RetrievalCase, ...]) -> RetrievalEvaluation:
        if not cases:
            raise ValueError("Retrieval evaluation needs at least one case")
        reciprocal_ranks: list[float] = []
        misses: list[str] = []
        for case in cases:
            expected = {
                self._inspector.validate_file_reference(path)
                for path in case.expected_paths
            }
            ranked_paths = self._retrieve(case)
            rank = next(
                (
                    position
                    for position, path in enumerate(ranked_paths, start=1)
                    if path in expected
                ),
                None,
            )
            if rank is None:
                reciprocal_ranks.append(0.0)
                misses.append(case.query)
            else:
                reciprocal_ranks.append(1.0 / rank)

        hit_count = sum(rank > 0 for rank in reciprocal_ranks)
        return RetrievalEvaluation(
            case_count=len(cases),
            hit_count=hit_count,
            hit_rate=hit_count / len(cases),
            mean_reciprocal_rank=sum(reciprocal_ranks) / len(cases),
            missed_queries=tuple(misses),
        )

    def compare_text_and_embeddings(
        self, cases: tuple[RetrievalCase, ...]
    ) -> RetrievalComparison:
        if self._embeddings is None:
            raise ValueError("Embedding comparison requires an embedding index")
        text_cases = tuple(
            RetrievalCase(case.query, case.expected_paths, mode="text")
            for case in cases
        )
        embedding_cases = tuple(
            RetrievalCase(case.query, case.expected_paths, mode="embedding")
            for case in cases
        )
        return RetrievalComparison(
            self.evaluate(text_cases), self.evaluate(embedding_cases)
        )

    def _retrieve(self, case: RetrievalCase) -> list[str]:
        if case.mode == "embedding":
            if self._embeddings is None:
                raise ValueError("Embedding retrieval requires an embedding index")
            results = self._embeddings.search(case.query)
        elif case.mode == "symbol":
            results = self._symbols.find(case.query, kind=case.kind)
        else:
            results = self._inspector.search_code(case.query)
        paths: list[str] = []
        for result in results:
            path = result.get("path")
            if isinstance(path, str) and path not in paths:
                paths.append(path)
            if len(paths) >= self.top_k:
                break
        return paths
