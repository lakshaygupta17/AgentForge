"""Explicit, capability-scoped human approval for sensitive operations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ApprovalCapability(StrEnum):
    CODE_TRANSMISSION = "code_transmission"
    SANDBOX_EXECUTION = "sandbox_execution"


class ApprovalRequiredError(PermissionError):
    def __init__(self, capability: ApprovalCapability) -> None:
        self.capability = capability
        super().__init__(f"Human approval is required for {capability.value}")


@dataclass(frozen=True, slots=True)
class ApprovalCheckpoint:
    """Keep independent approvals separate and fail closed when one is absent."""

    code_transmission: bool = False
    sandbox_execution: bool = False

    def require(self, capability: ApprovalCapability) -> None:
        approved = {
            ApprovalCapability.CODE_TRANSMISSION: self.code_transmission,
            ApprovalCapability.SANDBOX_EXECUTION: self.sandbox_execution,
        }[capability]
        if not approved:
            raise ApprovalRequiredError(capability)

    def granted_capabilities(self) -> tuple[str, ...]:
        return tuple(
            capability.value
            for capability in ApprovalCapability
            if (
                capability is ApprovalCapability.CODE_TRANSMISSION
                and self.code_transmission
            )
            or (
                capability is ApprovalCapability.SANDBOX_EXECUTION
                and self.sandbox_execution
            )
        )
