"""Thread-safe, bounded lifecycle management for local AgentForge runs."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from threading import RLock
from typing import Any
from uuid import uuid4


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in {self.SUCCEEDED, self.FAILED, self.CANCELLED}


@dataclass(frozen=True, slots=True)
class ServiceRunEvent:
    sequence: int
    event_type: str
    recorded_at: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ServiceRunSnapshot:
    run_id: str
    kind: str
    status: RunStatus
    created_at: str
    started_at: str | None
    finished_at: str | None
    cancel_requested: bool
    result: dict[str, Any] | None
    error: str | None


@dataclass(slots=True)
class _MutableRun:
    run_id: str
    kind: str
    created_at: str
    status: RunStatus = RunStatus.QUEUED
    started_at: str | None = None
    finished_at: str | None = None
    cancel_requested: bool = False
    result: dict[str, Any] | None = None
    error: str | None = None
    events: list[ServiceRunEvent] = field(default_factory=list)
    future: Future[None] | None = None


class RunNotFoundError(KeyError):
    """The requested service run is not retained by this process."""


class RunCapacityError(RuntimeError):
    """All retained run slots are currently occupied by active work."""


class RunManager:
    """Run one local workflow at a time and retain a bounded status history."""

    def __init__(
        self,
        runner: Callable[[], dict[str, Any]],
        *,
        maximum_retained_runs: int = 100,
    ) -> None:
        if maximum_retained_runs <= 0:
            raise ValueError("maximum_retained_runs must be greater than zero")
        self._runner = runner
        self._maximum_retained_runs = maximum_retained_runs
        self._runs: dict[str, _MutableRun] = {}
        self._lock = RLock()
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="agentforge-local-run"
        )
        self._closed = False

    def submit(self, kind: str = "benchmark-suite") -> ServiceRunSnapshot:
        with self._lock:
            if self._closed:
                raise RuntimeError("Run manager is closed")
            self._prune_terminal_runs()
            if len(self._runs) >= self._maximum_retained_runs:
                raise RunCapacityError("Local run capacity is full")
            run_id = str(uuid4())
            run = _MutableRun(run_id, kind, _utc_now())
            self._runs[run_id] = run
            self._record(run, "run_queued", {"kind": kind})
            run.future = self._executor.submit(self._execute, run_id)
            return self._snapshot(run)

    def get(self, run_id: str) -> ServiceRunSnapshot:
        with self._lock:
            return self._snapshot(self._require(run_id))

    def events_after(
        self, run_id: str, after_sequence: int = 0
    ) -> tuple[ServiceRunEvent, ...]:
        if after_sequence < 0:
            raise ValueError("after_sequence cannot be negative")
        with self._lock:
            run = self._require(run_id)
            return tuple(
                event for event in run.events if event.sequence > after_sequence
            )

    def cancel(self, run_id: str) -> ServiceRunSnapshot:
        with self._lock:
            run = self._require(run_id)
            if run.status.terminal or run.cancel_requested:
                return self._snapshot(run)
            run.cancel_requested = True
            self._record(run, "cancellation_requested")
            if run.future is not None and run.future.cancel():
                self._finish_cancelled(run)
            return self._snapshot(run)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for run in self._runs.values():
                if not run.status.terminal:
                    run.cancel_requested = True
                    if run.future is not None and run.future.cancel():
                        self._finish_cancelled(run)
        self._executor.shutdown(wait=True, cancel_futures=True)

    def _execute(self, run_id: str) -> None:
        with self._lock:
            run = self._require(run_id)
            if run.cancel_requested:
                self._finish_cancelled(run)
                return
            run.status = RunStatus.RUNNING
            run.started_at = _utc_now()
            self._record(run, "run_started")
        try:
            result = self._runner()
        except Exception as error:
            with self._lock:
                run = self._require(run_id)
                if run.cancel_requested:
                    self._finish_cancelled(run)
                else:
                    run.status = RunStatus.FAILED
                    run.error = f"Run failed with {type(error).__name__}"
                    run.finished_at = _utc_now()
                    self._record(run, "run_failed", {"error_type": type(error).__name__})
            return
        with self._lock:
            run = self._require(run_id)
            if run.cancel_requested:
                self._finish_cancelled(run)
                return
            run.result = result
            run.finished_at = _utc_now()
            if result.get("succeeded") is False:
                run.status = RunStatus.FAILED
                run.error = "Run completed with an unsuccessful result"
                self._record(run, "run_failed", {"reason": "unsuccessful_result"})
            else:
                run.status = RunStatus.SUCCEEDED
                self._record(run, "run_succeeded")

    def _finish_cancelled(self, run: _MutableRun) -> None:
        if run.status.terminal:
            return
        run.status = RunStatus.CANCELLED
        run.finished_at = _utc_now()
        self._record(run, "run_cancelled")

    def _require(self, run_id: str) -> _MutableRun:
        try:
            return self._runs[run_id]
        except KeyError:
            raise RunNotFoundError(run_id) from None

    def _record(
        self,
        run: _MutableRun,
        event_type: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        run.events.append(
            ServiceRunEvent(len(run.events) + 1, event_type, _utc_now(), data or {})
        )

    def _snapshot(self, run: _MutableRun) -> ServiceRunSnapshot:
        return ServiceRunSnapshot(
            run_id=run.run_id,
            kind=run.kind,
            status=run.status,
            created_at=run.created_at,
            started_at=run.started_at,
            finished_at=run.finished_at,
            cancel_requested=run.cancel_requested,
            result=run.result,
            error=run.error,
        )

    def _prune_terminal_runs(self) -> None:
        overflow = len(self._runs) - self._maximum_retained_runs + 1
        if overflow <= 0:
            return
        terminal_ids = [
            run_id for run_id, run in self._runs.items() if run.status.terminal
        ]
        for run_id in terminal_ids[:overflow]:
            del self._runs[run_id]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
