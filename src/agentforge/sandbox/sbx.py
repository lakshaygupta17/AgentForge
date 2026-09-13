"""Docker Sandboxes (``sbx``) backend using an isolated, mountless microVM."""

from __future__ import annotations

import math
import os
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
from agentforge.sandbox.orphans import OrphanRegistry
from agentforge.sandbox.workspace import WorkspaceManifest, WorkspaceTransfer

SBX_WORKSPACE = PurePosixPath("/home/agent/workspace")


class SbxCommandRunner(Protocol):
    """Small process seam for deterministic lifecycle tests."""

    def run(
        self, arguments: Sequence[str], *, timeout: float | None = None
    ) -> subprocess.CompletedProcess[bytes]: ...


class SubprocessSbxCommandRunner:
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


def _resolve_sbx_executable(requested: str | None = None) -> str | None:
    name = requested or "sbx"
    resolved = shutil.which(name)
    if resolved is not None:
        return resolved
    explicit_path = Path(name)
    if requested is not None and explicit_path.is_file():
        return str(explicit_path.resolve())
    local_app_data = os.environ.get("LOCALAPPDATA")
    if requested is None and local_app_data:
        installed = Path(local_app_data) / "DockerSandboxes" / "bin" / "sbx.exe"
        if installed.is_file():
            return str(installed.resolve())
    return None


class SbxSandbox:
    """Run approved actions in a disposable Docker Sandboxes microVM."""

    def __init__(
        self,
        *,
        limits: SandboxLimits | None = None,
        maximum_output_bytes: int | None = None,
        sbx_executable: str | None = None,
        runner: SbxCommandRunner | None = None,
        orphan_registry: OrphanRegistry | None = None,
    ) -> None:
        selected_limits = limits or SandboxLimits()
        if maximum_output_bytes is not None:
            selected_limits = replace(
                selected_limits, maximum_output_bytes=maximum_output_bytes
            )
        if runner is None:
            resolved_executable = _resolve_sbx_executable(sbx_executable)
            if resolved_executable is None:
                raise SandboxUnavailableError(
                    "Docker Sandboxes CLI was not found. Install the 'sbx' CLI and "
                    "ensure it is on PATH."
                )
            self._sbx_executable = resolved_executable
            self._runner: SbxCommandRunner = SubprocessSbxCommandRunner()
        else:
            self._sbx_executable = sbx_executable or "sbx"
            self._runner = runner

        self._orphan_registry = orphan_registry

        self.limits = selected_limits
        self._maximum_output_bytes = selected_limits.maximum_output_bytes
        self._temporary_directory = tempfile.TemporaryDirectory(prefix="agentforge-sbx-")
        self._workspace = Path(self._temporary_directory.name).resolve()
        self._sandbox_name = f"agentforge-{uuid4().hex[:12]}"
        self._available = False
        self._created = False
        self._closed = False
        self._workspace_synced = False
        self._import_manifest: WorkspaceManifest | None = None

    @property
    def workspace(self) -> Path:
        return self._workspace

    @property
    def sandbox_name(self) -> str:
        return self._sandbox_name

    @property
    def import_manifest(self) -> WorkspaceManifest | None:
        return self._import_manifest

    def check_available(self) -> None:
        if self._closed:
            raise RuntimeError("Sandbox is already closed")
        if self._available:
            return
        try:
            completed = self._run([self._sbx_executable, "version"], timeout=15)
        except (OSError, subprocess.SubprocessError) as error:
            raise SandboxUnavailableError(f"Docker Sandboxes is unavailable: {error}") from error
        if completed.returncode != 0:
            raise SandboxUnavailableError(
                "Docker Sandboxes is unavailable: " + self._message(completed)
            )
        self._available = True

    def import_workspace(
        self, source: Path, transfer: WorkspaceTransfer | None = None
    ) -> WorkspaceManifest:
        if self._closed:
            raise RuntimeError("Sandbox is already closed")
        if self._created:
            raise SandboxLifecycleError("Cannot import after the sbx microVM is created")
        manifest = (transfer or WorkspaceTransfer()).copy(source, self.workspace)
        self._import_manifest = manifest
        self._workspace_synced = False
        return manifest

    def start(self, policy: SecurityPolicy | None = None) -> None:
        """Create the microVM and upload the filtered local snapshot."""

        if self._closed:
            raise RuntimeError("Sandbox is already closed")
        selected_policy = policy or SecurityPolicy()
        limits_decision = selected_policy.evaluate_sandbox(self.limits)
        if not limits_decision.allowed:
            raise PermissionError("; ".join(limits_decision.reasons))
        self._ensure_created()
        self._sync_workspace()

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
        self._ensure_created()
        self._sync_workspace()
        relative_directory = working_directory.relative_to(self.workspace).as_posix()
        sandbox_directory = SBX_WORKSPACE
        if relative_directory != ".":
            sandbox_directory /= relative_directory

        arguments = [
            self._sbx_executable,
            "exec",
            "--workdir",
            str(sandbox_directory),
        ]
        for key, value in sorted(request.environment.items()):
            arguments.extend(("--env", f"{key}={value}"))
        arguments.extend((self.sandbox_name, *request.argv))
        started_at = time.monotonic()
        try:
            completed = self._run(arguments, timeout=request.timeout_seconds)
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

    def export_workspace(
        self, destination: Path, transfer: WorkspaceTransfer | None = None
    ) -> WorkspaceManifest:
        if self._closed:
            raise RuntimeError("Sandbox is already closed")
        self._ensure_created()
        if not self._workspace_synced:
            self._sync_workspace()
        with tempfile.TemporaryDirectory(prefix="agentforge-sbx-export-") as directory:
            staging = Path(directory)
            self._require_success(
                self._run(
                    [
                        self._sbx_executable,
                        "cp",
                        f"{self.sandbox_name}:{SBX_WORKSPACE}",
                        str(staging),
                    ],
                    timeout=120,
                ),
                "download the repaired workspace",
            )
            pulled_workspace = staging / SBX_WORKSPACE.name
            if not pulled_workspace.is_dir():
                raise SandboxLifecycleError(
                    "sbx did not return the expected /home/agent/workspace directory"
                )
            return (transfer or WorkspaceTransfer()).copy(pulled_workspace, destination)

    def close(self) -> None:
        if self._closed:
            return
        cleanup_error: SandboxLifecycleError | None = None
        if self._created:
            try:
                completed = self._run(
                    [self._sbx_executable, "rm", "--force", self.sandbox_name],
                    timeout=60,
                )
                if completed.returncode != 0:
                    cleanup_error = SandboxLifecycleError(
                        "sbx could not remove the microVM: " + self._message(completed)
                    )
                elif self._orphan_registry is not None:
                    self._orphan_registry.forget("sbx", self.sandbox_name)
            except (OSError, subprocess.SubprocessError) as error:
                cleanup_error = SandboxLifecycleError(f"sbx cleanup failed: {error}")
        self._temporary_directory.cleanup()
        self._closed = True
        self._created = False
        if cleanup_error is not None:
            raise cleanup_error

    def _ensure_created(self) -> None:
        if self._created:
            return
        self.check_available()
        create_arguments = [
            self._sbx_executable,
            "create",
            "--quiet",
            "--name",
            self.sandbox_name,
            "--cpus",
            str(math.ceil(self.limits.cpu_count)),
            "--memory",
            f"{self.limits.memory_mb}m",
        ]
        if not self.limits.network_access:
            create_arguments.extend(("--deny-network", "**"))
        create_arguments.append("shell")
        self._require_success(self._run(create_arguments, timeout=180), "create the microVM")
        self._created = True
        if self._orphan_registry is not None:
            self._orphan_registry.register_sbx(self.sandbox_name)

    def _sync_workspace(self) -> None:
        """Replace the VM snapshot so tests always see the latest agent edits."""

        self._require_success(
            self._run(
                [
                    self._sbx_executable,
                    "exec",
                    self.sandbox_name,
                    "rm",
                    "-rf",
                    str(SBX_WORKSPACE),
                ],
                timeout=30,
            ),
            "clear the previous workspace snapshot",
        )
        self._require_success(
            self._run(
                [
                    self._sbx_executable,
                    "exec",
                    self.sandbox_name,
                    "mkdir",
                    "-p",
                    str(SBX_WORKSPACE),
                ],
                timeout=30,
            ),
            "prepare the workspace",
        )
        for child in sorted(self.workspace.iterdir()):
            self._require_success(
                self._run(
                    [
                        self._sbx_executable,
                        "cp",
                        str(child),
                        f"{self.sandbox_name}:{SBX_WORKSPACE}/",
                    ],
                    timeout=120,
                ),
                f"upload {child.name}",
            )
        self._workspace_synced = True

    def _stop_after_timeout(self) -> None:
        completed = self._run(
            [self._sbx_executable, "stop", self.sandbox_name], timeout=30
        )
        if completed.returncode != 0:
            raise SandboxLifecycleError(
                "Timed-out microVM could not be stopped: " + self._message(completed)
            )

    def _run(
        self, arguments: Sequence[str], *, timeout: float | None
    ) -> subprocess.CompletedProcess[bytes]:
        try:
            return self._runner.run(arguments, timeout=timeout)
        except FileNotFoundError as error:
            raise SandboxUnavailableError("The sbx CLI disappeared from PATH") from error

    def _require_success(
        self, completed: subprocess.CompletedProcess[bytes], operation: str
    ) -> None:
        if completed.returncode != 0:
            raise SandboxLifecycleError(
                f"sbx could not {operation}: {self._message(completed)}"
            )

    @staticmethod
    def _message(completed: subprocess.CompletedProcess[bytes]) -> str:
        output = completed.stderr or completed.stdout
        return output.decode("utf-8", errors="replace").strip() or "unknown sbx error"

    def _decode(self, output: bytes) -> tuple[str, bool]:
        truncated = len(output) > self._maximum_output_bytes
        kept = output[: self._maximum_output_bytes]
        return kept.decode("utf-8", errors="replace"), truncated

    def __enter__(self) -> SbxSandbox:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
