import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from agentforge.cli import main
from agentforge.evaluation.comparison import (
    ComparisonError,
    VariantRun,
    compare_variants,
    load_variant_runs,
)


class VariantComparisonTests(TestCase):
    def test_aggregates_variants_and_reports_ties_honestly(self) -> None:
        runs = (
            VariantRun("model-a", "strict", True, 2, 1, 100, 0.01),
            VariantRun("model-a", "strict", False, 4, 2, 200, 0.03),
            VariantRun("model-b", "strict", True, 3, 1, 120, None),
        )

        report = compare_variants(runs)

        self.assertEqual(len(report.variants), 2)
        first = report.variants[0]
        self.assertEqual(first.runs, 2)
        self.assertEqual(first.success_rate, 0.5)
        self.assertEqual(first.average_duration_seconds, 3)
        self.assertEqual(first.average_estimated_cost_usd, 0.02)
        self.assertEqual(report.highest_success_rate, ("model-b | strict",))
        self.assertEqual(
            report.fastest_average, ("model-a | strict", "model-b | strict")
        )
        self.assertEqual(report.lowest_known_cost, ("model-a | strict",))

    def test_loader_validates_every_jsonl_line(self) -> None:
        valid = {
            "model": "model-a",
            "policy": "strict",
            "succeeded": True,
            "duration_seconds": 1,
            "repair_attempts": 1,
            "total_tokens": 50,
        }
        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "runs.jsonl"
            path.write_text(json.dumps(valid) + "\nnot-json\n", encoding="utf-8")

            with self.assertRaisesRegex(ComparisonError, "line 2"):
                load_variant_runs(path)

    def test_cli_prints_machine_readable_comparison(self) -> None:
        document = {
            "model": "model-a",
            "policy": "default",
            "succeeded": True,
            "duration_seconds": 1,
            "repair_attempts": 1,
            "total_tokens": 0,
            "estimated_cost_usd": 0,
        }
        output = io.StringIO()
        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "runs.jsonl"
            path.write_text(json.dumps(document) + "\n", encoding="utf-8")
            with (
                patch("agentforge.cli.load_project_environment"),
                redirect_stdout(output),
            ):
                exit_code = main(["compare-runs", "--input", str(path)])

        report = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(report["highest_success_rate"], ["model-a | default"])
