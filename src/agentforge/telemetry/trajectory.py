"""Append-only event recording for explainable and measurable runs."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

_REDACTED = "[REDACTED]"
_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "set_cookie",
    "token",
    "access_token",
}
_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b([A-Z][A-Z0-9_]*(?:TOKEN|PASSWORD|SECRET|API_KEY))\s*([=:])\s*"
    r"([^\s,;]+)"
)
_BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_PRIVATE_KEY_PATTERN = re.compile(
    r"-----BEGIN [^-\r\n]*PRIVATE KEY-----.*?"
    r"-----END [^-\r\n]*PRIVATE KEY-----",
    re.DOTALL,
)


@dataclass(frozen=True, slots=True)
class TrajectoryEvent:
    run_id: str
    event_type: str
    actor: str
    data: dict[str, Any] = field(default_factory=dict)
    occurred_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


class TrajectoryRecorder(Protocol):
    def record(self, event: TrajectoryEvent) -> None: ...


def redact_trajectory_value(value: Any, *, key: str | None = None) -> Any:
    """Recursively remove common credentials before an event reaches storage."""

    normalized_key = key.casefold().replace("-", "_") if key else None
    if normalized_key in _SENSITIVE_KEYS:
        return _REDACTED
    if isinstance(value, dict):
        return {
            item_key: redact_trajectory_value(item_value, key=str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [redact_trajectory_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_trajectory_value(item) for item in value)
    if isinstance(value, str):
        redacted = _PRIVATE_KEY_PATTERN.sub(_REDACTED, value)
        redacted = _BEARER_PATTERN.sub(f"Bearer {_REDACTED}", redacted)
        return _ASSIGNMENT_PATTERN.sub(
            lambda match: f"{match.group(1)}{match.group(2)}{_REDACTED}",
            redacted,
        )
    return value


def redact_sensitive_text(value: str) -> str:
    """Apply the same credential patterns to API, CLI, and provider text."""

    redacted = redact_trajectory_value(value)
    if not isinstance(redacted, str):
        raise TypeError("Sensitive text redaction must return text")
    return redacted


def contains_potential_secret(value: str) -> bool:
    return redact_sensitive_text(value) != value


def redact_trajectory_event(event: TrajectoryEvent) -> TrajectoryEvent:
    """Return a sanitized copy while preserving event identity and time."""

    return TrajectoryEvent(
        run_id=event.run_id,
        event_type=event.event_type,
        actor=event.actor,
        data=redact_trajectory_value(event.data),
        occurred_at=event.occurred_at,
    )


@dataclass(slots=True)
class InMemoryTrajectoryRecorder:
    events: list[TrajectoryEvent] = field(default_factory=list)

    def record(self, event: TrajectoryEvent) -> None:
        self.events.append(redact_trajectory_event(event))


class JsonlTrajectoryRecorder:
    def __init__(self, path: Path) -> None:
        self.path = path

    def record(self, event: TrajectoryEvent) -> None:
        event = redact_trajectory_event(event)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as output_file:
            json.dump(asdict(event), output_file, ensure_ascii=False, default=str)
            output_file.write("\n")
