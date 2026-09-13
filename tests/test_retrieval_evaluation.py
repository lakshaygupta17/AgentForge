from pathlib import Path
from unittest import TestCase

from agentforge.evaluation.retrieval import RetrievalCase, RetrievalEvaluator
from agentforge.tools.repository import RepositoryInspector

FIXTURE = Path(__file__).parent / "fixtures" / "retrieval_project"


class RetrievalEvaluatorTests(TestCase):
    def test_fixture_retrieval_has_perfect_top_three_hit_rate(self) -> None:
        evaluation = RetrievalEvaluator(
            RepositoryInspector(FIXTURE), top_k=3
        ).evaluate(
            (
                RetrievalCase(
                    "calculate_total", ("checkout.py",), mode="symbol", kind="method"
                ),
                RetrievalCase(
                    "reserve_stock", ("inventory.py",), mode="symbol", kind="method"
                ),
                RetrievalCase("startswith", ("checkout.py",), mode="text"),
            )
        )

        self.assertEqual(evaluation.case_count, 3)
        self.assertEqual(evaluation.hit_count, 3)
        self.assertEqual(evaluation.hit_rate, 1.0)
        self.assertEqual(evaluation.mean_reciprocal_rank, 1.0)
        self.assertEqual(evaluation.missed_queries, ())

    def test_reports_misses_instead_of_hiding_them(self) -> None:
        evaluation = RetrievalEvaluator(RepositoryInspector(FIXTURE)).evaluate(
            (RetrievalCase("missing concept", ("checkout.py",)),)
        )

        self.assertEqual(evaluation.hit_rate, 0.0)
        self.assertEqual(evaluation.missed_queries, ("missing concept",))
