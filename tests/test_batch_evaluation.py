import json
import threading
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from agentforge.evaluation.batch import (
    BatchLedgerError,
    RepairCaseResult,
    load_batch_ledger,
    partition_cases,
    run_case_batches,
)
from agentforge.evaluation.swebench import SweBenchCase, select_balanced_swebench_cases
from agentforge.evaluation.swebench_runner import (
    _install_utf8_swebench_grader,
    recover_interrupted_generation,
)


def repair_case(index: int) -> SweBenchCase:
    return SweBenchCase(
        f"project__issue-{index:03d}",
        "owner/project",
        "abcdef1",
        f"Repair issue {index}",
        (f"test_{index}",),
        (),
    )


class BatchEvaluationTests(TestCase):
    def test_windows_grader_log_round_trips_through_locale_reader(self) -> None:
        driver = SimpleNamespace()
        grading = SimpleNamespace()

        def get_eval_report(_spec, prediction, log_path, _include_status):
            with open(log_path) as log_file:
                log_file.read()
            return {prediction["instance_id"]: {"resolved": False}}

        grading.get_eval_report = get_eval_report

        def load_module(name: str):
            return driver if name == "e2b_swebench.driver" else grading

        with (
            patch("agentforge.evaluation.swebench_runner.sys.platform", "win32"),
            patch(
                "agentforge.evaluation.swebench_runner.import_module",
                side_effect=load_module,
            ),
        ):
            _install_utf8_swebench_grader()

        verdict = driver._grade(  # type: ignore[attr-defined]
            object(),
            {"instance_id": "project__issue-1"},
            "ANSI-safe output plus a Unicode check mark: ✓",
        )

        self.assertEqual(verdict, {"resolved": False})

    def test_balanced_selection_round_robins_repositories(self) -> None:
        rows = []
        for repository, count in (("owner/a", 4), ("owner/b", 2), ("owner/c", 1)):
            for index in range(count):
                rows.append(
                    {
                        "instance_id": f"{repository.replace('/', '__')}-{index}",
                        "repo": repository,
                        "base_commit": "abcdef1",
                        "problem_statement": "Repair the issue",
                        "FAIL_TO_PASS": ["test_failure"],
                        "PASS_TO_PASS": [],
                    }
                )

        cases = select_balanced_swebench_cases(rows, limit=6)

        self.assertEqual(
            [case.repository for case in cases],
            ["owner/a", "owner/b", "owner/c", "owner/a", "owner/b", "owner/a"],
        )

    def test_partitions_one_hundred_cases_into_five_waves_of_twenty(self) -> None:
        waves = partition_cases(tuple(repair_case(index) for index in range(125)))

        self.assertEqual([len(wave) for wave in waves], [20, 20, 20, 20, 20])
        self.assertEqual(waves[-1][-1].instance_id, "project__issue-099")

    def test_rejects_more_than_twenty_concurrent_cases(self) -> None:
        with self.assertRaisesRegex(ValueError, "between 1 and 20"):
            partition_cases((repair_case(1),), batch_size=21)

    def test_runs_waves_sequentially_and_isolates_case_errors(self) -> None:
        cases = tuple(repair_case(index) for index in range(4))
        barriers = (threading.Barrier(2), threading.Barrier(2))
        completed: set[str] = set()
        lock = threading.Lock()
        second_wave_started_early = False

        def execute(case: SweBenchCase) -> RepairCaseResult:
            nonlocal second_wave_started_early
            index = int(case.instance_id.rsplit("-", 1)[1])
            wave_index = index // 2
            with lock:
                if wave_index == 1 and len(completed) != 2:
                    second_wave_started_early = True
            barriers[wave_index].wait(timeout=2)
            if index == 2:
                raise RuntimeError("isolated failure")
            with lock:
                completed.add(case.instance_id)
            return RepairCaseResult(case.instance_id, "resolved", 0.01)

        with TemporaryDirectory() as directory:
            ledger = Path(directory) / "results.jsonl"
            summary = run_case_batches(cases, execute, ledger_path=ledger, batch_size=2)
            saved = load_batch_ledger(ledger)

        self.assertFalse(second_wave_started_early)
        self.assertEqual(summary.batches_started, 2)
        self.assertEqual(summary.resolved_cases, 3)
        self.assertEqual(summary.error_cases, 1)
        self.assertIn("RuntimeError", saved[cases[2].instance_id].error or "")

    def test_resume_skips_finished_cases_and_retries_errors(self) -> None:
        cases = tuple(repair_case(index) for index in range(3))
        calls: list[str] = []

        def first_run(case: SweBenchCase) -> RepairCaseResult:
            status = "error" if case is cases[1] else "resolved"
            return RepairCaseResult(case.instance_id, status, 0.01)

        def resumed_run(case: SweBenchCase) -> RepairCaseResult:
            calls.append(case.instance_id)
            return RepairCaseResult(case.instance_id, "unresolved", 0.02)

        with TemporaryDirectory() as directory:
            ledger = Path(directory) / "results.jsonl"
            run_case_batches(cases, first_run, ledger_path=ledger, batch_size=2)
            summary = run_case_batches(cases, resumed_run, ledger_path=ledger, batch_size=2)

        self.assertEqual(calls, [cases[1].instance_id])
        self.assertEqual(summary.resumed_cases, 2)
        self.assertEqual(summary.resolved_cases, 2)
        self.assertEqual(summary.unresolved_cases, 1)

    def test_prepares_only_the_pending_cases_in_each_wave(self) -> None:
        cases = tuple(repair_case(index) for index in range(5))
        prepared: list[list[str]] = []

        def execute(case: SweBenchCase) -> RepairCaseResult:
            return RepairCaseResult(case.instance_id, "resolved", 0.01)

        with TemporaryDirectory() as directory:
            run_case_batches(
                cases,
                execute,
                ledger_path=Path(directory) / "results.jsonl",
                batch_size=2,
                before_wave=lambda wave: prepared.append([case.instance_id for case in wave]),
            )

        self.assertEqual([len(wave) for wave in prepared], [2, 2, 1])

    def test_checkpoints_a_finished_case_before_its_wave_finishes(self) -> None:
        cases = (repair_case(0), repair_case(1))
        release_slow_case = threading.Event()
        first_checkpointed = threading.Event()

        def execute(case: SweBenchCase) -> RepairCaseResult:
            if case is cases[1]:
                release_slow_case.wait(timeout=2)
            return RepairCaseResult(case.instance_id, "resolved", 0.01)

        with TemporaryDirectory() as directory:
            ledger = Path(directory) / "results.jsonl"
            from agentforge.evaluation import batch

            append_result = batch._append_batch_result

            def track_checkpoint(path: Path, result: RepairCaseResult) -> None:
                append_result(path, result)
                if result.instance_id == cases[0].instance_id:
                    first_checkpointed.set()

            with patch.object(batch, "_append_batch_result", track_checkpoint):
                runner = threading.Thread(
                    target=run_case_batches,
                    args=(cases, execute),
                    kwargs={"ledger_path": ledger, "batch_size": 2},
                )
                runner.start()
                self.assertTrue(first_checkpointed.wait(timeout=2))
                self.assertEqual(len(load_batch_ledger(ledger)), 1)
                release_slow_case.set()
                runner.join(timeout=2)

        self.assertFalse(runner.is_alive())

    def test_stop_check_prevents_later_waves_without_losing_summary(self) -> None:
        cases = tuple(repair_case(index) for index in range(4))
        calls: list[str] = []

        def execute(case: SweBenchCase) -> RepairCaseResult:
            calls.append(case.instance_id)
            return RepairCaseResult(case.instance_id, "resolved", 0.01)

        with TemporaryDirectory() as directory:
            summary = run_case_batches(
                cases,
                execute,
                ledger_path=Path(directory) / "results.jsonl",
                batch_size=2,
                should_stop=lambda: len(calls) >= 2,
            )

        self.assertEqual(calls, [case.instance_id for case in cases[:2]])
        self.assertEqual(summary.completed_cases, 2)
        self.assertEqual(summary.batches_started, 1)

    def test_recovers_terminal_artifacts_from_an_interrupted_wave(self) -> None:
        case = repair_case(7)
        with TemporaryDirectory() as directory:
            output = Path(directory)
            parts = output / "prediction-parts"
            trajectories = output / "trajectories"
            parts.mkdir()
            trajectories.mkdir()
            (parts / f"{case.instance_id}.json").write_text(
                json.dumps(
                    {
                        "instance_id": case.instance_id,
                        "model_name_or_path": "model",
                        "model_patch": "diff --git a/a.py b/a.py\n",
                    }
                ),
                encoding="utf-8",
            )
            events = (
                {
                    "event_type": "workflow_started",
                    "occurred_at": "2026-09-13T10:00:00+00:00",
                    "data": {},
                },
                {
                    "event_type": "repair_attempt_started",
                    "occurred_at": "2026-09-13T10:00:01+00:00",
                    "data": {},
                },
                {
                    "event_type": "llm_responded",
                    "occurred_at": "2026-09-13T10:00:02+00:00",
                    "data": {"total_tokens": 123},
                },
                {
                    "event_type": "workflow_completed",
                    "occurred_at": "2026-09-13T10:00:03+00:00",
                    "data": {"failure_reason": None},
                },
            )
            (trajectories / f"{case.instance_id}.jsonl").write_text(
                "\n".join(json.dumps(event) for event in events) + "\n",
                encoding="utf-8",
            )

            recovered = recover_interrupted_generation((case,), output_directory=output)
            saved = load_batch_ledger(output / "generation.jsonl")[case.instance_id]

        self.assertEqual(recovered, 1)
        self.assertEqual(saved.status, "resolved")
        self.assertEqual(saved.repair_attempts, 1)
        self.assertEqual(saved.total_tokens, 123)
        self.assertEqual(saved.duration_seconds, 3.0)

    def test_rejects_a_malformed_ledger_with_its_line_number(self) -> None:
        with TemporaryDirectory() as directory:
            ledger = Path(directory) / "results.jsonl"
            ledger.write_text(json.dumps({"schema_version": 99}) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(BatchLedgerError, "line 1"):
                load_batch_ledger(ledger)
