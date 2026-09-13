"""Bounded cold-start measurements used to evaluate whether pooling is worthwhile."""

from __future__ import annotations

import statistics
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, TypeVar


class CloseableSandbox(Protocol):
    def close(self) -> None: ...


SandboxType = TypeVar("SandboxType", bound=CloseableSandbox)


@dataclass(frozen=True, slots=True)
class SandboxStartupMetrics:
    iterations: int
    durations_seconds: tuple[float, ...]
    average_seconds: float
    maximum_seconds: float


def measure_sandbox_startup(
    factory: Callable[[], SandboxType],
    start: Callable[[SandboxType], None],
    *,
    iterations: int = 3,
    clock: Callable[[], float] = time.monotonic,
) -> SandboxStartupMetrics:
    if iterations <= 0 or iterations > 10:
        raise ValueError("Startup iterations must be between 1 and 10")
    durations: list[float] = []
    for _ in range(iterations):
        started = clock()
        sandbox = factory()
        try:
            start(sandbox)
            durations.append(max(0.0, clock() - started))
        finally:
            sandbox.close()
    return SandboxStartupMetrics(
        iterations=iterations,
        durations_seconds=tuple(durations),
        average_seconds=statistics.fmean(durations),
        maximum_seconds=max(durations),
    )
