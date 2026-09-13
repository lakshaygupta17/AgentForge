import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from agentforge.cli import main
from agentforge.telemetry.metrics import (
    MetricsError,
    SqliteMetricsStore,
    TokenPricing,
    load_trajectory,
    summarize_trajectory,
)
from agentforge.telemetry.trajectory import JsonlTrajectoryRecorder, TrajectoryEvent


class TrajectoryMetricsTests(TestCase):
    @staticmethod
    def _events(run_id: str = "run-1") -> tuple[TrajectoryEvent, ...]:
        def event(
            event_type: str,
            second: int,
            data: dict[str, object] | None = None,
        ) -> TrajectoryEvent:
            return TrajectoryEvent(
                run_id,
                event_type,
                "test",
                data or {},
                f"2026-01-01T00:00:{second:02d}+00:00",
            )

        return (
            event("workflow_started", 0),
            event("repair_attempt_started", 0),
            event("llm_requested", 0),
            event(
                "llm_responded",
                1,
                {
                    "model": "local/mock",
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "total_tokens": 150,
                },
            ),
            event("test_execution_completed", 1, {"duration_seconds": 1.25}),
            event("tests_failed", 1),
            event("repair_retry_scheduled", 1),
            event("review_rejected", 1),
            event("agent_completed", 1),
            event("workflow_completed", 2),
        )

    def test_summarizes_reliability_usage_sandbox_time_and_cost(self) -> None:
        metrics = summarize_trajectory(
            self._events(), pricing=TokenPricing(1, 2)
        )

        self.assertTrue(metrics.succeeded)
        self.assertEqual(metrics.duration_seconds, 2)
        self.assertEqual(metrics.agent_steps, 1)
        self.assertEqual(metrics.repair_attempts, 1)
        self.assertEqual(metrics.retries, 1)
        self.assertEqual(metrics.test_runs, 1)
        self.assertEqual(metrics.failed_test_runs, 1)
        self.assertEqual(metrics.review_rejections, 1)
        self.assertEqual(metrics.total_tokens, 150)
        self.assertEqual(metrics.sandbox_time_seconds, 1.25)
        self.assertAlmostEqual(metrics.estimated_cost_usd or 0, 0.0002)
        self.assertEqual(metrics.models, ("local/mock",))

    def test_jsonl_loader_rejects_malformed_and_mixed_runs(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            malformed = Path(temporary_directory) / "malformed.jsonl"
            malformed.write_text("not json\n", encoding="utf-8")
            with self.assertRaisesRegex(MetricsError, "line 1"):
                load_trajectory(malformed)

            mixed = Path(temporary_directory) / "mixed.jsonl"
            recorder = JsonlTrajectoryRecorder(mixed)
            recorder.record(self._events("one")[0])
            recorder.record(self._events("two")[0])
            with self.assertRaisesRegex(MetricsError, "exactly one run"):
                load_trajectory(mixed)

    def test_sqlite_store_upserts_sanitized_aggregates(self) -> None:
        first = summarize_trajectory(self._events())
        changed = summarize_trajectory(self._events(), pricing=TokenPricing(1, 1))
        with TemporaryDirectory() as temporary_directory:
            store = SqliteMetricsStore(Path(temporary_directory) / "metrics.sqlite3")
            store.save(first)
            store.save(changed)

            recent = store.recent()

        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0].run_id, "run-1")
        self.assertIsNotNone(recent[0].estimated_cost_usd)

    def test_cli_reports_and_persists_metrics(self) -> None:
        output = io.StringIO()
        with TemporaryDirectory() as temporary_directory:
            trajectory = Path(temporary_directory) / "trajectory.jsonl"
            database = Path(temporary_directory) / "metrics.sqlite3"
            recorder = JsonlTrajectoryRecorder(trajectory)
            for event in self._events():
                recorder.record(event)
            with (
                patch("agentforge.cli.load_project_environment"),
                redirect_stdout(output),
            ):
                exit_code = main(
                    [
                        "trajectory-report",
                        "--trajectory",
                        str(trajectory),
                        "--database",
                        str(database),
                        "--input-price",
                        "1",
                        "--output-price",
                        "2",
                    ]
                )

            document = json.loads(output.getvalue())
            saved = SqliteMetricsStore(database).recent()

        self.assertEqual(exit_code, 0)
        self.assertEqual(document["run_id"], "run-1")
        self.assertEqual(document["database"], str(database.resolve()))
        self.assertEqual(saved[0].total_tokens, 150)

    def test_cli_requires_both_token_prices(self) -> None:
        with self.assertRaisesRegex(SystemExit, "required together"):
            main(
                [
                    "trajectory-report",
                    "--trajectory",
                    "unused.jsonl",
                    "--input-price",
                    "1",
                ]
            )
