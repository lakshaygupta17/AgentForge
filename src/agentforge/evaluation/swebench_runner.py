"""High-level, resumable SWE-bench generation and canonical E2B grading."""

from __future__ import annotations

import asyncio
import json
import locale
import os
import sys
import tempfile
import types
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime
from importlib import import_module
from pathlib import Path
from typing import Any

from agentforge.evaluation.batch import (
    BatchRunSummary,
    CaseStatus,
    RepairCaseResult,
    _append_batch_result,
    load_batch_ledger,
    run_case_batches,
)
from agentforge.evaluation.swebench import SweBenchCase, write_swebench_subset
from agentforge.evaluation.swebench_e2b import (
    combine_prediction_parts,
    ensure_swebench_template,
    is_retryable_generation_failure,
    probe_model_routes,
    run_agentforge_prediction,
)
from agentforge.llm.budget import ModelPricing, SharedInferenceBudget


def prepare_template_wave(cases: tuple[SweBenchCase, ...], *, workers: int = 4) -> dict[str, bool]:
    """Prepare only this wave's templates with bounded process concurrency."""

    if not 1 <= workers <= 20:
        raise ValueError("Template workers must be between 1 and 20")
    prepared: dict[str, bool] = {}
    if workers == 1:
        for case in cases:
            _, built = ensure_swebench_template(case.instance_id)
            prepared[case.instance_id] = built
        return prepared
    failures: list[str] = []
    with ProcessPoolExecutor(max_workers=min(workers, len(cases))) as pool:
        futures = {
            pool.submit(ensure_swebench_template, case.instance_id): case.instance_id
            for case in cases
        }
        for future in as_completed(futures):
            instance_id = futures[future]
            try:
                _, built = future.result()
                prepared[instance_id] = built
            except Exception as error:  # noqa: BLE001 - report every failed template
                failures.append(f"{instance_id}: {type(error).__name__}: {error}")
    if failures:
        raise RuntimeError("Template preparation failed: " + " | ".join(failures))
    return prepared


def run_generation_batches(
    cases: tuple[SweBenchCase, ...],
    *,
    output_directory: Path,
    batch_size: int = 20,
    template_workers: int = 4,
    maximum_repair_attempts: int = 3,
    economy_model: str | None = None,
    repair_model: str | None = None,
    economy_pricing: ModelPricing | None = None,
    repair_pricing: ModelPricing | None = None,
    maximum_model_requests: int | None = None,
    maximum_model_tokens: int | None = None,
    maximum_model_cost_usd: float | None = None,
    maximum_output_tokens: int = 4_096,
    economy_timeout_seconds: float = 30.0,
    repair_timeout_seconds: float = 180.0,
    maximum_case_model_requests: int | None = None,
    maximum_case_model_tokens: int | None = None,
    maximum_case_model_cost_usd: float | None = None,
) -> BatchRunSummary:
    """Build and run five ordered waves for a normal 100-case evaluation."""

    output = output_directory.resolve()
    output.mkdir(parents=True, exist_ok=True)
    write_swebench_subset(output / "selected-cases.jsonl", cases)
    budget_values = (
        maximum_model_requests,
        maximum_model_tokens,
        maximum_model_cost_usd,
    )
    if any(value is not None for value in budget_values) and not all(
        value is not None for value in budget_values
    ):
        raise ValueError("All three model budget limits must be provided together")
    budget = (
        SharedInferenceBudget(
            maximum_requests=maximum_model_requests,
            maximum_tokens=maximum_model_tokens,
            maximum_cost_usd=maximum_model_cost_usd,
        )
        if maximum_model_requests is not None
        and maximum_model_tokens is not None
        and maximum_model_cost_usd is not None
        else None
    )

    route_health = None

    def prepare(wave: tuple[SweBenchCase, ...]) -> None:
        nonlocal route_health
        if route_health is None:
            broker_model = os.environ.get("AGENTFORGE_MODEL")
            economy_name = economy_model or broker_model
            repair_name = repair_model or broker_model
            if not economy_name or not repair_name:
                raise ValueError("Model routes must be configured before the health probe")
            route_health = probe_model_routes(
                economy_model=economy_name,
                repair_model=repair_name,
                economy_pricing=economy_pricing,
                repair_pricing=repair_pricing,
                inference_budget=budget,
                economy_timeout_seconds=economy_timeout_seconds,
                repair_timeout_seconds=repair_timeout_seconds,
            )
            (output / "provider-health.json").write_text(
                json.dumps(asdict(route_health), indent=2) + "\n",
                encoding="utf-8",
            )
        prepare_template_wave(wave, workers=template_workers)

    summary = run_case_batches(
        cases,
        lambda case: run_agentforge_prediction(
            case,
            output_directory=output,
            maximum_repair_attempts=maximum_repair_attempts,
            economy_model=economy_model,
            repair_model=repair_model,
            economy_pricing=economy_pricing,
            repair_pricing=repair_pricing,
            inference_budget=budget,
            maximum_output_tokens=maximum_output_tokens,
            economy_available=(
                route_health.economy_available if route_health is not None else True
            ),
            economy_timeout_seconds=economy_timeout_seconds,
            repair_timeout_seconds=repair_timeout_seconds,
            maximum_case_model_requests=maximum_case_model_requests,
            maximum_case_model_tokens=maximum_case_model_tokens,
            maximum_case_model_cost_usd=maximum_case_model_cost_usd,
        ),
        ledger_path=output / "generation.jsonl",
        case_limit=len(cases),
        batch_size=batch_size,
        before_wave=prepare,
        should_stop=(lambda: budget.snapshot().exhausted) if budget is not None else None,
    )
    combine_prediction_parts(output, cases)
    (output / "generation-summary.json").write_text(
        json.dumps(
            {
                **asdict(summary),
                "inference_budget": asdict(budget.snapshot()) if budget is not None else None,
                "economy_model": economy_model,
                "repair_model": repair_model,
                "provider_health": asdict(route_health) if route_health is not None else None,
                "per_case_limits": {
                    "requests": maximum_case_model_requests,
                    "tokens": maximum_case_model_tokens,
                    "estimated_cost_usd": maximum_case_model_cost_usd,
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return summary


def grade_prediction_batches(
    cases: tuple[SweBenchCase, ...],
    *,
    predictions_path: Path,
    output_directory: Path,
    batch_size: int = 20,
) -> dict[str, object]:
    """Grade patches in fresh microVMs, never exceeding one wave of 20."""

    if not 1 <= batch_size <= 20:
        raise ValueError("Batch size must be between 1 and 20")
    _configure_utf8_stdio()
    _enable_windows_resource_import()
    dataset_module = import_module("e2b_swebench.dataset")
    runner_module = import_module("e2b_swebench.runner")
    _install_utf8_swebench_grader()
    load_instances = dataset_module.load_instances
    run_many = runner_module.run_many
    summarize = runner_module.summarize

    predictions = _load_predictions(predictions_path)
    by_id = {prediction["instance_id"]: prediction for prediction in predictions}
    ordered = [by_id[case.instance_id] for case in cases]
    instances = load_instances()
    output = output_directory.resolve()
    output.mkdir(parents=True, exist_ok=True)
    verdicts_path = output / "verdicts.jsonl"
    existing = _load_jsonl(verdicts_path) if verdicts_path.exists() else []
    retained = [verdict for verdict in existing if not verdict.get("error")]
    if retained != existing:
        verdicts_path.write_text(
            "".join(json.dumps(verdict, separators=(",", ":")) + "\n" for verdict in retained),
            encoding="utf-8",
        )
    completed_ids = {str(verdict["instance_id"]) for verdict in retained}
    pending = [item for item in ordered if item["instance_id"] not in completed_ids]

    for offset in range(0, len(pending), batch_size):
        wave = pending[offset : offset + batch_size]
        asyncio.run(
            run_many(
                instances,
                wave,
                concurrency=min(batch_size, len(wave)),
                result_path=str(verdicts_path),
            )
        )

    verdicts = _load_jsonl(verdicts_path)
    report: dict[str, object] = summarize(verdicts)
    (output / "report.json").write_text(
        json.dumps({"report": report, "verdicts": verdicts}, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def _configure_utf8_stdio() -> None:
    """Prevent Windows legacy console encodings from corrupting grader output."""

    if sys.platform != "win32":
        return
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="backslashreplace")


def _install_utf8_swebench_grader() -> None:
    """Make the harness's temporary log compatible with its locale-based reader."""

    if sys.platform != "win32":
        return
    driver_module = import_module("e2b_swebench.driver")
    grading_module = import_module("swebench.harness.grading")
    log_encoding = locale.getpreferredencoding(False) or "utf-8"

    def grade(test_spec: Any, prediction: dict[str, Any], output: str) -> dict[str, Any]:
        with tempfile.NamedTemporaryFile(
            "w",
            suffix=".txt",
            delete=False,
            encoding=log_encoding,
            errors="replace",
        ) as output_file:
            output_file.write(output)
            log_path = output_file.name
        try:
            report = grading_module.get_eval_report(
                test_spec,
                prediction,
                log_path,
                True,
            )
        finally:
            os.unlink(log_path)
        verdict = report[prediction["instance_id"]]
        if not isinstance(verdict, dict):
            raise TypeError("SWE-bench grader returned an invalid verdict")
        return verdict

    driver_module.__dict__["_grade"] = grade


def recover_interrupted_generation(
    cases: tuple[SweBenchCase, ...], *, output_directory: Path
) -> int:
    """Checkpoint terminal per-case artifacts left by an interrupted wave."""

    output = output_directory.resolve()
    ledger_path = output / "generation.jsonl"
    saved = load_batch_ledger(ledger_path) if ledger_path.exists() else {}
    recovered = 0
    for case in cases:
        if case.instance_id in saved:
            continue
        prediction_path = output / "prediction-parts" / f"{case.instance_id}.json"
        trajectory_path = output / "trajectories" / f"{case.instance_id}.jsonl"
        if not prediction_path.exists() or not trajectory_path.exists():
            continue
        prediction = json.loads(prediction_path.read_text(encoding="utf-8"))
        events = _load_jsonl(trajectory_path)
        terminal = next(
            (
                event
                for event in reversed(events)
                if event.get("event_type") in {"workflow_completed", "workflow_failed"}
            ),
            None,
        )
        if terminal is None or not events:
            continue
        patch = prediction.get("model_patch")
        if not isinstance(patch, str):
            continue
        terminal_data = terminal.get("data")
        error = (
            terminal_data.get("failure_reason")
            if isinstance(terminal_data, dict)
            and isinstance(terminal_data.get("failure_reason"), str)
            else None
        )
        duration = max(
            0.0,
            (
                datetime.fromisoformat(str(terminal["occurred_at"]))
                - datetime.fromisoformat(str(events[0]["occurred_at"]))
            ).total_seconds(),
        )
        attempts = sum(event.get("event_type") == "repair_attempt_started" for event in events)
        tokens = sum(
            int(data.get("total_tokens", 0))
            for event in events
            if event.get("event_type") == "llm_responded"
            and isinstance((data := event.get("data")), dict)
        )
        accepted = terminal.get("event_type") == "workflow_completed" and bool(patch.strip())
        status: CaseStatus = (
            "error"
            if is_retryable_generation_failure(error)
            else "resolved"
            if accepted
            else "unresolved"
        )
        _append_batch_result(
            ledger_path,
            RepairCaseResult(
                case.instance_id,
                status,
                duration,
                repair_attempts=attempts,
                total_tokens=tokens,
                trajectory=str(trajectory_path),
                error=error,
            ),
        )
        recovered += 1
    return recovered


def _load_predictions(path: Path) -> list[dict[str, str]]:
    documents = _load_jsonl(path)
    predictions: list[dict[str, str]] = []
    for document in documents:
        required = ("instance_id", "model_name_or_path", "model_patch")
        if not all(isinstance(document.get(key), str) for key in required):
            raise ValueError("Each prediction requires three string fields")
        predictions.append({key: str(document[key]) for key in required})
    return predictions


def _load_jsonl(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.resolve().read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _enable_windows_resource_import() -> None:
    """SWE-bench imports Unix ``resource`` through an unused Docker module."""

    if os.name != "nt" or "resource" in sys.modules:
        return
    module = types.ModuleType("resource")
    module.RLIMIT_NOFILE = 7  # type: ignore[attr-defined]
    module.setrlimit = lambda *_args: None  # type: ignore[attr-defined]
    sys.modules["resource"] = module
