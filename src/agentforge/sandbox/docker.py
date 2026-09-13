"""Docker-backed sandbox with explicit lifecycle and resource boundaries."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path, PurePosixPath
from types import TracebackType
from typing import Protocol
from uuid import uuid4

from agentforge.core.models import ActionRequest, ExecutionResult, SandboxLimits
from agentforge.policy.engine import SecurityPolicy
from agentforge.sandbox.errors import SandboxLifecycleError, SandboxUnavailableError
from agentforge.sandbox.workspace import WorkspaceManifest, WorkspaceTransfer


class DockerCommandRunner(Protocol):
    """Narrow seam that keeps Docker command construction independently testable."""

    def run(
        self, arguments: Sequence[str], *, timeout: float | None = None
    ) -> subprocess.CompletedProcess[bytes]: ...


class SubprocessDockerCommandRunner:
    def run(
        self, arguments: Sequence[str], *, timeout: float | None = None
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            arguments,
            capture_output=True,
            check=False,
            shell=False,
            timeout=timeout,
        )


class DockerSandbox:
    """Execute approved commands in a disposable, constrained Docker container."""

    def __init__(
        self,
        *,
        image: str = "python:3.12-slim",
        limits: SandboxLimits | None = None,
        maximum_output_bytes: int | None = None,
        docker_executable: str | None = None,
        runner: DockerCommandRunner | None = None,
    ) -> None:
        if not image.strip():
            raise ValueError("Docker image cannot be empty")
        if image.startswith("-"):
            raise ValueError("Docker image cannot begin with an option prefix")
        selected_limits = limits or SandboxLimits()
        if maximum_output_bytes is not None:
            selected_limits = replace(
                selected_limits, maximum_output_bytes=maximum_output_bytes
            )

        if runner is None:
            resolved_executable = shutil.which(docker_executable or "docker")
            if resolved_executable is None:
                raise SandboxUnavailableError(
                    "Docker CLI was not found. Install Docker Desktop and ensure "
                    "the 'docker' command is on PATH."
                )
            self._docker_executable = resolved_executable
            self._runner: DockerCommandRunner = SubprocessDockerCommandRunner()
        else:
            self._docker_executable = docker_executable or "docker"
            self._runner = runner

        self.image = image
        self.limits = selected_limits
        self._maximum_output_bytes = selected_limits.maximum_output_bytes
        self._temporary_directory = tempfile.TemporaryDirectory(prefix="agentforge-docker-")
        self._workspace = Path(self._temporary_directory.name).resolve()
        self._container_name = f"agentforge-{uuid4().hex[:12]}"
        self._available = False
        self._created = False
        self._started = False
        self._closed = False
        self._import_manifest: WorkspaceManifest | None = None

    @property
    def workspace(self) -> Path:
        return self._workspace

    @property
    def container_name(self) -> str:
        return self._container_name

    @property
    def import_manifest(self) -> WorkspaceManifest | None:
        return self._import_manifest

    def check_available(self) -> None:
        """Verify that the Docker CLI and daemon are reachable without creating a container."""

        if self._closed:
            raise RuntimeError("Sandbox is already closed")
        self._ensure_available()

    def import_workspace(
        self, source: Path, transfer: WorkspaceTransfer | None = None
    ) -> WorkspaceManifest:
        """Copy a filtered source snapshot into the sandbox before execution begins."""

        if self._closed:
            raise RuntimeError("Sandbox is already closed")
        if self._created:
            raise SandboxLifecycleError("Cannot import after the Docker container is created")
        manifest = (transfer or WorkspaceTransfer()).copy(source, self.workspace)
        self._import_manifest = manifest
        return manifest

    def export_workspace(
        self, destination: Path, transfer: WorkspaceTransfer | None = None
    ) -> WorkspaceManifest:
        """Copy the sandbox result to an absent or empty destination without overwriting."""

        if self._closed:
            raise RuntimeError("Sandbox is already closed")
        return (transfer or WorkspaceTransfer()).copy(self.workspace, destination)

    def execute(self, request: ActionRequest, policy: SecurityPolicy) -> ExecutionResult:
        if self._closed:
            raise RuntimeError("Sandbox is already closed")

        action_decision = policy.evaluate(request, self.workspace)
        if not action_decision.allowed:
            raise PermissionError("; ".join(action_decision.reasons))
        limits_decision = policy.evaluate_sandbox(self.limits)
        if not limits_decision.allowed:
            raise PermissionError("; ".join(limits_decision.reasons))

        working_directory = (self.workspace / request.working_directory).resolve()
        working_directory.mkdir(parents=True, exist_ok=True)
        self._ensure_started()

        relative_directory = working_directory.relative_to(self.workspace).as_posix()
        container_directory = PurePosixPath("/workspace")
        if relative_directory != ".":
            container_directory /= relative_directory

        docker_arguments = [
            self._docker_executable,
            "exec",
            "--workdir",
            str(container_directory),
        ]
        for key, value in sorted(request.environment.items()):
            docker_arguments.extend(("--env", f"{key}={value}"))
        docker_arguments.extend((self.container_name, *request.argv))
        started_at = time.monotonic()

        try:
            completed = self._run(docker_arguments, timeout=request.timeout_seconds)
        except subprocess.TimeoutExpired as error:
            duration = time.monotonic() - started_at
            self._stop_after_timeout()
            stdout, stdout_truncated = self._decode(error.stdout or b"")
            stderr, stderr_truncated = self._decode(error.stderr or b"")
            return ExecutionResult(
                exit_code=None,
                stdout=stdout,
                stderr=stderr,
                duration_seconds=duration,
                timed_out=True,
                output_truncated=stdout_truncated or stderr_truncated,
            )

        duration = time.monotonic() - started_at
        stdout, stdout_truncated = self._decode(completed.stdout)
        stderr, stderr_truncated = self._decode(completed.stderr)
        return ExecutionResult(
            exit_code=completed.returncode,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=duration,
            output_truncated=stdout_truncated or stderr_truncated,
        )

    def close(self) -> None:
        if self._closed:
            return

        cleanup_error: SandboxLifecycleError | None = None
        if self._created:
            try:
                completed = self._run(
                    [self._docker_executable, "rm", "--force", self.container_name],
                    timeout=30,
                )
                if completed.returncode != 0:
                    cleanup_error = SandboxLifecycleError(
                        "Docker could not remove the sandbox container: "
                        + self._message(completed)
                    )
            except (OSError, subprocess.SubprocessError) as error:
                cleanup_error = SandboxLifecycleError(
                    f"Docker sandbox cleanup failed: {error}"
                )
        self._temporary_directory.cleanup()
        self._closed = True
        self._created = False
        self._started = False
        if cleanup_error is not None:
            raise cleanup_error

    def _ensure_started(self) -> None:
        self._ensure_available()
        if not self._created:
            create_arguments = [
                self._docker_executable,
                "create",
                "--name",
                self.container_name,
                "--label",
                "agentforge.managed=true",
                "--cpus",
                f"{self.limits.cpu_count:g}",
                "--memory",
                f"{self.limits.memory_mb}m",
                "--pids-limit",
                str(self.limits.process_limit),
                "--network",
                "bridge" if self.limits.network_access else "none",
            ]
            if self.limits.protect_host_filesystem:
                create_arguments.append("--read-only")
            create_arguments.extend(
                (
                    "--tmpfs",
                    "/tmp:rw,noexec,nosuid,size=64m",
                    "--mount",
                    f"type=bind,source={self.workspace},target=/workspace",
                    "--workdir",
                    "/workspace",
                    self.image,
                    "sleep",
                    "infinity",
                )
            )
            self._require_success(self._run(create_arguments, timeout=120), "create")
            self._created = True

        if not self._started:
            self._require_success(
                self._run(
                    [self._docker_executable, "start", self.container_name], timeout=30
                ),
                "start",
            )
            self._started = True

    def _ensure_available(self) -> None:
        if self._available:
            return
        try:
            completed = self._run(
                [self._docker_executable, "version", "--format", "{{.Server.Version}}"],
                timeout=15,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise SandboxUnavailableError(f"Docker is unavailable: {error}") from error
        if completed.returncode != 0:
            raise SandboxUnavailableError(
                "Docker daemon is unavailable: " + self._message(completed)
            )
        self._available = True

    def _stop_after_timeout(self) -> None:
        completed = self._run(
            [self._docker_executable, "kill", self.container_name], timeout=15
        )
        if completed.returncode != 0:
            raise SandboxLifecycleError(
                "Timed-out container could not be stopped: " + self._message(completed)
            )
        self._started = False

    def _run(
        self, arguments: Sequence[str], *, timeout: float | None
    ) -> subprocess.CompletedProcess[bytes]:
        try:
            return self._runner.run(arguments, timeout=timeout)
        except FileNotFoundError as error:
            raise SandboxUnavailableError("Docker CLI disappeared from PATH") from error

    def _require_success(
        self, completed: subprocess.CompletedProcess[bytes], operation: str
    ) -> None:
        if completed.returncode != 0:
            raise SandboxLifecycleError(
                f"Docker could not {operation} the sandbox: {self._message(completed)}"
            )

    def _message(self, completed: subprocess.CompletedProcess[bytes]) -> str:
        output = completed.stderr or completed.stdout
        return output.decode("utf-8", errors="replace").strip() or "unknown Docker error"

    def _decode(self, output: bytes) -> tuple[str, bool]:
        truncated = len(output) > self._maximum_output_bytes
        kept = output[: self._maximum_output_bytes]
        return kept.decode("utf-8", errors="replace"), truncated

    def __enter__(self) -> DockerSandbox:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
