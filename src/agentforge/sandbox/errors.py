"""Explicit sandbox errors suitable for CLI and API responses."""


class SandboxError(RuntimeError):
    """Base error for sandbox setup and lifecycle failures."""


class SandboxUnavailableError(SandboxError):
    """The requested sandbox backend is not installed or reachable."""


class SandboxLifecycleError(SandboxError):
    """A sandbox could not be created, started, or removed correctly."""

