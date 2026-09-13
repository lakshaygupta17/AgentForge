import io
import os
from contextlib import redirect_stdout
from unittest import TestCase
from unittest.mock import patch

from agentforge.cli import main
from agentforge.sandbox.health import (
    SandboxHealth,
    check_all_sandbox_health,
    check_sandbox_health,
)


class SandboxHealthTests(TestCase):
    def test_local_health_is_runtime_verified(self) -> None:
        report = check_sandbox_health("local")

        self.assertTrue(report.ready)
        self.assertTrue(report.live_verified)
        self.assertEqual(report.probe_level, "runtime")

    def test_e2b_health_is_configuration_only_and_never_creates_vm(self) -> None:
        with (
            patch("agentforge.sandbox.health.importlib.util.find_spec", return_value=object()),
            patch.dict(os.environ, {"E2B_API_KEY": "configured"}, clear=True),
        ):
            report = check_sandbox_health("e2b")

        self.assertTrue(report.ready)
        self.assertFalse(report.live_verified)
        self.assertEqual(report.probe_level, "configuration")

    def test_e2b_health_reports_missing_key(self) -> None:
        with (
            patch("agentforge.sandbox.health.importlib.util.find_spec", return_value=object()),
            patch.dict(os.environ, {}, clear=True),
        ):
            report = check_sandbox_health("e2b")

        self.assertFalse(report.ready)
        self.assertIn("E2B_API_KEY", report.detail)

    def test_sbx_health_distinguishes_cli_from_live_vm(self) -> None:
        class ReadySandbox:
            def __enter__(self) -> "ReadySandbox":
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def check_available(self) -> None:
                return None

        with (
            patch("agentforge.sandbox.health._resolve_sbx_executable", return_value="sbx"),
            patch("agentforge.sandbox.health.SbxSandbox", return_value=ReadySandbox()),
        ):
            report = check_sandbox_health("sbx")

        self.assertTrue(report.ready)
        self.assertFalse(report.live_verified)
        self.assertIn("does not prove", report.detail)

    def test_cli_returns_nonzero_when_any_backend_is_not_ready(self) -> None:
        reports = (
            SandboxHealth("local", True, "runtime", "ready", True),
            SandboxHealth("sbx", False, "installation", "missing", False),
        )
        output = io.StringIO()
        with (
            patch("agentforge.cli.load_project_environment"),
            patch("agentforge.cli.check_all_sandbox_health", return_value=reports),
            redirect_stdout(output),
        ):
            exit_code = main(["sandbox-health"])

        self.assertEqual(exit_code, 2)
        self.assertIn('"backend": "sbx"', output.getvalue())

    def test_all_health_checks_have_stable_order(self) -> None:
        with patch("agentforge.sandbox.health.check_sandbox_health") as check:
            check.side_effect = lambda name: SandboxHealth(
                name, True, "test", "ready", False
            )
            reports = check_all_sandbox_health()

        self.assertEqual([report.backend for report in reports], ["local", "sbx", "e2b"])
