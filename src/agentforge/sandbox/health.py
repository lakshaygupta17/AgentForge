"""Non-destructive readiness checks for AgentForge sandbox backends."""

from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass

from agentforge.sandbox.errors import SandboxUnavailableError
from agentforge.sandbox.sbx import SbxSandbox, _resolve_sbx_executable


@dataclass(frozen=True, slots=True)
class SandboxHealth:
    backend: str
    ready: bool
    probe_level: str
    detail: str
    live_verified: bool


def check_sandbox_health(backend: str) -> SandboxHealth:
    """Check local prerequisites without creating a VM or consuming cloud credit."""

    if backend == "local":
        return SandboxHealth(
            "local",
            True,
            "runtime",
            "Temporary local process backend is available for trusted fixtures",
            True,
        )
    if backend == "e2b":
        sdk_present = importlib.util.find_spec("e2b") is not None
        key_present = bool(os.environ.get("E2B_API_KEY", "").strip())
        if not sdk_present:
            detail = "E2B SDK is not installed in the project environment"
        elif not key_present:
            detail = "E2B_API_KEY is not configured"
        else:
            detail = "E2B SDK and key are configured; this check did not create a VM"
        return SandboxHealth(
            "e2b", sdk_present and key_present, "configuration", detail, False
        )
    if backend == "sbx":
        if _resolve_sbx_executable() is None:
            return SandboxHealth(
                "sbx",
                False,
                "installation",
                "Docker Sandboxes CLI is not installed",
                False,
            )
        try:
            with SbxSandbox() as sandbox:
                sandbox.check_available()
        except (SandboxUnavailableError, OSError, RuntimeError) as error:
            return SandboxHealth("sbx", False, "cli", str(error), False)
        return SandboxHealth(
            "sbx",
            True,
            "cli",
            "sbx CLI responds; this does not prove that a microVM can start",
            False,
        )
    raise ValueError(f"Unknown sandbox backend '{backend}'")


def check_all_sandbox_health() -> tuple[SandboxHealth, ...]:
    return tuple(check_sandbox_health(name) for name in ("local", "sbx", "e2b"))
