import io
import os
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from agentforge.cli import build_parser, main


class CredentialCheckCliTests(TestCase):
    def test_reports_readiness_without_printing_secret_values(self) -> None:
        environment = {
            "HF_TOKEN": "hf_private-test-value",
            "AGENTFORGE_MODEL": "example/model",
            "E2B_API_KEY": "private-e2b-value",
        }
        output = io.StringIO()
        with (
            patch.dict(os.environ, environment, clear=True),
            patch("agentforge.cli.load_project_environment"),
            redirect_stdout(output),
        ):
            exit_code = main(["credentials-check"])

        displayed = output.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertEqual(displayed.count("CONFIGURED"), 3)
        self.assertNotIn("hf_private-test-value", displayed)
        self.assertNotIn("private-e2b-value", displayed)

    def test_returns_two_when_a_required_setting_is_missing(self) -> None:
        output = io.StringIO()
        with (
            patch.dict(os.environ, {"HF_TOKEN": "present"}, clear=True),
            patch("agentforge.cli.load_project_environment"),
            redirect_stdout(output),
        ):
            exit_code = main(["credentials-check"])

        self.assertEqual(exit_code, 2)
        self.assertIn("MISSING (AGENTFORGE_MODEL)", output.getvalue())
        self.assertIn("MISSING (E2B_API_KEY)", output.getvalue())

    def test_rejects_non_hugging_face_token_format(self) -> None:
        output = io.StringIO()
        environment = {
            "HF_TOKEN": "not-a-user-access-token",
            "AGENTFORGE_MODEL": "example/model",
            "E2B_API_KEY": "present",
        }
        with (
            patch.dict(os.environ, environment, clear=True),
            patch("agentforge.cli.load_project_environment"),
            redirect_stdout(output),
        ):
            exit_code = main(["credentials-check"])

        self.assertEqual(exit_code, 2)
        self.assertIn("INVALID FORMAT", output.getvalue())
        self.assertNotIn("not-a-user-access-token", output.getvalue())


class LiveExploreCliTests(TestCase):
    def test_rejects_credentials_in_task_before_any_consent_or_provider_call(self) -> None:
        with self.assertRaisesRegex(SystemExit, "appears to contain a credential"):
            main(
                [
                    "live-explore",
                    "--workspace",
                    ".",
                    "--task",
                    "Debug this with HF_TOKEN=hf_private",
                ]
            )

    def test_requires_explicit_code_transmission_flag_before_provider_setup(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            with self.assertRaises(SystemExit) as raised:
                main(
                    [
                        "live-explore",
                        "--workspace",
                        temporary_directory,
                        "--task",
                        "Inspect this repository",
                    ]
                )

        self.assertIn("--allow-code-transmission", str(raised.exception))

    def test_rejects_missing_workspace_after_transmission_is_acknowledged(self) -> None:
        missing = Path("definitely-missing-agentforge-workspace")
        with self.assertRaises(SystemExit) as raised:
            main(
                [
                    "live-explore",
                    "--workspace",
                    str(missing),
                    "--task",
                    "Inspect this repository",
                    "--allow-code-transmission",
                ]
            )

        self.assertIn("not a directory", str(raised.exception))


class PolicyFileCliTests(TestCase):
    def test_policy_check_loads_yaml_policy(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            policy = Path(temporary_directory) / "policy.yaml"
            policy.write_text(
                "version: 1\ncommands:\n  forbidden_executables: [python]\n",
                encoding="utf-8",
            )
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "policy-check",
                        "--policy",
                        str(policy),
                        "--",
                        "python",
                        "-m",
                        "pytest",
                    ]
                )

        self.assertEqual(exit_code, 2)
        self.assertIn("DENIED", output.getvalue())
        self.assertIn("forbidden", output.getvalue())

    def test_policy_check_rejects_invalid_yaml_policy(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            policy = Path(temporary_directory) / "policy.yaml"
            policy.write_text("version: 99\n", encoding="utf-8")

            with self.assertRaisesRegex(SystemExit, "Invalid policy"):
                main(
                    [
                        "policy-check",
                        "--policy",
                        str(policy),
                        "--",
                        "python",
                    ]
                )


class LiveRepairCliTests(TestCase):
    def test_accepts_e2b_as_live_repair_backend(self) -> None:
        arguments = build_parser().parse_args(
            [
                "live-repair",
                "--workspace",
                "source",
                "--output",
                "output",
                "--task",
                "repair it",
                "--sandbox-backend",
                "e2b",
                "--e2b-template",
                "agentforge",
            ]
        )

        self.assertEqual(arguments.sandbox_backend, "e2b")
        self.assertEqual(arguments.e2b_template, "agentforge")

    def test_requires_code_transmission_consent_before_docker_setup(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            with self.assertRaises(SystemExit) as raised:
                main(
                    [
                        "live-repair",
                        "--workspace",
                        temporary_directory,
                        "--output",
                        str(Path(temporary_directory) / "output"),
                        "--task",
                        "Repair it",
                    ]
                )

        self.assertIn("--allow-code-transmission", str(raised.exception))

    def test_requires_sandbox_execution_consent_before_docker_setup(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            with self.assertRaises(SystemExit) as raised:
                main(
                    [
                        "live-repair",
                        "--workspace",
                        temporary_directory,
                        "--output",
                        str(Path(temporary_directory) / "output"),
                        "--task",
                        "Repair it",
                        "--allow-code-transmission",
                    ]
                )

        self.assertIn("--allow-sandbox-execution", str(raised.exception))

    def test_rejects_an_existing_output_before_docker_setup(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            with self.assertRaises(SystemExit) as raised:
                main(
                    [
                        "live-repair",
                        "--workspace",
                        temporary_directory,
                        "--output",
                        temporary_directory,
                        "--task",
                        "Repair it",
                        "--allow-code-transmission",
                        "--allow-sandbox-execution",
                    ]
                )

        self.assertIn("must not already exist", str(raised.exception))


class LocalServiceCliTests(TestCase):
    def test_serve_binds_only_to_loopback(self) -> None:
        with (
            patch("agentforge.cli.load_project_environment"),
            patch("uvicorn.run") as run,
        ):
            exit_code = main(["serve", "--port", "8877"])

        self.assertEqual(exit_code, 0)
        _, keyword_arguments = run.call_args
        self.assertEqual(keyword_arguments["host"], "127.0.0.1")
        self.assertEqual(keyword_arguments["port"], 8877)

    def test_serve_rejects_privileged_or_invalid_ports(self) -> None:
        with (
            patch("agentforge.cli.load_project_environment"),
            self.assertRaisesRegex(SystemExit, "between 1024 and 65535"),
        ):
            main(["serve", "--port", "80"])
