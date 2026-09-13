"""Validated trajectory metrics and lightweight personal SQLite persistence."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from agentforge.telemetry.trajectory import TrajectoryEvent


class MetricsError(ValueError):
    """A trajectory or metrics operation could not be validated."""


@dataclass(frozen=True, slots=True)
class TokenPricing:
    input_per_million_usd: float
    output_per_million_usd: float

    def __post_init__(self) -> None:
        if self.input_per_million_usd < 0 or self.output_per_million_usd < 0:
            raise ValueError("Token prices cannot be negative")


@dataclass(frozen=True, slots=True)
class RunMetrics:
    run_id: str
    succeeded: bool
    duration_seconds: float
    agent_steps: int
    repair_attempts: int
    retries: int
    test_runs: int
    failed_test_runs: int
    review_rejections: int
    model_requests: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    sandbox_time_seconds: float
    estimated_cost_usd: float | None
    models: tuple[str, ...]


def load_trajectory(path: Path) -> tuple[TrajectoryEvent, ...]:
    """Load one strict JSONL trajectory without accepting partial bad data."""

    try:
        lines = path.resolve().read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise MetricsError(f"Could not read trajectory: {error}") from error
    if not lines:
        raise MetricsError("Trajectory is empty")
    events: list[TrajectoryEvent] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            raise MetricsError(f"Trajectory line {line_number} is empty")
        try:
            events.append(_parse_event(json.loads(line)))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            raise MetricsError(
                f"Invalid trajectory event on line {line_number}: {error}"
            ) from error
    if len({event.run_id for event in events}) != 1:
        raise MetricsError("Trajectory must contain exactly one run id")
    return tuple(events)


def summarize_trajectory(
    events: tuple[TrajectoryEvent, ...],
    *,
    pricing: TokenPricing | None = None,
) -> RunMetrics:
    if not events:
        raise MetricsError("At least one trajectory event is required")
    if len({event.run_id for event in events}) != 1:
        raise MetricsError("Events must belong to exactly one run")
    timestamps = [datetime.fromisoformat(event.occurred_at) for event in events]
    duration = max(0.0, (max(timestamps) - min(timestamps)).total_seconds())
    responded = [event for event in events if event.event_type == "llm_responded"]
    input_tokens = sum(_non_negative_integer(event.data, "input_tokens") for event in responded)
    output_tokens = sum(
        _non_negative_integer(event.data, "output_tokens") for event in responded
    )
    total_tokens = sum(_non_negative_integer(event.data, "total_tokens") for event in responded)
    test_events = [
        event for event in events if event.event_type == "test_execution_completed"
    ]
    sandbox_time = sum(
        _non_negative_float(event.data, "duration_seconds") for event in test_events
    )
    cost = None
    if pricing is not None:
        cost = (
            input_tokens * pricing.input_per_million_usd
            + output_tokens * pricing.output_per_million_usd
        ) / 1_000_000
    models: set[str] = set()
    for event in responded:
        model = event.data.get("model")
        if isinstance(model, str) and model:
            models.add(model)
    event_types = [event.event_type for event in events]
    return RunMetrics(
        run_id=events[0].run_id,
        succeeded="workflow_completed" in event_types,
        duration_seconds=duration,
        agent_steps=event_types.count("agent_completed"),
        repair_attempts=event_types.count("repair_attempt_started"),
        retries=event_types.count("repair_retry_scheduled"),
        test_runs=len(test_events),
        failed_test_runs=event_types.count("tests_failed"),
        review_rejections=event_types.count("review_rejected"),
        model_requests=event_types.count("llm_requested"),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        sandbox_time_seconds=sandbox_time,
        estimated_cost_usd=cost,
        models=tuple(sorted(models)),
    )


class SqliteMetricsStore:
    """Persist one sanitized aggregate row per run using the standard library."""

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def save(self, metrics: RunMetrics) -> None:
        document = json.dumps(asdict(metrics), separators=(",", ":"))
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                """
                INSERT INTO run_metrics (run_id, succeeded, duration_seconds, metrics_json)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    succeeded = excluded.succeeded,
                    duration_seconds = excluded.duration_seconds,
                    metrics_json = excluded.metrics_json
                """,
                (metrics.run_id, int(metrics.succeeded), metrics.duration_seconds, document),
            )

    def recent(self, *, limit: int = 20) -> tuple[RunMetrics, ...]:
        if limit <= 0 or limit > 1_000:
            raise ValueError("Metrics query limit must be between 1 and 1000")
        with closing(sqlite3.connect(self.path)) as connection, connection:
            rows = connection.execute(
                "SELECT metrics_json FROM run_metrics ORDER BY rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return tuple(_parse_metrics(json.loads(row[0])) for row in rows)

    def _initialize(self) -> None:
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS run_metrics (
                    run_id TEXT PRIMARY KEY,
                    succeeded INTEGER NOT NULL CHECK (succeeded IN (0, 1)),
                    duration_seconds REAL NOT NULL CHECK (duration_seconds >= 0),
                    metrics_json TEXT NOT NULL
                )
                """
            )


def _parse_event(document: object) -> TrajectoryEvent:
    if not isinstance(document, dict):
        raise TypeError("event must be an object")
    run_id = document["run_id"]
    event_type = document["event_type"]
    actor = document["actor"]
    data = document.get("data", {})
    occurred_at = document["occurred_at"]
    values = (run_id, event_type, actor)
    if not all(isinstance(value, str) and value.strip() for value in values):
        raise TypeError("run_id, event_type, and actor must be non-empty text")
    if not isinstance(data, dict):
        raise TypeError("data must be an object")
    if not isinstance(occurred_at, str):
        raise TypeError("occurred_at must be text")
    datetime.fromisoformat(occurred_at)
    return TrajectoryEvent(run_id, event_type, actor, data, occurred_at)


def _parse_metrics(document: object) -> RunMetrics:
    if not isinstance(document, dict):
        raise MetricsError("Stored metrics must be an object")
    try:
        models = document["models"]
        if not isinstance(models, list) or not all(isinstance(item, str) for item in models):
            raise TypeError("models must be a list of text")
        succeeded = document["succeeded"]
        if not isinstance(succeeded, bool):
            raise TypeError("succeeded must be boolean")
        return RunMetrics(
            run_id=str(document["run_id"]),
            succeeded=succeeded,
            duration_seconds=float(document["duration_seconds"]),
            agent_steps=int(document["agent_steps"]),
            repair_attempts=int(document["repair_attempts"]),
            retries=int(document["retries"]),
            test_runs=int(document["test_runs"]),
            failed_test_runs=int(document["failed_test_runs"]),
            review_rejections=int(document["review_rejections"]),
            model_requests=int(document["model_requests"]),
            input_tokens=int(document["input_tokens"]),
            output_tokens=int(document["output_tokens"]),
            total_tokens=int(document["total_tokens"]),
            sandbox_time_seconds=float(document["sandbox_time_seconds"]),
            estimated_cost_usd=(
                None
                if document["estimated_cost_usd"] is None
                else float(document["estimated_cost_usd"])
            ),
            models=tuple(models),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise MetricsError(f"Stored metrics are malformed: {error}") from error


def _non_negative_integer(data: dict[str, Any], key: str) -> int:
    value = data.get(key, 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MetricsError(f"Event {key} must be a non-negative integer")
    return value


def _non_negative_float(data: dict[str, Any], key: str) -> float:
    value = data.get(key, 0)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise MetricsError(f"Event {key} must be a non-negative number")
    return float(value)
