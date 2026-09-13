"""E2B backend for disposable, hosted Linux microVMs."""

from __future__ import annotations

import math
import shlex
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from importlib import import_module
from pathlib import Path, PurePosixPath
from types import TracebackType
from typing import Any, Protocol

from agentforge.core.models import ActionRequest, ExecutionResult, SandboxLimits
from agentforge.credentials import CredentialConfigurationError, HostCredentialBroker
from agentforge.policy.engine import SecurityPolicy
from agentforge.sandbox.errors import SandboxLifecycleError, SandboxUnavailableError
from agentforge.sandbox.workspace import WorkspaceManifest, WorkspaceTransfer

E2B_WORKSPACE = PurePosixPath("/home/user/agentforge-workspace")
E2B_HOBBY_MAX_SESSION_SECONDS = 3_600


@dataclass(frozen=True, slots=True)
class E2BCommandOutcome:
    exit_code: int
    stdout: str
    stderr: str


class E2BCommandTimeout(TimeoutError):
    """The E2B command exceeded its action-specific time limit."""


class E2BClient(Protocol):
    def run(
        self,
        command: str,
        *,
        cwd: str,
        environment: Mapping[str, str],
        timeout_seconds: float,
    ) -> E2BCommandOutcome: ...

    def reset_directory(self, path: str) -> None: ...

    def write_file(self, path: str, data: bytes) -> None: ...

    def list_files(self, path: str) -> Sequence[str]: ...

    def read_file(self, path: str) -> bytes: ...

    def kill(self) -> None: ...


class E2BClientFactory(Protocol):
    def create(
        self,
        *,
        timeout_seconds: float,
        allow_internet_access: bool,
        template: str | None,
    ) -> E2BClient: ...


class SdkE2BClientFactory:
    """Late-import the optional SDK so other backends need not install E2B."""

    def create(
        self,
        *,
        timeout_seconds: float,
        allow_internet_access: bool,
        template: str | None,
    ) -> E2BClient:
        try:
            e2b = import_module("e2b")
        except ImportError as error:
            raise SandboxUnavailableError(
                "The E2B SDK is not installed. Install AgentForge's 'e2b' extra "
                "inside the project virtual environment."
            ) from error

        try:
            api_key = HostCredentialBroker.from_process().require_secret("E2B_API_KEY")
        except CredentialConfigurationError as error:
            raise SandboxUnavailableError(str(error)) from error
        options: dict[str, object] = {
            "api_key": api_key,
            "timeout": math.ceil(timeout_seconds),
            "secure": True,
            "allow_internet_access": allow_internet_access,
        }
        if template is not None:
            options["template"] = template
        try:
            sandbox = e2b.Sandbox.create(**options)
        except Exception as error:
            raise SandboxUnavailableError(f"E2B could not create a microVM: {error}") from error
        return _SdkE2BClient(sandbox, e2b)


class _SdkE2BClient:
    def __init__(self, sandbox: Any, module: Any) -> None:
        self._sandbox = sandbox
        self._command_exit_error = module.CommandExitException
        self._timeout_error = module.TimeoutException
        self._file_not_found_error = module.FileNotFoundException
        self._file_type = module.FileType

    def run(
        self,
        command: str,
        *,
        cwd: str,
        environment: Mapping[str, str],
        timeout_seconds: float,
    ) -> E2BCommandOutcome:
        try:
            result = self._sandbox.commands.run(
                command,
                cwd=cwd,
                envs=dict(environment),
                timeout=timeout_seconds,
            )
        except self._timeout_error as error:
            raise E2BCommandTimeout(str(error)) from error
        except self._command_exit_error as error:
            return E2BCommandOutcome(error.exit_code, error.stdout, error.stderr)
        return E2BCommandOutcome(result.exit_code, result.stdout, result.stderr)

    def reset_directory(self, path: str) -> None:
        try:
            self._sandbox.files.remove(path)
        except self._file_not_found_error:
            pass
        self._sandbox.files.make_dir(path)

    def write_file(self, path: str, data: bytes) -> None:
        self._sandbox.files.write(path, data)

    def list_files(self, path: str) -> Sequence[str]:
        entries = self._sandbox.files.list(path, depth=100)
        root = PurePosixPath(path)
        files: list[str] = []
        for entry in entries:
            if entry.type != self._file_type.FILE:
                continue
            candidate = PurePosixPath(entry.path)
            files.append(str(candidate if candidate.is_absolute() else root / candidate))
        return tuple(files)

    def read_file(self, path: str) -> bytes:
        result = self._sandbox.files.read(path, format="bytes")
        if not isinstance(result, (bytes, bytearray)):
            raise SandboxLifecycleError(f"E2B returned non-binary data for {path}")
        return bytes(result)

    def kill(self) -> None:
        self._sandbox.kill()


class E2BSandbox:
    """Run approved actions in an E2B-hosted disposable microVM."""

    def __init__(
        self,
        *,
        limits: SandboxLimits | None = None,
        maximum_output_bytes: int | None = None,
        sandbox_timeout_seconds: float = 300,
        template: str | None = None,
        factory: E2BClientFactory | None = None,
    ) -> None:
        selected_limits = limits or SandboxLimits()
        if maximum_output_bytes is not None:
            selected_limits = replace(
                selected_limits, maximum_output_bytes=maximum_output_bytes
            )
        if sandbox_timeout_seconds <= 0:
            raise ValueError("Sandbox timeout must be greater than zero")
        if sandbox_timeout_seconds > E2B_HOBBY_MAX_SESSION_SECONDS:
            raise ValueError(
                "Sandbox timeout exceeds the E2B Hobby one-hour session limit"
            )
        self.limits = selected_limits
        self._maximum_output_bytes = selected_limits.maximum_output_bytes
        self._sandbox_timeout_seconds = sandbox_timeout_seconds
        self._template = template
        self._factory = factory or SdkE2BClientFactory()
        self._temporary_directory = tempfile.TemporaryDirectory(prefix="agentforge-e2b-")
        self._workspace = Path(self._temporary_directory.name).resolve()
        self._client: E2BClient | None = None
        self._closed = False
        self._workspace_synced = False
        self._import_manifest: WorkspaceManifest | None = None

    @property
    def workspace(self) -> Path:
        return self._workspace

    @property
    def import_manifest(self) -> WorkspaceManifest | None:
        return self._import_manifest

    def import_workspace(
        self, source: Path, transfer: WorkspaceTransfer | None = None
    ) -> WorkspaceManifest:
        if self._closed:
            raise RuntimeError("Sandbox is already closed")
        if self._client is not None:
            raise SandboxLifecycleError("Cannot import after the E2B microVM is created")
        manifest = (transfer or WorkspaceTransfer()).copy(source, self.workspace)
        self._import_manifest = manifest
        self._workspace_synced = False
        return manifest

    def start(self, policy: SecurityPolicy | None = None) -> None:
        if self._closed:
            raise RuntimeError("Sandbox is already closed")
        selected_policy = policy or SecurityPolicy()
        decision = selected_policy.evaluate_sandbox(self.limits)
        if not decision.allowed:
            raise PermissionError("; ".join(decision.reasons))
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
        remote_directory = E2B_WORKSPACE
        if relative_directory != ".":
            remote_directory /= relative_directory

        client = self._require_client()
        started_at = time.monotonic()
        try:
            outcome = client.run(
                shlex.join(request.argv),
                cwd=str(remote_directory),
                environment=request.environment,
                timeout_seconds=request.timeout_seconds,
            )
        except E2BCommandTimeout:
            duration = time.monotonic() - started_at
            self._kill_after_timeout()
            return ExecutionResult(
                exit_code=None,
                stdout="",
                stderr="",
                duration_seconds=duration,
                timed_out=True,
            )

        duration = time.monotonic() - started_at
        stdout, stdout_truncated = self._truncate(outcome.stdout)
        stderr, stderr_truncated = self._truncate(outcome.stderr)
        return ExecutionResult(
            exit_code=outcome.exit_code,
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
        client = self._require_client()
        with tempfile.TemporaryDirectory(prefix="agentforge-e2b-export-") as directory:
            staging = Path(directory)
            for remote_path in client.list_files(str(E2B_WORKSPACE)):
                relative = self._safe_relative_remote_path(remote_path)
                output = staging / Path(*relative.parts)
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(client.read_file(remote_path))
            return (transfer or WorkspaceTransfer()).copy(staging, destination)

    def close(self) -> None:
        if self._closed:
            return
        cleanup_error: SandboxLifecycleError | None = None
        if self._client is not None:
            try:
                self._client.kill()
            except Exception as error:
                cleanup_error = SandboxLifecycleError(f"E2B cleanup failed: {error}")
        self._client = None
        self._temporary_directory.cleanup()
        self._closed = True
        if cleanup_error is not None:
            raise cleanup_error

    def _ensure_created(self) -> None:
        if self._client is not None:
            return
        try:
            self._client = self._factory.create(
                timeout_seconds=self._sandbox_timeout_seconds,
                allow_internet_access=self.limits.network_access,
                template=self._template,
            )
        except SandboxUnavailableError:
            raise
        except Exception as error:
            raise SandboxUnavailableError(f"E2B could not create a microVM: {error}") from error

    def _sync_workspace(self) -> None:
        client = self._require_client()
        try:
            client.reset_directory(str(E2B_WORKSPACE))
            directories: set[PurePosixPath] = set()
            files = sorted(path for path in self.workspace.rglob("*") if path.is_file())
            for local_path in files:
                relative = PurePosixPath(local_path.relative_to(self.workspace).as_posix())
                directories.add(relative.parent)
            for directory in sorted(directories, key=lambda value: len(value.parts)):
                if str(directory) != ".":
                    client.reset_directory(str(E2B_WORKSPACE / directory))
            for local_path in files:
                relative = PurePosixPath(local_path.relative_to(self.workspace).as_posix())
                client.write_file(str(E2B_WORKSPACE / relative), local_path.read_bytes())
        except Exception as error:
            raise SandboxLifecycleError(f"E2B workspace upload failed: {error}") from error
        self._workspace_synced = True

    def _kill_after_timeout(self) -> None:
        client = self._require_client()
        try:
            client.kill()
        except Exception as error:
            raise SandboxLifecycleError(
                f"Timed-out E2B microVM could not be stopped: {error}"
            ) from error
        self._client = None
        self._workspace_synced = False

    def _require_client(self) -> E2BClient:
        if self._client is None:
            raise SandboxLifecycleError("E2B microVM is not running")
        return self._client

    def _safe_relative_remote_path(self, remote_path: str) -> PurePosixPath:
        candidate = PurePosixPath(remote_path)
        try:
            relative = candidate.relative_to(E2B_WORKSPACE)
        except ValueError as error:
            raise SandboxLifecycleError(
                f"E2B returned a file outside the workspace: {remote_path}"
            ) from error
        if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
            raise SandboxLifecycleError(f"E2B returned an unsafe file path: {remote_path}")
        return relative

    def _truncate(self, output: str) -> tuple[str, bool]:
        encoded = output.encode("utf-8")
        truncated = len(encoded) > self._maximum_output_bytes
        kept = encoded[: self._maximum_output_bytes]
        return kept.decode("utf-8", errors="replace"), truncated

    def __enter__(self) -> E2BSandbox:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
