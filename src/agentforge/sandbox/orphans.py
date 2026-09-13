"""Exact-resource registry and reaper for interrupted sandbox runs."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from agentforge.sandbox.errors import SandboxLifecycleError, SandboxUnavailableError

_SBX_NAME = re.compile(r"agentforge-[0-9a-f]{12}\Z")


class OrphanCommandRunner(Protocol):
    def run(
        self, arguments: Sequence[str], *, timeout: float | None = None
    ) -> subprocess.CompletedProcess[bytes]: ...


@dataclass(frozen=True)
class OwnedSandbox:
    backend: str
    resource_id: str
    created_at: str


@dataclass(frozen=True)
class ReaperResult:
    removed: tuple[str, ...]
    retained: tuple[str, ...]


class OrphanRegistry:
    """Persist only resources AgentForge itself successfully created."""

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()

    def register_sbx(self, resource_id: str) -> None:
        self._validate_sbx_id(resource_id)
        records = list(self.load())
        if any(record.backend == "sbx" and record.resource_id == resource_id for record in records):
            return
        records.append(
            OwnedSandbox(
                backend="sbx",
                resource_id=resource_id,
                created_at=datetime.now(UTC).isoformat(),
            )
        )
        self._save(records)

    def forget(self, backend: str, resource_id: str) -> None:
        records = [
            record
            for record in self.load()
            if not (record.backend == backend and record.resource_id == resource_id)
        ]
        self._save(records)

    def load(self) -> tuple[OwnedSandbox, ...]:
        if not self.path.exists():
            return ()
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise SandboxLifecycleError(f"Invalid orphan registry: {error}") from error
        if not isinstance(document, list):
            raise SandboxLifecycleError("Invalid orphan registry: expected a JSON list")
        records: list[OwnedSandbox] = []
        for item in document:
            if not isinstance(item, dict) or set(item) != {
                "backend",
                "resource_id",
                "created_at",
            }:
                raise SandboxLifecycleError("Invalid orphan registry record")
            try:
                record = OwnedSandbox(**item)
            except TypeError as error:
                raise SandboxLifecycleError("Invalid orphan registry record") from error
            if record.backend != "sbx":
                raise SandboxLifecycleError("Unsupported orphan registry backend")
            self._validate_sbx_id(record.resource_id)
            records.append(record)
        return tuple(records)

    def _save(self, records: Sequence[OwnedSandbox]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps([asdict(record) for record in records], indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)

    @staticmethod
    def _validate_sbx_id(resource_id: str) -> None:
        if _SBX_NAME.fullmatch(resource_id) is None:
            raise SandboxLifecycleError("Refusing an unrecognized sbx resource ID")


def reap_sbx_orphans(
    registry: OrphanRegistry,
    *,
    executable: str,
    runner: OrphanCommandRunner,
) -> ReaperResult:
    """Remove exact registered sbx IDs; never enumerate or use a wildcard."""

    removed: list[str] = []
    retained: list[str] = []
    for record in registry.load():
        if record.backend != "sbx":
            retained.append(record.resource_id)
            continue
        try:
            completed = runner.run(
                [executable, "rm", "--force", record.resource_id], timeout=60
            )
        except (OSError, subprocess.SubprocessError):
            retained.append(record.resource_id)
            continue
        if completed.returncode == 0:
            registry.forget("sbx", record.resource_id)
            removed.append(record.resource_id)
        else:
            retained.append(record.resource_id)
    return ReaperResult(tuple(removed), tuple(retained))


def require_sbx_executable(resolved: str | None) -> str:
    if resolved is None:
        raise SandboxUnavailableError("Docker Sandboxes CLI was not found")
    return resolved
