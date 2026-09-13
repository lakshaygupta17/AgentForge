import sys
from os import environ
from pathlib import Path
from unittest import TestCase

from agentforge.core.models import ActionRequest
from agentforge.policy.engine import SecurityPolicy
from agentforge.sandbox.local import LocalSandbox


class LocalSandboxTests(TestCase):
    def test_executes_a_trusted_command_in_the_temporary_workspace(self) -> None:
        with LocalSandbox() as sandbox:
            request = ActionRequest(
                executable=sys.executable,
                arguments=(
                    "-c",
                    "__import__('builtins').print(__import__('pathlib').Path.cwd().name)",
                ),
            )

            result = sandbox.execute(request, SecurityPolicy())

            self.assertTrue(result.succeeded)
            self.assertEqual(result.stdout.strip(), sandbox.workspace.name)

    def test_reports_timeout_without_raising(self) -> None:
        with LocalSandbox() as sandbox:
            request = ActionRequest(
                executable=sys.executable,
                arguments=("-c", "__import__('time').sleep(1)"),
                timeout_seconds=0.05,
            )

            result = sandbox.execute(request, SecurityPolicy())

            self.assertTrue(result.timed_out)
            self.assertIsNone(result.exit_code)

    def test_policy_denial_prevents_execution(self) -> None:
        with LocalSandbox() as sandbox:
            request = ActionRequest("sudo", ("anything",))

            with self.assertRaises(PermissionError):
                sandbox.execute(request, SecurityPolicy())

    def test_marks_large_output_as_truncated(self) -> None:
        with LocalSandbox(maximum_output_bytes=5) as sandbox:
            request = ActionRequest(
                executable=sys.executable,
                arguments=("-c", "print('abcdefghij')"),
            )

            result = sandbox.execute(request, SecurityPolicy())

            self.assertTrue(result.output_truncated)
            self.assertEqual(result.stdout, "abcde")

    def test_policy_caps_the_sandbox_output_budget_before_execution(self) -> None:
        with LocalSandbox(maximum_output_bytes=6) as sandbox:
            request = ActionRequest(sys.executable, ("-c", "print('never runs')"))

            with self.assertRaisesRegex(PermissionError, "Output request"):
                sandbox.execute(request, SecurityPolicy(maximum_output_bytes=5))

    def test_workspace_is_removed_when_context_closes(self) -> None:
        with LocalSandbox() as sandbox:
            workspace = Path(sandbox.workspace)
            self.assertTrue(workspace.exists())

        self.assertFalse(workspace.exists())

    def test_does_not_inherit_arbitrary_host_secrets(self) -> None:
        environ["AGENTFORGE_TEST_SECRET"] = "must-not-leak"
        try:
            with LocalSandbox() as sandbox:
                request = ActionRequest(
                    executable=sys.executable,
                    arguments=(
                        "-c",
                        "print(__import__('os').environ.get('AGENTFORGE_TEST_SECRET'))",
                    ),
                )

                result = sandbox.execute(request, SecurityPolicy())
        finally:
            environ.pop("AGENTFORGE_TEST_SECRET", None)

        self.assertTrue(result.succeeded)
        self.assertEqual(result.stdout.strip(), "None")
