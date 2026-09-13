from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from agentforge.core.models import ActionRequest, SandboxLimits
from agentforge.policy.engine import FileAccessRequest, SecurityPolicy


class SecurityPolicyTests(TestCase):
    def setUp(self) -> None:
        self.policy = SecurityPolicy()
        self.temporary_directory = TemporaryDirectory()
        self.workspace = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_allows_a_structured_python_command(self) -> None:
        request = ActionRequest("python", ("-m", "unittest"))

        decision = self.policy.evaluate(request, self.workspace)

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reasons, ())

    def test_rejects_forbidden_executable_case_insensitively(self) -> None:
        request = ActionRequest("PowerShell.EXE", ("-Command", "Get-ChildItem"))

        decision = self.policy.evaluate(request, self.workspace)

        self.assertFalse(decision.allowed)
        self.assertIn("forbidden", " ".join(decision.reasons))

    def test_rejects_shell_control_characters(self) -> None:
        request = ActionRequest("python", ("safe.py; shutdown",))

        decision = self.policy.evaluate(request, self.workspace)

        self.assertFalse(decision.allowed)
        self.assertIn("shell-control", " ".join(decision.reasons))

    def test_rejects_workspace_escape(self) -> None:
        request = ActionRequest("python", ("script.py",), working_directory=Path(".."))

        decision = self.policy.evaluate(request, self.workspace)

        self.assertFalse(decision.allowed)
        self.assertIn("escapes", " ".join(decision.reasons))

    def test_rejects_unapproved_environment_keys(self) -> None:
        request = ActionRequest("python", environment={"SECRET": "value"})

        decision = self.policy.evaluate(request, self.workspace)

        self.assertFalse(decision.allowed)
        self.assertIn("SECRET", " ".join(decision.reasons))

    def test_filesystem_policy_separates_read_and_write_allowlists(self) -> None:
        policy = SecurityPolicy(
            readable_paths=("src/*",),
            writable_paths=("tests/*",),
        )

        readable = policy.evaluate_file_access(
            FileAccessRequest(Path("src/module.py"), "read"), self.workspace
        )
        not_writable = policy.evaluate_file_access(
            FileAccessRequest(Path("src/module.py"), "write"), self.workspace
        )

        self.assertTrue(readable.allowed)
        self.assertFalse(not_writable.allowed)
        self.assertIn("allowlist", " ".join(not_writable.reasons))

    def test_filesystem_policy_rejects_escape_and_sensitive_paths(self) -> None:
        escaped = self.policy.evaluate_file_access(
            FileAccessRequest(Path("../secret.txt"), "read"), self.workspace
        )
        sensitive = self.policy.evaluate_file_access(
            FileAccessRequest(Path("nested/.env"), "read"), self.workspace
        )

        self.assertFalse(escaped.allowed)
        self.assertFalse(sensitive.allowed)

    def test_network_destination_requires_exact_allowlisted_hostname(self) -> None:
        policy = SecurityPolicy(
            allowed_network_destinations=frozenset({"pypi.org"})
        )

        allowed = policy.evaluate_network_destination("https://pypi.org/simple")
        lookalike = policy.evaluate_network_destination("https://pypi.org.attacker.test")

        self.assertTrue(allowed.allowed)
        self.assertFalse(lookalike.allowed)

    def test_rejects_sandbox_output_budget_above_policy_limit(self) -> None:
        policy = SecurityPolicy(maximum_output_bytes=128)

        decision = policy.evaluate_sandbox(SandboxLimits(maximum_output_bytes=129))

        self.assertFalse(decision.allowed)
        self.assertIn("Output request", " ".join(decision.reasons))
