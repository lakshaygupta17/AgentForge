import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from agentforge.evaluation.swebench import SweBenchSubsetError, load_swebench_subset


class SweBenchSubsetTests(TestCase):
    @staticmethod
    def _case(instance_id: str = "project__issue-1") -> dict[str, object]:
        return {
            "instance_id": instance_id,
            "repo": "owner/project",
            "base_commit": "0123456789abcdef",
            "problem_statement": "Correct the parser's empty-input behavior.",
            "FAIL_TO_PASS": ["tests/test_parser.py::test_empty"],
            "PASS_TO_PASS": "[\"tests/test_parser.py::test_regular\"]",
        }

    def test_loads_bounded_jsonl_metadata_without_execution(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "subset.jsonl"
            path.write_text(json.dumps(self._case()) + "\n", encoding="utf-8")

            cases = load_swebench_subset(path)

        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0].repository, "owner/project")
        self.assertEqual(cases[0].fail_to_pass, ("tests/test_parser.py::test_empty",))
        self.assertEqual(cases[0].pass_to_pass, ("tests/test_parser.py::test_regular",))

    def test_rejects_duplicate_cases_and_oversized_subsets(self) -> None:
        line = json.dumps(self._case())
        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "subset.jsonl"
            path.write_text(f"{line}\n{line}\n", encoding="utf-8")
            with self.assertRaisesRegex(SweBenchSubsetError, "Duplicate"):
                load_swebench_subset(path)
            with self.assertRaisesRegex(SweBenchSubsetError, "1-case"):
                load_swebench_subset(path, maximum_cases=1)

    def test_rejects_invalid_commit_missing_failure_tests_and_long_problem(self) -> None:
        invalid_cases = []
        invalid_commit = self._case("bad-commit")
        invalid_commit["base_commit"] = "main"
        invalid_cases.append(invalid_commit)
        missing_tests = self._case("missing-tests")
        missing_tests["FAIL_TO_PASS"] = []
        invalid_cases.append(missing_tests)
        long_problem = self._case("long-problem")
        long_problem["problem_statement"] = "x" * 20_001
        invalid_cases.append(long_problem)

        with TemporaryDirectory() as temporary_directory:
            for index, document in enumerate(invalid_cases):
                path = Path(temporary_directory) / f"invalid-{index}.jsonl"
                path.write_text(json.dumps(document) + "\n", encoding="utf-8")
                with self.subTest(index=index), self.assertRaises(SweBenchSubsetError):
                    load_swebench_subset(path)
