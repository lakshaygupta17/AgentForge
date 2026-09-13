from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from agentforge.core.models import ActionRequest, ExecutionResult
from agentforge.policy.engine import SecurityPolicy
from agentforge.tools.quality import QualityCheckKind, RepositoryQualityTools


class RecordingSandbox:
    def __init__(self, workspace: Path, exit_codes: list[int] | None = None) -> None:
        self._workspace = workspace
        self.requests: list[ActionRequest] = []
        self.exit_codes = exit_codes or [0]

    @property
    def workspace(self) -> Path:
        return self._workspace

    def execute(
        self, request: ActionRequest, policy: SecurityPolicy
    ) -> ExecutionResult:
        self.requests.append(request)
        index = min(len(self.requests) - 1, len(self.exit_codes) - 1)
        return ExecutionResult(self.exit_codes[index], "ok", "", 0.1)

    def close(self) -> None:
        return None


class RepositoryQualityToolsTests(TestCase):
    def test_fixed_commands_use_python_modules_without_shell(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            sandbox = RecordingSandbox(Path(temporary_directory))
            tools = RepositoryQualityTools(sandbox, SecurityPolicy())

            test = tools.test(("tests",))
            lint = tools.lint(("src", "tests"))
            types = tools.type_check(("src",))

        self.assertEqual(test.kind, QualityCheckKind.TEST)
        self.assertEqual(lint.kind, QualityCheckKind.LINT)
        self.assertEqual(types.kind, QualityCheckKind.TYPE_CHECK)
        self.assertTrue(test.passed)
        self.assertEqual(sandbox.requests[0].arguments[1:3], ("-m", "pytest"))
        self.assertEqual(sandbox.requests[1].arguments[1:4], ("-m", "ruff", "check"))
        self.assertEqual(sandbox.requests[2].arguments[1:3], ("-m", "mypy"))

    def test_run_all_stops_after_first_failed_quality_gate(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            sandbox = RecordingSandbox(Path(temporary_directory), [0, 1, 0])
            results = RepositoryQualityTools(sandbox, SecurityPolicy()).run_all()

        self.assertEqual([result.kind for result in results], [
            QualityCheckKind.TEST,
            QualityCheckKind.LINT,
        ])
        self.assertFalse(results[-1].passed)
        self.assertEqual(len(sandbox.requests), 2)

    def test_rejects_path_escape_and_option_injection(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            sandbox = RecordingSandbox(Path(temporary_directory))
            tools = RepositoryQualityTools(sandbox, SecurityPolicy())

            for path in ("../outside", "/absolute", "--config=attacker.toml", ""):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    tools.lint((path,))

        self.assertEqual(sandbox.requests, [])
