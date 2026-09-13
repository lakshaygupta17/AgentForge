import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from agentforge.cli import main
from agentforge.evaluation.repair import (
    BenchmarkHistoryError,
    RepairBenchmarkRecord,
    RepairBenchmarkResult,
    RepairBenchmarkSuiteResult,
    append_repair_benchmark,
    append_repair_benchmark_suite,
    load_repair_benchmark_suites,
    load_repair_benchmarks,
    run_repair_benchmark_suite,
    run_seeded_repair_benchmark,
    run_seeded_retry_benchmark,
    run_seeded_review_retry_benchmark,
    summarize_repair_benchmark_suites,
    summarize_repair_benchmarks,
)


class RepairBenchmarkTests(TestCase):
    @staticmethod
    def _result(
        *,
        succeeded: bool = True,
        tests_passed: bool = True,
        review_accepted: bool = True,
        repair_attempts: int = 1,
        duration_seconds: float = 0.4,
        case_name: str = "parity_direct",
    ) -> RepairBenchmarkResult:
        return RepairBenchmarkResult(
            succeeded=succeeded,
            tests_passed=tests_passed,
            review_accepted=review_accepted,
            repair_attempts=repair_attempts,
            completed_agent_steps=5,
            trajectory_events=36,
            duration_seconds=duration_seconds,
            case_name=case_name,
        )

    @classmethod
    def _suite(
        cls,
        *cases: RepairBenchmarkResult,
    ) -> RepairBenchmarkSuiteResult:
        selected = cases or (
            cls._result(),
            cls._result(repair_attempts=2, case_name="parity_debug_retry"),
        )
        passed = sum(case.succeeded for case in selected)
        return RepairBenchmarkSuiteResult(
            succeeded=passed == len(selected),
            case_count=len(selected),
            passed_cases=passed,
            total_repair_attempts=sum(case.repair_attempts for case in selected),
            total_duration_seconds=sum(case.duration_seconds for case in selected),
            cases=tuple(selected),
        )

    def test_seeded_benchmark_repairs_tests_and_reviews_fixture(self) -> None:
        result = run_seeded_repair_benchmark()

        self.assertTrue(result.succeeded)
        self.assertTrue(result.tests_passed)
        self.assertTrue(result.review_accepted)
        self.assertEqual(result.repair_attempts, 1)
        self.assertEqual(result.completed_agent_steps, 5)
        self.assertGreater(result.trajectory_events, 0)
        self.assertGreaterEqual(result.duration_seconds, 0)

    def test_retry_benchmark_diagnoses_failure_and_repairs_on_second_attempt(
        self,
    ) -> None:
        result = run_seeded_retry_benchmark()

        self.assertTrue(result.succeeded)
        self.assertTrue(result.tests_passed)
        self.assertTrue(result.review_accepted)
        self.assertEqual(result.case_name, "parity_debug_retry")
        self.assertEqual(result.repair_attempts, 2)
        self.assertEqual(result.completed_agent_steps, 7)

    def test_benchmark_suite_aggregates_all_cases(self) -> None:
        result = run_repair_benchmark_suite()

        self.assertTrue(result.succeeded)
        self.assertEqual(result.case_count, 3)
        self.assertEqual(result.passed_cases, 3)
        self.assertEqual(result.total_repair_attempts, 5)
        self.assertEqual(
            [case.case_name for case in result.cases],
            ["parity_direct", "parity_debug_retry", "parity_review_retry"],
        )

    def test_review_retry_benchmark_recovers_after_rejection(self) -> None:
        result = run_seeded_review_retry_benchmark()

        self.assertTrue(result.succeeded)
        self.assertTrue(result.tests_passed)
        self.assertTrue(result.review_accepted)
        self.assertEqual(result.case_name, "parity_review_retry")
        self.assertEqual(result.repair_attempts, 2)
        self.assertEqual(result.completed_agent_steps, 7)

    def test_cli_prints_machine_readable_suite_result(self) -> None:
        output = io.StringIO()
        with TemporaryDirectory() as temporary_directory:
            history = Path(temporary_directory) / "suite.jsonl"
            with (
                patch("agentforge.cli.load_project_environment"),
                redirect_stdout(output),
            ):
                exit_code = main(["benchmark-suite", "--history", str(history)])

        document = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertTrue(document["succeeded"])
        self.assertEqual(document["case_count"], 3)
        self.assertEqual(document["total_repair_attempts"], 5)
        self.assertEqual(document["history"], str(history.resolve()))

    def test_suite_history_round_trip_and_clean_summary(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            history = Path(temporary_directory) / "nested" / "suite.jsonl"
            suite = self._suite()
            append_repair_benchmark_suite(history, suite)
            append_repair_benchmark_suite(history, suite)

            records = load_repair_benchmark_suites(history)
            summary = summarize_repair_benchmark_suites(records)

        self.assertEqual(len(records), 2)
        self.assertEqual(summary.record_count, 2)
        self.assertEqual(summary.successful_runs, 2)
        self.assertEqual(summary.latest_passed_cases, 2)
        self.assertFalse(summary.regression_detected)

    def test_suite_summary_detects_case_specific_regressions(self) -> None:
        healthy = self._suite()
        regressed = self._suite(
            self._result(succeeded=False, tests_passed=False, repair_attempts=2)
        )
        with TemporaryDirectory() as temporary_directory:
            history = Path(temporary_directory) / "suite.jsonl"
            append_repair_benchmark_suite(history, healthy)
            append_repair_benchmark_suite(history, regressed)

            summary = summarize_repair_benchmark_suites(
                load_repair_benchmark_suites(history)
            )

        self.assertTrue(summary.regression_detected)
        self.assertIn(
            "parity_direct: workflow no longer succeeds",
            summary.functional_regressions,
        )
        self.assertIn(
            "parity_direct: tests no longer pass", summary.functional_regressions
        )
        self.assertIn(
            "parity_direct: repair attempts increased",
            summary.functional_regressions,
        )
        self.assertIn(
            "parity_debug_retry: case is missing", summary.functional_regressions
        )

    def test_suite_report_cli_reads_separate_history(self) -> None:
        output = io.StringIO()
        with TemporaryDirectory() as temporary_directory:
            history = Path(temporary_directory) / "suite.jsonl"
            append_repair_benchmark_suite(history, self._suite())
            with (
                patch("agentforge.cli.load_project_environment"),
                redirect_stdout(output),
            ):
                exit_code = main(
                    ["benchmark-suite-report", "--history", str(history)]
                )

        document = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(document["latest_case_count"], 2)
        self.assertEqual(document["functional_regressions"], [])

    def test_cli_prints_machine_readable_benchmark_result(self) -> None:
        output = io.StringIO()
        with TemporaryDirectory() as temporary_directory:
            history = Path(temporary_directory) / "history.jsonl"
            with (
                patch("agentforge.cli.load_project_environment"),
                redirect_stdout(output),
            ):
                exit_code = main(
                    ["benchmark-repair", "--history", str(history)]
                )

            document = json.loads(output.getvalue())
            saved = json.loads(history.read_text(encoding="utf-8"))

            self.assertEqual(exit_code, 0)
            self.assertTrue(document["succeeded"])
            self.assertEqual(document["repair_attempts"], 1)
            self.assertEqual(document["history"], str(history.resolve()))
            self.assertEqual(saved["schema_version"], 1)
            self.assertTrue(saved["result"]["succeeded"])

    def test_history_writer_appends_records(self) -> None:
        result = run_seeded_repair_benchmark()
        with TemporaryDirectory() as temporary_directory:
            history = Path(temporary_directory) / "nested" / "history.jsonl"

            first = append_repair_benchmark(history, result)
            append_repair_benchmark(history, result)
            records = [
                json.loads(line)
                for line in history.read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(first.schema_version, 1)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["result"], records[1]["result"])

    def test_loads_and_summarizes_valid_history(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            history = Path(temporary_directory) / "history.jsonl"
            append_repair_benchmark(history, self._result(duration_seconds=0.4))
            append_repair_benchmark(history, self._result(duration_seconds=0.5))

            records = load_repair_benchmarks(history)
            summary = summarize_repair_benchmarks(records)

        self.assertEqual(summary.record_count, 2)
        self.assertEqual(summary.successful_runs, 2)
        self.assertEqual(summary.success_rate, 1)
        self.assertAlmostEqual(summary.average_duration_seconds, 0.45)
        self.assertAlmostEqual(summary.duration_change_seconds or 0, 0.1)
        self.assertFalse(summary.performance_warning)
        self.assertFalse(summary.regression_detected)

    def test_loads_history_written_before_case_names_were_added(self) -> None:
        old_record = {
            "schema_version": 1,
            "recorded_at": "2026-01-01T00:00:00+00:00",
            "result": {
                "succeeded": True,
                "tests_passed": True,
                "review_accepted": True,
                "repair_attempts": 1,
                "completed_agent_steps": 5,
                "trajectory_events": 36,
                "duration_seconds": 0.4,
            },
        }
        with TemporaryDirectory() as temporary_directory:
            history = Path(temporary_directory) / "history.jsonl"
            history.write_text(json.dumps(old_record) + "\n", encoding="utf-8")

            records = load_repair_benchmarks(history)

        self.assertEqual(records[0].result.case_name, "parity_direct")

    def test_detects_functional_and_performance_regressions(self) -> None:
        records = (
            RepairBenchmarkRecord(1, "2026-01-01T00:00:00+00:00", self._result()),
            RepairBenchmarkRecord(
                1,
                "2026-01-02T00:00:00+00:00",
                self._result(
                    succeeded=False,
                    tests_passed=False,
                    review_accepted=False,
                    repair_attempts=2,
                    duration_seconds=1,
                ),
            ),
        )

        summary = summarize_repair_benchmarks(records)

        self.assertTrue(summary.regression_detected)
        self.assertTrue(summary.performance_warning)
        self.assertEqual(len(summary.functional_regressions), 4)

    def test_rejects_malformed_history_with_line_number(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            history = Path(temporary_directory) / "history.jsonl"
            history.write_text("not-json\n", encoding="utf-8")

            with self.assertRaisesRegex(BenchmarkHistoryError, "line 1"):
                load_repair_benchmarks(history)

    def test_report_cli_summarizes_history(self) -> None:
        output = io.StringIO()
        with TemporaryDirectory() as temporary_directory:
            history = Path(temporary_directory) / "history.jsonl"
            append_repair_benchmark(history, self._result())
            with (
                patch("agentforge.cli.load_project_environment"),
                redirect_stdout(output),
            ):
                exit_code = main(["benchmark-report", "--history", str(history)])

        document = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(document["record_count"], 1)
        self.assertEqual(document["functional_regressions"], [])
