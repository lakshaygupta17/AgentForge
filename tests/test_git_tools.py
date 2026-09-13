from dataclasses import dataclass, field
from pathlib import Path
from unittest import TestCase

from agentforge.core.models import ActionRequest, ExecutionResult
from agentforge.policy.engine import SecurityPolicy
from agentforge.tools.git import GitRepositoryTools, GitToolError


@dataclass
class FakeSandbox:
    result: ExecutionResult
    workspace: Path = Path(".")
    requests: list[ActionRequest] = field(default_factory=list)

    def execute(
        self, request: ActionRequest, policy: SecurityPolicy
    ) -> ExecutionResult:
        decision = policy.evaluate(request, self.workspace)
        if not decision.allowed:
            raise PermissionError("; ".join(decision.reasons))
        self.requests.append(request)
        return self.result

    def close(self) -> None:
        pass


class GitRepositoryToolsTests(TestCase):
    def test_status_and_diff_use_fixed_read_only_commands(self) -> None:
        sandbox = FakeSandbox(ExecutionResult(0, " M app.py\n", "", 0.01))
        tools = GitRepositoryTools(sandbox, SecurityPolicy())

        self.assertEqual(tools.status(), " M app.py\n")
        self.assertEqual(tools.diff(), " M app.py\n")

        self.assertEqual(
            sandbox.requests[0].arguments,
            ("status", "--short", "--untracked-files=all"),
        )
        self.assertEqual(
            sandbox.requests[1].arguments,
            ("diff", "--no-ext-diff", "--"),
        )

    def test_mutating_git_command_is_denied_by_policy(self) -> None:
        decision = SecurityPolicy().evaluate(
            ActionRequest("git", ("checkout", "--", "app.py")), Path.cwd()
        )

        self.assertFalse(decision.allowed)
        self.assertIn("not allowed", " ".join(decision.reasons))

    def test_nonzero_git_result_becomes_a_tool_error(self) -> None:
        sandbox = FakeSandbox(
            ExecutionResult(128, "", "not a git repository", 0.01)
        )

        with self.assertRaisesRegex(GitToolError, "not a git repository"):
            GitRepositoryTools(sandbox, SecurityPolicy()).status()

