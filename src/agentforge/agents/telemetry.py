"""Shared helpers for recording model and tool activity without prompt contents."""

from __future__ import annotations

from typing import Any

from agentforge.core.models import WorkflowState
from agentforge.telemetry.trajectory import TrajectoryEvent, TrajectoryRecorder


def record_agent_event(
    recorder: TrajectoryRecorder | None,
    state: WorkflowState,
    actor: str,
    event_type: str,
    data: dict[str, Any] | None = None,
) -> None:
    if recorder is None:
        return
    recorder.record(
        TrajectoryEvent(
            run_id=state.run_id,
            event_type=event_type,
            actor=actor,
            data=data or {},
        )
    )

