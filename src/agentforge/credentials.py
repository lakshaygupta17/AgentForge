"""Host-only credential access and sandbox delegation guardrails."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass

_SECRET_NAME = re.compile(
    r"(?:^|_)(?:api_?key|token|secret|password|credential|private_?key)(?:$|_)",
    re.IGNORECASE,
)


class CredentialConfigurationError(ValueError):
    """A required host-only credential or setting is unavailable or misused."""


@dataclass(frozen=True, slots=True)
class HostCredentialBroker:
    """Read provider credentials for host control planes, never guest processes."""

    environment: Mapping[str, str]

    @classmethod
    def from_process(cls) -> HostCredentialBroker:
        return cls(os.environ)

    def require_secret(self, variable: str) -> str:
        value = self.environment.get(variable, "").strip()
        if not value:
            raise CredentialConfigurationError(
                f"Missing required environment variable: {variable}"
            )
        return value

    def require_setting(self, variable: str) -> str:
        if self.is_secret_name(variable):
            raise CredentialConfigurationError(
                f"Credential setting {variable!r} must use require_secret"
            )
        value = self.environment.get(variable, "").strip()
        if not value:
            raise CredentialConfigurationError(
                f"Missing required environment variable: {variable}"
            )
        return value

    def is_configured(self, variable: str) -> bool:
        return bool(self.environment.get(variable, "").strip())

    @staticmethod
    def is_secret_name(variable: str) -> bool:
        return _SECRET_NAME.search(variable) is not None


def contains_credential_environment(environment: Mapping[str, str]) -> bool:
    """Return true when an action tries to delegate a likely credential."""

    return any(HostCredentialBroker.is_secret_name(name) for name in environment)
