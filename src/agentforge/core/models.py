"""Small, dependency-free types shared across AgentForge's layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from agentforge.context.memory import RunContextMemory
from agentforge.core.approval import ApprovalCheckpoint


@dataclass(frozen=True, slots=True)
class Task:
    """A software-engineering goal supplied to the agent team."""

    description: str
    repository: str | None = None
    id: str = field(default_factory=lambda: str(uuid4()))

    def __post_init__(self) -> None:
        if not self.description.strip():
            raise ValueError("A task description cannot be empty")


@dataclass(frozen=True, slots=True)
class ActionRequest:
    """A structured process request. It is deliberately not a shell string."""

    executable: str
    arguments: tuple[str, ...] = ()
    working_directory: Path = Path(".")
    timeout_seconds: float = 60.0
    environment: dict[str, str] = field(default_factory=dict)

    @property
    def argv(self) -> tuple[str, ...]:
        return (self.executable, *self.arguments)


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """Backend-neutral result returned for every command execution."""

    exit_code: int | None
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False
    output_truncated: bool = False

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


@dataclass(frozen=True, slots=True)
class SandboxLimits:
    """Resource and connectivity boundaries applied to a sandbox."""

    cpu_count: float = 1.0
    memory_mb: int = 512
    process_limit: int = 64
    maximum_output_bytes: int = 1_000_000
    network_access: bool = False
    protect_host_filesystem: bool = True

    def __post_init__(self) -> None:
        if self.cpu_count <= 0:
            raise ValueError("CPU count must be greater than zero")
        if self.memory_mb <= 0:
            raise ValueError("Memory must be greater than zero")
        if self.process_limit <= 0:
            raise ValueError("Process limit must be greater than zero")
        if self.maximum_output_bytes <= 0:
            raise ValueError("Maximum output bytes must be greater than zero")


@dataclass(slots=True)
class WorkflowState:
    """Mutable state handed from one specialist agent to the next."""

    task: Task
    run_id: str = field(default_factory=lambda: str(uuid4()))
    artifacts: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    completed_agents: list[str] = field(default_factory=list)
    failed: bool = False
    failure_reason: str | None = None
    memory: RunContextMemory = field(default_factory=RunContextMemory)
    approval: ApprovalCheckpoint = field(default_factory=ApprovalCheckpoint)
