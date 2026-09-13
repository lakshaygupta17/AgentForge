import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from agentforge.core.models import ActionRequest, SandboxLimits
from agentforge.policy.engine import SecurityPolicy
from agentforge.sandbox.docker import DockerSandbox
from agentforge.sandbox.errors import SandboxUnavailableError


@dataclass
class FakeDockerRunner:
    outcomes: list[subprocess.CompletedProcess[bytes] | BaseException] = field(
        default_factory=list
    )
    calls: list[tuple[str, ...]] = field(default_factory=list)

    def run(
        self, arguments: Sequence[str], *, timeout: float | None = None
    ) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(tuple(arguments))
        if self.outcomes:
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome
        return subprocess.CompletedProcess(arguments, 0, b"", b"")


class DockerSandboxTests(TestCase):
    def test_rejects_an_image_that_could_be_parsed_as_a_docker_option(self) -> None:
        with self.assertRaisesRegex(ValueError, "option prefix"):
            DockerSandbox(image="--privileged", runner=FakeDockerRunner())

    def test_reports_when_the_docker_cli_is_not_installed(self) -> None:
        with patch("agentforge.sandbox.docker.shutil.which", return_value=None):
            with self.assertRaisesRegex(SandboxUnavailableError, "Docker CLI"):
                DockerSandbox()

    def test_maps_limits_and_executes_without_a_shell(self) -> None:
        runner = FakeDockerRunner()
        sandbox = DockerSandbox(
            image="python:test",
            limits=SandboxLimits(cpu_count=1.5, memory_mb=768, process_limit=40),
            docker_executable="docker-test",
            runner=runner,
        )
        try:
            result = sandbox.execute(
                ActionRequest("python", ("-c", "print('ok')")), SecurityPolicy()
            )
        finally:
            sandbox.close()

        self.assertTrue(result.succeeded)
        create_call = runner.calls[1]
        self.assertEqual(create_call[:2], ("docker-test", "create"))
        self.assertIn("--read-only", create_call)
        self.assertEqual(create_call[create_call.index("--cpus") + 1], "1.5")
        self.assertEqual(create_call[create_call.index("--memory") + 1], "768m")
        self.assertEqual(create_call[create_call.index("--pids-limit") + 1], "40")
        self.assertEqual(create_call[create_call.index("--network") + 1], "none")
        exec_call = runner.calls[3]
        self.assertEqual(exec_call[-3:], ("python", "-c", "print('ok')"))
        self.assertNotIn("sh", exec_call)
        self.assertEqual(runner.calls[-1][1:3], ("rm", "--force"))

    def test_rejects_excessive_resources_before_contacting_docker(self) -> None:
        runner = FakeDockerRunner()
        sandbox = DockerSandbox(
            limits=SandboxLimits(memory_mb=4096),
            runner=runner,
        )
        try:
            with self.assertRaisesRegex(PermissionError, "Memory request"):
                sandbox.execute(ActionRequest("python"), SecurityPolicy())
        finally:
            sandbox.close()

        self.assertEqual(runner.calls, [])

    def test_network_access_requires_policy_permission(self) -> None:
        runner = FakeDockerRunner()
        sandbox = DockerSandbox(
            limits=SandboxLimits(network_access=True),
            runner=runner,
        )
        try:
            with self.assertRaisesRegex(PermissionError, "Network access"):
                sandbox.execute(ActionRequest("python"), SecurityPolicy())
        finally:
            sandbox.close()

    def test_timeout_kills_container_and_returns_a_timeout_result(self) -> None:
        timeout = subprocess.TimeoutExpired(
            cmd=["docker", "exec"], timeout=0.1, output=b"partial"
        )
        runner = FakeDockerRunner(
            outcomes=[
                subprocess.CompletedProcess([], 0, b"27.0", b""),
                subprocess.CompletedProcess([], 0, b"container-id", b""),
                subprocess.CompletedProcess([], 0, b"container-name", b""),
                timeout,
                subprocess.CompletedProcess([], 0, b"container-name", b""),
                subprocess.CompletedProcess([], 0, b"container-name", b""),
            ]
        )
        sandbox = DockerSandbox(runner=runner)
        try:
            result = sandbox.execute(
                ActionRequest("python", timeout_seconds=0.1), SecurityPolicy()
            )
        finally:
            sandbox.close()

        self.assertTrue(result.timed_out)
        self.assertEqual(result.stdout, "partial")
        self.assertIn(("docker", "kill", sandbox.container_name), runner.calls)

    def test_imports_before_container_creation_and_exports_to_a_new_workspace(self) -> None:
        runner = FakeDockerRunner()
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "source"
            output = root / "output"
            source.mkdir()
            (source / "program.py").write_text("print('ok')\n", encoding="utf-8")
            sandbox = DockerSandbox(runner=runner)
            try:
                imported = sandbox.import_workspace(source)
                sandbox.execute(ActionRequest("python", ("program.py",)), SecurityPolicy())
                exported = sandbox.export_workspace(output)
            finally:
                sandbox.close()

            self.assertEqual(imported, sandbox.import_manifest)
            self.assertEqual(len(imported.entries), 1)
            self.assertEqual(len(exported.entries), 1)
            self.assertEqual((output / "program.py").read_text(), "print('ok')\n")

    def test_rejects_import_after_container_creation(self) -> None:
        runner = FakeDockerRunner()
        with TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "source"
            source.mkdir()
            sandbox = DockerSandbox(runner=runner)
            try:
                sandbox.execute(ActionRequest("python"), SecurityPolicy())
                with self.assertRaisesRegex(RuntimeError, "after.*container"):
                    sandbox.import_workspace(source)
            finally:
                sandbox.close()
