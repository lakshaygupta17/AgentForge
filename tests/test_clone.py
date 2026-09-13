import subprocess
from collections.abc import Sequence
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from agentforge.cli import main
from agentforge.tools.clone import (
    RepositoryCloneError,
    RepositoryClonePolicy,
    SafeRepositoryCloner,
)


class FixtureCloneRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def run(
        self, arguments: Sequence[str], *, timeout_seconds: float
    ) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(tuple(arguments))
        checkout = Path(arguments[-1])
        checkout.mkdir(parents=True)
        (checkout / ".git").mkdir()
        (checkout / ".git" / "config").write_text("private", encoding="utf-8")
        (checkout / "app.py").write_text("print('safe')\n", encoding="utf-8")
        (checkout / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
        return subprocess.CompletedProcess(arguments, 0, b"", b"")


class SafeRepositoryCloneTests(TestCase):
    def test_cli_requires_explicit_network_acknowledgement(self) -> None:
        with (
            patch("agentforge.cli.load_project_environment"),
            self.assertRaisesRegex(SystemExit, "--allow-network-access"),
        ):
            main(
                [
                    "clone-snapshot",
                    "--url",
                    "https://github.com/owner/repo",
                    "--output",
                    "snapshot",
                    "--allow-host",
                    "github.com",
                ]
            )

    def test_clones_filtered_shallow_snapshot_from_explicit_host(self) -> None:
        runner = FixtureCloneRunner()
        cloner = SafeRepositoryCloner(
            RepositoryClonePolicy(frozenset({"github.com"})), runner=runner
        )
        with TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory) / "snapshot"
            result = cloner.clone_snapshot(
                "https://github.com/example/project.git", destination
            )

            self.assertTrue((destination / "app.py").is_file())
            self.assertFalse((destination / ".git").exists())
            self.assertFalse((destination / ".env").exists())

        arguments = runner.calls[0]
        self.assertIn("--depth", arguments)
        self.assertIn("--single-branch", arguments)
        self.assertIn("--no-tags", arguments)
        self.assertIn("protocol.file.allow=never", arguments)
        self.assertEqual(result.host, "github.com")
        self.assertEqual([entry.path for entry in result.manifest.entries], ["app.py"])

    def test_rejects_lookalike_host_credentials_and_non_https_urls(self) -> None:
        cloner = SafeRepositoryCloner(
            RepositoryClonePolicy(frozenset({"github.com"})),
            runner=FixtureCloneRunner(),
        )
        invalid_urls = (
            "https://github.com.attacker.example/owner/repo",
            "https://user:token@github.com/owner/repo",
            "ssh://github.com/owner/repo",
            "https://github.com:8443/owner/repo",
            "https://github.com/owner/repo?token=secret",
        )
        with TemporaryDirectory() as temporary_directory:
            for index, url in enumerate(invalid_urls):
                with self.subTest(url=url), self.assertRaises(RepositoryCloneError):
                    cloner.clone_snapshot(
                        url, Path(temporary_directory) / f"destination-{index}"
                    )

    def test_rejects_existing_destination_before_network_process(self) -> None:
        runner = FixtureCloneRunner()
        cloner = SafeRepositoryCloner(
            RepositoryClonePolicy(frozenset({"github.com"})), runner=runner
        )
        with TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory) / "existing"
            destination.mkdir()

            with self.assertRaisesRegex(RepositoryCloneError, "must not already exist"):
                cloner.clone_snapshot("https://github.com/owner/repo", destination)

        self.assertEqual(runner.calls, [])

    def test_nonzero_git_result_does_not_expose_process_output(self) -> None:
        class FailedRunner:
            def run(
                self, arguments: Sequence[str], *, timeout_seconds: float
            ) -> subprocess.CompletedProcess[bytes]:
                return subprocess.CompletedProcess(
                    arguments, 128, b"", b"remote included a private token"
                )

        cloner = SafeRepositoryCloner(
            RepositoryClonePolicy(frozenset({"github.com"})), runner=FailedRunner()
        )
        with TemporaryDirectory() as temporary_directory:
            with self.assertRaisesRegex(RepositoryCloneError, "exit code 128") as raised:
                cloner.clone_snapshot(
                    "https://github.com/owner/repo",
                    Path(temporary_directory) / "destination",
                )

        self.assertNotIn("private token", str(raised.exception))
