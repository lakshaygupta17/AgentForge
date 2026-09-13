"""Honest aggregate comparisons across explicitly labelled model/policy runs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


class ComparisonError(ValueError):
    """Comparison input is absent, malformed, or cannot be compared."""


@dataclass(frozen=True, slots=True)
class VariantRun:
    model: str
    policy: str
    succeeded: bool
    duration_seconds: float
    repair_attempts: int
    total_tokens: int
    estimated_cost_usd: float | None = None


@dataclass(frozen=True, slots=True)
class VariantSummary:
    model: str
    policy: str
    runs: int
    successful_runs: int
    success_rate: float
    average_duration_seconds: float
    average_repair_attempts: float
    average_total_tokens: float
    average_estimated_cost_usd: float | None


@dataclass(frozen=True, slots=True)
class ComparisonReport:
    variants: tuple[VariantSummary, ...]
    highest_success_rate: tuple[str, ...]
    fastest_average: tuple[str, ...]
    lowest_known_cost: tuple[str, ...]


def load_variant_runs(path: Path, *, maximum_runs: int = 1_000) -> tuple[VariantRun, ...]:
    if maximum_runs <= 0:
        raise ValueError("Maximum comparison runs must be greater than zero")
    try:
        lines = path.resolve().read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise ComparisonError(f"Could not read comparison runs: {error}") from error
    if not lines:
        raise ComparisonError("Comparison input is empty")
    if len(lines) > maximum_runs:
        raise ComparisonError(f"Comparison input exceeds the {maximum_runs}-run limit")
    runs: list[VariantRun] = []
    for line_number, line in enumerate(lines, start=1):
        try:
            runs.append(_parse_run(json.loads(line)))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            raise ComparisonError(
                f"Invalid comparison run on line {line_number}: {error}"
            ) from error
    return tuple(runs)


def compare_variants(runs: tuple[VariantRun, ...]) -> ComparisonReport:
    if not runs:
        raise ComparisonError("At least one comparison run is required")
    grouped: dict[tuple[str, str], list[VariantRun]] = {}
    for run in runs:
        grouped.setdefault((run.model, run.policy), []).append(run)
    summaries: list[VariantSummary] = []
    for (model, policy), variant_runs in sorted(grouped.items()):
        costs = [
            run.estimated_cost_usd
            for run in variant_runs
            if run.estimated_cost_usd is not None
        ]
        summaries.append(
            VariantSummary(
                model=model,
                policy=policy,
                runs=len(variant_runs),
                successful_runs=sum(run.succeeded for run in variant_runs),
                success_rate=sum(run.succeeded for run in variant_runs) / len(variant_runs),
                average_duration_seconds=sum(
                    run.duration_seconds for run in variant_runs
                )
                / len(variant_runs),
                average_repair_attempts=sum(
                    run.repair_attempts for run in variant_runs
                )
                / len(variant_runs),
                average_total_tokens=sum(run.total_tokens for run in variant_runs)
                / len(variant_runs),
                average_estimated_cost_usd=(
                    sum(costs) / len(costs) if len(costs) == len(variant_runs) else None
                ),
            )
        )
    return ComparisonReport(
        variants=tuple(summaries),
        highest_success_rate=_winners(summaries, "success_rate", highest=True),
        fastest_average=_winners(summaries, "average_duration_seconds", highest=False),
        lowest_known_cost=_known_cost_winners(summaries),
    )


def _parse_run(document: object) -> VariantRun:
    if not isinstance(document, dict):
        raise TypeError("run must be an object")
    model = document["model"]
    policy = document["policy"]
    succeeded = document["succeeded"]
    if not isinstance(model, str) or not model.strip():
        raise TypeError("model must be non-empty text")
    if not isinstance(policy, str) or not policy.strip():
        raise TypeError("policy must be non-empty text")
    if not isinstance(succeeded, bool):
        raise TypeError("succeeded must be boolean")
    duration = _number(document["duration_seconds"], "duration_seconds")
    attempts = _integer(document["repair_attempts"], "repair_attempts")
    tokens = _integer(document["total_tokens"], "total_tokens")
    raw_cost = document.get("estimated_cost_usd")
    cost = None if raw_cost is None else _number(raw_cost, "estimated_cost_usd")
    return VariantRun(
        model.strip(), policy.strip(), succeeded, duration, attempts, tokens, cost
    )


def _winners(
    summaries: list[VariantSummary], field: str, *, highest: bool
) -> tuple[str, ...]:
    values = [float(getattr(summary, field)) for summary in summaries]
    target = max(values) if highest else min(values)
    return tuple(
        f"{summary.model} | {summary.policy}"
        for summary in summaries
        if float(getattr(summary, field)) == target
    )


def _known_cost_winners(summaries: list[VariantSummary]) -> tuple[str, ...]:
    known = [
        summary
        for summary in summaries
        if summary.average_estimated_cost_usd is not None
    ]
    if not known:
        return ()
    costs = [
        summary.average_estimated_cost_usd
        for summary in known
        if summary.average_estimated_cost_usd is not None
    ]
    target = min(costs)
    return tuple(
        f"{summary.model} | {summary.policy}"
        for summary in known
        if summary.average_estimated_cost_usd == target
    )


def _number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise TypeError(f"{label} must be a non-negative number")
    return float(value)


def _integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TypeError(f"{label} must be a non-negative integer")
    return value
