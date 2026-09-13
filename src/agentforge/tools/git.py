"""Fixed read-only Git commands routed through policy and sandbox interfaces."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agentforge.core.models import ActionRequest
from agentforge.policy.engine import SecurityPolicy
from agentforge.sandbox.base import Sandbox


class GitToolError(RuntimeError):
    """A read-only Git inspection command could not complete."""


@dataclass(slots=True)
class GitRepositoryTools:
    sandbox: Sandbox
    policy: SecurityPolicy
    working_directory: Path = Path(".")
    timeout_seconds: float = 30.0

    def status(self) -> str:
        return self._execute(("status", "--short", "--untracked-files=all"))

    def diff(self) -> str:
        return self._execute(("diff", "--no-ext-diff", "--"))

    def _execute(self, arguments: tuple[str, ...]) -> str:
        result = self.sandbox.execute(
            ActionRequest(
                "git",
                arguments,
                working_directory=self.working_directory,
                timeout_seconds=self.timeout_seconds,
            ),
            self.policy,
        )
        if not result.succeeded:
            detail = result.stderr.strip() or result.stdout.strip() or "unknown Git error"
            raise GitToolError(f"git {arguments[0]} failed: {detail}")
        return result.stdout
