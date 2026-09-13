"""The interface shared by local, Docker, E2B, and future sandboxes."""

from pathlib import Path
from typing import Protocol

from agentforge.core.models import ActionRequest, ExecutionResult
from agentforge.policy.engine import SecurityPolicy


class Sandbox(Protocol):
    @property
    def workspace(self) -> Path: ...

    def execute(self, request: ActionRequest, policy: SecurityPolicy) -> ExecutionResult: ...

    def close(self) -> None: ...

