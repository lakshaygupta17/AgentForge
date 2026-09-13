"""Bounded HTTPS repository snapshot cloning with explicit host approval."""

from __future__ import annotations

import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import unquote, urlparse

from agentforge.sandbox.workspace import WorkspaceManifest, WorkspaceTransfer


class RepositoryCloneError(RuntimeError):
    """A remote repository could not be imported within the clone policy."""


class CloneCommandRunner(Protocol):
    def run(
        self, arguments: Sequence[str], *, timeout_seconds: float
    ) -> subprocess.CompletedProcess[bytes]: ...


class SubprocessCloneCommandRunner:
    def run(
        self, arguments: Sequence[str], *, timeout_seconds: float
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            arguments,
            capture_output=True,
            check=False,
            shell=False,
            timeout=timeout_seconds,
        )


@dataclass(frozen=True, slots=True)
class RepositoryClonePolicy:
    allowed_hosts: frozenset[str]
    timeout_seconds: float = 120
    maximum_blob_bytes: int = 2_000_000

    def __post_init__(self) -> None:
        if not self.allowed_hosts:
            raise ValueError("At least one clone host must be explicitly allowed")
        if any(not host.strip() or "://" in host or "/" in host for host in self.allowed_hosts):
            raise ValueError("Clone hosts must be bare hostnames")
        if self.timeout_seconds <= 0 or self.maximum_blob_bytes <= 0:
            raise ValueError("Clone timeout and blob limit must be greater than zero")


@dataclass(frozen=True, slots=True)
class RepositoryCloneResult:
    host: str
    manifest: WorkspaceManifest


class SafeRepositoryCloner:
    def __init__(
        self,
        policy: RepositoryClonePolicy,
        *,
        runner: CloneCommandRunner | None = None,
        git_executable: str = "git",
    ) -> None:
        self.policy = policy
        self._runner = runner or SubprocessCloneCommandRunner()
        self._git_executable = git_executable

    def clone_snapshot(
        self,
        url: str,
        destination: Path,
        *,
        transfer: WorkspaceTransfer | None = None,
    ) -> RepositoryCloneResult:
        host = self._validate_url(url)
        destination = destination.resolve()
        if destination.exists():
            raise RepositoryCloneError("Clone destination must not already exist")
        with tempfile.TemporaryDirectory(prefix="agentforge-clone-") as temporary:
            checkout = Path(temporary) / "repository"
            arguments = (
                self._git_executable,
                "-c",
                "protocol.file.allow=never",
                "clone",
                "--depth",
                "1",
                "--single-branch",
                "--no-tags",
                f"--filter=blob:limit={self.policy.maximum_blob_bytes}",
                "--",
                url,
                str(checkout),
            )
            try:
                completed = self._runner.run(
                    arguments, timeout_seconds=self.policy.timeout_seconds
                )
            except (OSError, subprocess.SubprocessError) as error:
                raise RepositoryCloneError(
                    f"Git clone process failed with {type(error).__name__}"
                ) from error
            if completed.returncode != 0:
                raise RepositoryCloneError(
                    f"Git clone failed with exit code {completed.returncode}"
                )
            manifest = (transfer or WorkspaceTransfer()).copy(checkout, destination)
        return RepositoryCloneResult(host, manifest)

    def _validate_url(self, url: str) -> str:
        if not isinstance(url, str) or not url.strip():
            raise RepositoryCloneError("Repository URL must be non-empty text")
        parsed = urlparse(url)
        host = (parsed.hostname or "").casefold().rstrip(".")
        allowed = {item.casefold().rstrip(".") for item in self.policy.allowed_hosts}
        if parsed.scheme != "https":
            raise RepositoryCloneError("Only HTTPS repository URLs are allowed")
        if not host or host not in allowed:
            raise RepositoryCloneError(f"Repository host '{host or 'missing'}' is not allowed")
        if parsed.username is not None or parsed.password is not None:
            raise RepositoryCloneError("Credentials must not appear in repository URLs")
        try:
            port = parsed.port
        except ValueError as error:
            raise RepositoryCloneError("Repository URL has an invalid port") from error
        if port not in (None, 443):
            raise RepositoryCloneError("Only the default HTTPS port is allowed")
        if parsed.query or parsed.fragment:
            raise RepositoryCloneError("Repository URLs cannot contain query or fragment data")
        decoded_path = unquote(parsed.path)
        if not decoded_path.strip("/") or "\x00" in decoded_path:
            raise RepositoryCloneError("Repository URL must contain a valid path")
        return host
