"""Fixed, policy-gated test, lint, and type-check commands."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath

from agentforge.core.models import ActionRequest, ExecutionResult
from agentforge.policy.engine import SecurityPolicy
from agentforge.sandbox.base import Sandbox


class QualityCheckKind(StrEnum):
    TEST = "test"
    LINT = "lint"
    TYPE_CHECK = "type-check"


@dataclass(frozen=True, slots=True)
class QualityCheckResult:
    kind: QualityCheckKind
    command: tuple[str, ...]
    execution: ExecutionResult

    @property
    def passed(self) -> bool:
        return self.execution.succeeded


@dataclass(slots=True)
class RepositoryQualityTools:
    sandbox: Sandbox
    policy: SecurityPolicy
    working_directory: Path = Path(".")
    timeout_seconds: float = 120

    def test(self, paths: tuple[str, ...] = ()) -> QualityCheckResult:
        return self._run(QualityCheckKind.TEST, "pytest", (*paths, "-q"), paths)

    def lint(self, paths: tuple[str, ...] = (".",)) -> QualityCheckResult:
        return self._run(QualityCheckKind.LINT, "ruff", ("check", *paths), paths)

    def type_check(self, paths: tuple[str, ...] = (".",)) -> QualityCheckResult:
        return self._run(QualityCheckKind.TYPE_CHECK, "mypy", paths, paths)

    def run_all(self) -> tuple[QualityCheckResult, ...]:
        results: list[QualityCheckResult] = []
        for check in (self.test, self.lint, self.type_check):
            result = check()
            results.append(result)
            if not result.passed:
                break
        return tuple(results)

    def _run(
        self,
        kind: QualityCheckKind,
        module: str,
        arguments: tuple[str, ...],
        paths: tuple[str, ...],
    ) -> QualityCheckResult:
        self._validate_paths(paths)
        command = ("-B", "-m", module, *arguments)
        result = self.sandbox.execute(
            ActionRequest(
                sys.executable,
                command,
                working_directory=self.working_directory,
                timeout_seconds=self.timeout_seconds,
            ),
            self.policy,
        )
        return QualityCheckResult(kind, (sys.executable, *command), result)

    @staticmethod
    def _validate_paths(values: tuple[str, ...]) -> None:
        for value in values:
            if not value or value.startswith("-"):
                raise ValueError("Quality-check paths must be non-empty positional paths")
            path = PurePosixPath(value.replace("\\", "/"))
            if path.is_absolute() or ".." in path.parts or "\x00" in value:
                raise ValueError(f"Quality-check path is unsafe: {value}")
