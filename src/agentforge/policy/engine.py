"""Pre-execution checks for structured command requests."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from agentforge.core.models import ActionRequest, SandboxLimits
from agentforge.credentials import contains_credential_environment

DEFAULT_FORBIDDEN_EXECUTABLES = frozenset(
    {
        "ash",
        "bash",
        "cmd",
        "cmd.exe",
        "dash",
        "fish",
        "mount",
        "powershell",
        "powershell.exe",
        "pwsh",
        "sh",
        "shutdown",
        "sudo",
        "su",
        "zsh",
    }
)
SHELL_CONTROL_CHARACTERS = frozenset({"\n", "\r", "\x00", ";", "|", "&", "`"})


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    allowed: bool
    reasons: tuple[str, ...] = ()


FileOperation = Literal["read", "write"]


@dataclass(frozen=True, slots=True)
class FileAccessRequest:
    path: Path
    operation: FileOperation

    def __post_init__(self) -> None:
        if self.operation not in {"read", "write"}:
            raise ValueError("File operation must be 'read' or 'write'")


@dataclass(slots=True)
class SecurityPolicy:
    """A conservative first policy that fails closed on malformed requests."""

    forbidden_executables: frozenset[str] = DEFAULT_FORBIDDEN_EXECUTABLES
    maximum_timeout_seconds: float = 120.0
    allowed_environment_keys: frozenset[str] = field(default_factory=frozenset)
    maximum_cpu_count: float = 2.0
    maximum_memory_mb: int = 2048
    maximum_processes: int = 128
    maximum_output_bytes: int = 1_000_000
    allow_network_access: bool = False
    allowed_network_destinations: frozenset[str] = field(default_factory=frozenset)
    readable_paths: tuple[str, ...] = ("*", "**/*")
    writable_paths: tuple[str, ...] = ("*", "**/*")
    denied_paths: tuple[str, ...] = (
        ".env",
        "**/.env",
        ".env.development",
        ".env.local",
        ".env.production",
        "**/.env.development",
        "**/.env.local",
        "**/.env.production",
        "*.key",
        "*.pem",
        "**/*.key",
        "**/*.pem",
    )
    allowed_git_subcommands: frozenset[str] = field(
        default_factory=lambda: frozenset({"diff", "status"})
    )

    def evaluate(self, request: ActionRequest, workspace: Path) -> PolicyDecision:
        reasons: list[str] = []
        executable = request.executable.strip()

        if not executable:
            reasons.append("Executable cannot be empty")
        elif Path(executable).name.casefold() in {
            name.casefold() for name in self.forbidden_executables
        }:
            reasons.append(f"Executable '{Path(executable).name}' is forbidden")

        executable_name = Path(executable).name.casefold()
        if executable_name in {"git", "git.exe"}:
            subcommand = request.arguments[0].casefold() if request.arguments else ""
            allowed_subcommands = {
                value.casefold() for value in self.allowed_git_subcommands
            }
            if subcommand not in allowed_subcommands:
                shown_subcommand = subcommand or "<missing>"
                reasons.append(f"Git subcommand '{shown_subcommand}' is not allowed")

        for value in request.argv:
            if any(character in value for character in SHELL_CONTROL_CHARACTERS):
                reasons.append("Command contains shell-control characters")
                break

        if request.timeout_seconds <= 0:
            reasons.append("Timeout must be greater than zero")
        elif request.timeout_seconds > self.maximum_timeout_seconds:
            reasons.append(
                f"Timeout exceeds the {self.maximum_timeout_seconds:g}-second policy limit"
            )

        workspace_path = workspace.resolve()
        requested_path = (workspace_path / request.working_directory).resolve()
        if not requested_path.is_relative_to(workspace_path):
            reasons.append("Working directory escapes the sandbox workspace")

        if contains_credential_environment(request.environment):
            reasons.append("Credentials cannot be delegated to sandbox processes")
        unexpected_keys = set(request.environment) - set(self.allowed_environment_keys)
        if unexpected_keys:
            reasons.append(
                "Environment keys are not allowed: " + ", ".join(sorted(unexpected_keys))
            )

        return PolicyDecision(allowed=not reasons, reasons=tuple(reasons))

    def evaluate_file_access(
        self, request: FileAccessRequest, workspace: Path
    ) -> PolicyDecision:
        workspace_path = workspace.resolve()
        requested_path = (workspace_path / request.path).resolve()
        if not requested_path.is_relative_to(workspace_path):
            return PolicyDecision(False, ("File path escapes the sandbox workspace",))
        relative = requested_path.relative_to(workspace_path)
        if any(relative.match(pattern) for pattern in self.denied_paths):
            return PolicyDecision(False, ("File path is explicitly denied by policy",))
        patterns = (
            self.readable_paths if request.operation == "read" else self.writable_paths
        )
        if not any(relative.match(pattern) for pattern in patterns):
            return PolicyDecision(
                False,
                (f"File {request.operation} is outside the policy allowlist",),
            )
        return PolicyDecision(True)

    def evaluate_network_destination(self, destination: str) -> PolicyDecision:
        candidate = destination.strip()
        parsed = urlsplit(candidate if "://" in candidate else f"//{candidate}")
        hostname = (parsed.hostname or "").casefold().rstrip(".")
        if not hostname:
            return PolicyDecision(False, ("Network destination is malformed",))
        allowed = {value.casefold().rstrip(".") for value in self.allowed_network_destinations}
        if hostname not in allowed:
            return PolicyDecision(False, (f"Network destination '{hostname}' is not allowed",))
        return PolicyDecision(True)

    def evaluate_sandbox(self, limits: SandboxLimits) -> PolicyDecision:
        """Check requested container boundaries before creating the sandbox."""

        reasons: list[str] = []
        if limits.cpu_count > self.maximum_cpu_count:
            reasons.append(
                f"CPU request exceeds the {self.maximum_cpu_count:g}-CPU policy limit"
            )
        if limits.memory_mb > self.maximum_memory_mb:
            reasons.append(
                f"Memory request exceeds the {self.maximum_memory_mb}-MB policy limit"
            )
        if limits.process_limit > self.maximum_processes:
            reasons.append(
                f"Process request exceeds the {self.maximum_processes}-process policy limit"
            )
        if limits.maximum_output_bytes > self.maximum_output_bytes:
            reasons.append(
                "Output request exceeds the "
                f"{self.maximum_output_bytes}-byte policy limit"
            )
        if limits.network_access and not self.allow_network_access:
            reasons.append("Network access is denied by policy")
        if not limits.protect_host_filesystem:
            reasons.append("Host filesystem protection is required by policy")
        return PolicyDecision(allowed=not reasons, reasons=tuple(reasons))
