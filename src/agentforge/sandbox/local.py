"""A temporary local backend for trusted development and tests only."""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from types import TracebackType

from agentforge.core.models import ActionRequest, ExecutionResult, SandboxLimits
from agentforge.policy.engine import SecurityPolicy

SAFE_HOST_ENVIRONMENT_KEYS = frozenset(
    {
        "LANG",
        "LC_ALL",
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "WINDIR",
    }
)


class LocalSandbox:
    """Run structured commands locally without a shell.

    This class validates architecture and test behavior. It does not provide the
    isolation guarantees required for untrusted repositories or model output.
    """

    def __init__(
        self,
        *,
        limits: SandboxLimits | None = None,
        maximum_output_bytes: int | None = None,
    ) -> None:
        selected_limits = limits or SandboxLimits()
        if maximum_output_bytes is not None:
            selected_limits = replace(
                selected_limits, maximum_output_bytes=maximum_output_bytes
            )
        self._temporary_directory = tempfile.TemporaryDirectory(prefix="agentforge-")
        self._workspace = Path(self._temporary_directory.name).resolve()
        self.limits = selected_limits
        self._maximum_output_bytes = selected_limits.maximum_output_bytes
        self._closed = False

    @property
    def workspace(self) -> Path:
        return self._workspace

    def execute(self, request: ActionRequest, policy: SecurityPolicy) -> ExecutionResult:
        if self._closed:
            raise RuntimeError("Sandbox is already closed")

        decision = policy.evaluate(request, self.workspace)
        if not decision.allowed:
            raise PermissionError("; ".join(decision.reasons))
        limits_decision = policy.evaluate_sandbox(self.limits)
        if not limits_decision.allowed:
            raise PermissionError("; ".join(limits_decision.reasons))

        working_directory = (self.workspace / request.working_directory).resolve()
        working_directory.mkdir(parents=True, exist_ok=True)
        environment = {
            key: value
            for key, value in os.environ.items()
            if key.upper() in SAFE_HOST_ENVIRONMENT_KEYS
        }
        environment.update(request.environment)
        started_at = time.monotonic()

        try:
            completed = subprocess.run(
                request.argv,
                cwd=working_directory,
                env=environment,
                capture_output=True,
                check=False,
                shell=False,
                timeout=request.timeout_seconds,
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
        except subprocess.TimeoutExpired as error:
            duration = time.monotonic() - started_at
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

    def _decode(self, output: bytes) -> tuple[str, bool]:
        truncated = len(output) > self._maximum_output_bytes
        kept = output[: self._maximum_output_bytes]
        return kept.decode("utf-8", errors="replace"), truncated

    def close(self) -> None:
        if not self._closed:
            self._temporary_directory.cleanup()
            self._closed = True

    def __enter__(self) -> LocalSandbox:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
