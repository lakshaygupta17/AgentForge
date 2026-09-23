"""Bounded, resumable execution of independent repair cases in sequential waves."""

from __future__ import annotations

import json
import os
import tempfile
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from agentforge.evaluation.swebench import SweBenchCase

CaseStatus = Literal["resolved", "unresolved", "error"]


class BatchLedgerError(ValueError):
    """A saved batch result is malformed or cannot be persisted safely."""


@dataclass(frozen=True, slots=True)
class RepairCaseResult:
    instance_id: str
    status: CaseStatus
    duration_seconds: float
    repair_attempts: int = 0
    total_tokens: int = 0
    trajectory: str | None = None
    error: str | None = None

    @property
    def resolved(self) -> bool:
        return self.status == "resolved"


@dataclass(frozen=True, slots=True)
class BatchRunSummary:
    selected_cases: int
    completed_cases: int
    resolved_cases: int
    unresolved_cases: int
    error_cases: int
    resumed_cases: int
    batches_started: int
    batch_size: int

    @property
    def success_rate(self) -> float:
        return self.resolved_cases / self.completed_cases if self.completed_cases else 0.0


CaseExecutor = Callable[[SweBenchCase], RepairCaseResult]
WaveHook = Callable[[tuple[SweBenchCase, ...]], None]
StopCheck = Callable[[], bool]


def partition_cases(
    cases: Sequence[SweBenchCase], *, case_limit: int = 100, batch_size: int = 20
) -> tuple[tuple[SweBenchCase, ...], ...]:
    """Select at most ``case_limit`` cases and split them into ordered waves."""

    if case_limit <= 0:
        raise ValueError("Case limit must be greater than zero")
    if not 1 <= batch_size <= 20:
        raise ValueError("Batch size must be between 1 and 20")
    selected = tuple(cases[:case_limit])
    identifiers = [case.instance_id for case in selected]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("Selected repair cases must have unique instance IDs")
    return tuple(
        selected[offset : offset + batch_size] for offset in range(0, len(selected), batch_size)
    )


def run_case_batches(
    cases: Sequence[SweBenchCase],
    executor: CaseExecutor,
    *,
    ledger_path: Path,
    case_limit: int = 100,
    batch_size: int = 20,
    retry_errors: bool = True,
    before_wave: WaveHook | None = None,
    should_stop: StopCheck | None = None,
) -> BatchRunSummary:
    """Run one wave at a time; at most 20 cases within a wave run concurrently."""

    waves = partition_cases(cases, case_limit=case_limit, batch_size=batch_size)
    selected = tuple(case for wave in waves for case in wave)
    previous = load_batch_ledger(ledger_path) if ledger_path.exists() else {}
    retained = {
        instance_id: result
        for instance_id, result in previous.items()
        if result.status != "error" or not retry_errors
    }
    resumed = sum(case.instance_id in retained for case in selected)
    completed: dict[str, RepairCaseResult] = {
        case.instance_id: retained[case.instance_id]
        for case in selected
        if case.instance_id in retained
    }
    batches_started = 0

    for wave in waves:
        if should_stop is not None and should_stop():
            break
        pending = tuple(case for case in wave if case.instance_id not in retained)
        if not pending:
            continue
        batches_started += 1
        if before_wave is not None:
            before_wave(pending)
        with ThreadPoolExecutor(max_workers=min(batch_size, len(pending))) as pool:
            futures = {pool.submit(_run_case, executor, case): case for case in pending}
            for future in as_completed(futures):
                result = future.result()
                completed[result.instance_id] = result
                _append_batch_result(ledger_path, result)

    ordered = tuple(
        completed[case.instance_id] for case in selected if case.instance_id in completed
    )
    return BatchRunSummary(
        selected_cases=len(selected),
        completed_cases=len(ordered),
        resolved_cases=sum(result.status == "resolved" for result in ordered),
        unresolved_cases=sum(result.status == "unresolved" for result in ordered),
        error_cases=sum(result.status == "error" for result in ordered),
        resumed_cases=resumed,
        batches_started=batches_started,
        batch_size=batch_size,
    )


def load_batch_ledger(path: Path) -> dict[str, RepairCaseResult]:
    """Return the latest valid result per case from an append-only JSONL ledger."""

    try:
        lines = path.resolve().read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise BatchLedgerError(f"Could not read batch ledger: {error}") from error
    results: dict[str, RepairCaseResult] = {}
    for line_number, line in enumerate(lines, start=1):
        try:
            document = json.loads(line)
            result = _parse_result(document)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            raise BatchLedgerError(
                f"Invalid batch ledger record on line {line_number}: {error}"
            ) from error
        results[result.instance_id] = result
    return results


def _run_case(executor: CaseExecutor, case: SweBenchCase) -> RepairCaseResult:
    started = time.monotonic()
    try:
        result = executor(case)
        if result.instance_id != case.instance_id:
            raise ValueError("Case executor returned the wrong instance ID")
        return result
    except Exception as error:  # noqa: BLE001 - one case must not terminate its wave
        return RepairCaseResult(
            instance_id=case.instance_id,
            status="error",
            duration_seconds=time.monotonic() - started,
            error=f"{type(error).__name__}: {error}",
        )


def _append_batch_result(path: Path, result: RepairCaseResult) -> None:
    output = path.resolve()
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        existing = output.read_bytes() if output.exists() else b""
        record = (
            json.dumps(
                {"schema_version": 1, "result": asdict(result)}, separators=(",", ":")
            ).encode("utf-8")
            + b"\n"
        )
        with tempfile.NamedTemporaryFile(
            dir=output.parent, prefix=f".{output.name}.", delete=False
        ) as temporary:
            temporary.write(existing)
            temporary.write(record)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, output)
    except (OSError, TypeError, ValueError) as error:
        raise BatchLedgerError(f"Could not persist batch result: {error}") from error


def _parse_result(document: object) -> RepairCaseResult:
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise ValueError("schema_version must be 1")
    raw = document["result"]
    if not isinstance(raw, dict):
        raise TypeError("result must be an object")
    instance_id = raw["instance_id"]
    status = raw["status"]
    duration = raw["duration_seconds"]
    attempts = raw.get("repair_attempts", 0)
    tokens = raw.get("total_tokens", 0)
    trajectory = raw.get("trajectory")
    error = raw.get("error")
    if not isinstance(instance_id, str) or not instance_id.strip():
        raise TypeError("instance_id must be non-empty text")
    if status not in {"resolved", "unresolved", "error"}:
        raise ValueError("status must be resolved, unresolved, or error")
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or duration < 0:
        raise TypeError("duration_seconds must be a non-negative number")
    if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 0:
        raise TypeError("repair_attempts must be a non-negative integer")
    if isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 0:
        raise TypeError("total_tokens must be a non-negative integer")
    if trajectory is not None and not isinstance(trajectory, str):
        raise TypeError("trajectory must be text or null")
    if error is not None and not isinstance(error, str):
        raise TypeError("error must be text or null")
    return RepairCaseResult(
        instance_id.strip(), status, float(duration), attempts, tokens, trajectory, error
    )
