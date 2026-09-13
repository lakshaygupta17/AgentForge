"""Bounded, secret-aware workspace transfer for disposable sandboxes."""

from __future__ import annotations

import hashlib
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path


class WorkspaceTransferError(RuntimeError):
    """A workspace could not be copied without violating transfer rules."""


@dataclass(frozen=True, slots=True)
class WorkspaceTransferPolicy:
    """Limits and exclusions applied before any destination file is written."""

    maximum_files: int = 10_000
    maximum_file_bytes: int = 2_000_000
    maximum_total_bytes: int = 100_000_000
    ignored_directories: frozenset[str] = frozenset(
        {
            ".git",
            ".hg",
            ".mypy_cache",
            ".pytest_cache",
            ".ruff_cache",
            ".svn",
            ".tox",
            ".venv",
            "__pycache__",
            "build",
            "dist",
            "node_modules",
            "venv",
        }
    )
    sensitive_filenames: frozenset[str] = frozenset(
        {
            ".netrc",
            ".npmrc",
            ".pypirc",
            "credentials.json",
            "id_ed25519",
            "id_rsa",
            "service-account.json",
        }
    )
    sensitive_suffixes: frozenset[str] = frozenset({".key", ".p12", ".pem"})

    def __post_init__(self) -> None:
        if self.maximum_files <= 0:
            raise ValueError("Maximum file count must be greater than zero")
        if self.maximum_file_bytes <= 0:
            raise ValueError("Maximum file size must be greater than zero")
        if self.maximum_total_bytes <= 0:
            raise ValueError("Maximum workspace size must be greater than zero")


@dataclass(frozen=True, slots=True)
class WorkspaceEntry:
    path: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class WorkspaceManifest:
    entries: tuple[WorkspaceEntry, ...]
    total_bytes: int
    skipped_paths: tuple[str, ...]


class WorkspaceTransfer:
    """Copy a filtered snapshot without overwriting an existing workspace."""

    def __init__(self, policy: WorkspaceTransferPolicy | None = None) -> None:
        self.policy = policy or WorkspaceTransferPolicy()

    def copy(self, source: Path, destination: Path) -> WorkspaceManifest:
        source = source.resolve()
        destination = destination.resolve()
        if not source.is_dir():
            raise WorkspaceTransferError(f"Workspace source is not a directory: {source}")
        if (
            source == destination
            or source.is_relative_to(destination)
            or destination.is_relative_to(source)
        ):
            raise WorkspaceTransferError("Source and destination workspaces must not overlap")
        if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
            raise WorkspaceTransferError("Workspace destination must be absent or empty")

        candidates, skipped = self._collect(source)
        entries: list[WorkspaceEntry] = []
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="agentforge-transfer-", dir=destination.parent
        ) as temporary_directory:
            staged_root = Path(temporary_directory)
            for relative_path, expected_size in candidates:
                source_file = source / relative_path
                staged_file = staged_root / relative_path
                staged_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_file, staged_file, follow_symlinks=False)
                actual_size = staged_file.stat().st_size
                if actual_size != expected_size:
                    raise WorkspaceTransferError(
                        f"File changed while taking workspace snapshot: {relative_path.as_posix()}"
                    )
                entries.append(
                    WorkspaceEntry(
                        relative_path.as_posix(),
                        actual_size,
                        self._sha256(staged_file),
                    )
                )

            destination.mkdir(parents=True, exist_ok=True)
            for entry in entries:
                relative_path = Path(entry.path)
                output_file = destination / relative_path
                output_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(staged_root / relative_path, output_file)

        return WorkspaceManifest(
            tuple(entries), sum(entry.size for entry in entries), tuple(skipped)
        )

    def _collect(self, source: Path) -> tuple[list[tuple[Path, int]], list[str]]:
        candidates: list[tuple[Path, int]] = []
        skipped: list[str] = []
        total_bytes = 0
        ignored = {name.casefold() for name in self.policy.ignored_directories}

        for candidate in sorted(source.rglob("*")):
            relative_path = candidate.relative_to(source)
            display_path = relative_path.as_posix()
            if any(part.casefold() in ignored for part in relative_path.parts[:-1]):
                continue
            if candidate.is_symlink():
                skipped.append(display_path)
                continue
            if candidate.is_dir():
                continue
            if not candidate.is_file() or self._is_sensitive(relative_path):
                skipped.append(display_path)
                continue
            size = candidate.stat().st_size
            if size > self.policy.maximum_file_bytes:
                raise WorkspaceTransferError(
                    f"File exceeds the {self.policy.maximum_file_bytes}-byte transfer limit: "
                    f"{display_path}"
                )
            candidates.append((relative_path, size))
            total_bytes += size
            if len(candidates) > self.policy.maximum_files:
                raise WorkspaceTransferError(
                    f"Workspace exceeds the {self.policy.maximum_files}-file transfer limit"
                )
            if total_bytes > self.policy.maximum_total_bytes:
                raise WorkspaceTransferError(
                    f"Workspace exceeds the {self.policy.maximum_total_bytes}-byte transfer limit"
                )
        return candidates, skipped

    def _is_sensitive(self, relative_path: Path) -> bool:
        name = relative_path.name.casefold()
        sensitive_names = {value.casefold() for value in self.policy.sensitive_filenames}
        sensitive_suffixes = {value.casefold() for value in self.policy.sensitive_suffixes}
        return (
            name == ".env"
            or name.startswith(".env.")
            or name in sensitive_names
            or relative_path.suffix.casefold() in sensitive_suffixes
        )

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as input_file:
            for chunk in iter(lambda: input_file.read(64 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
