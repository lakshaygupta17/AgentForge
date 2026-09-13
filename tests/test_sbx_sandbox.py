import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from agentforge.core.models import ActionRequest, SandboxLimits
from agentforge.policy.engine import SecurityPolicy
from agentforge.sandbox.errors import SandboxLifecycleError, SandboxUnavailableError
from agentforge.sandbox.sbx import SbxSandbox


@dataclass
class FakeSbxRunner:
    outcomes: list[subprocess.CompletedProcess[bytes] | BaseException] = field(
        default_factory=list
    )
    calls: list[tuple[str, ...]] = field(default_factory=list)
    downloaded_files: dict[str, str] = field(default_factory=dict)
    uploaded_text: list[str] = field(default_factory=list)

    def run(
        self, arguments: Sequence[str], *, timeout: float | None = None
    ) -> subprocess.CompletedProcess[bytes]:
        call = tuple(arguments)
        self.calls.append(call)
        if len(call) >= 4 and call[1] == "cp" and call[2].startswith("agentforge-"):
            pulled_workspace = Path(call[3]) / "workspace"
            pulled_workspace.mkdir(parents=True, exist_ok=True)
            for relative_path, content in self.downloaded_files.items():
                output = pulled_workspace / relative_path
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(content, encoding="utf-8")
        elif len(call) >= 4 and call[1] == "cp":
            uploaded = Path(call[2])
            if uploaded.is_file():
                self.uploaded_text.append(uploaded.read_text(encoding="utf-8"))
        if self.outcomes:
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome
        return subprocess.CompletedProcess(arguments, 0, b"", b"")


class SbxSandboxTests(TestCase):
    def test_reports_when_sbx_is_not_installed(self) -> None:
        with patch("agentforge.sandbox.sbx._resolve_sbx_executable", return_value=None):
            with self.assertRaisesRegex(SandboxUnavailableError, "sbx"):
                SbxSandbox()

    def test_creates_a_mountless_network_denied_microvm_and_executes_without_shell(
        self,
    ) -> None:
        runner = FakeSbxRunner()
        with TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "source"
            source.mkdir()
            (source / "program.py").write_text("print('ok')\n", encoding="utf-8")
            sandbox = SbxSandbox(
                limits=SandboxLimits(cpu_count=1.5, memory_mb=768),
                sbx_executable="sbx-test",
                runner=runner,
            )
            try:
                sandbox.import_workspace(source)
                result = sandbox.execute(
                    ActionRequest("python", ("program.py",)), SecurityPolicy()
                )
            finally:
                sandbox.close()

        self.assertTrue(result.succeeded)
        create_call = runner.calls[1]
        self.assertEqual(create_call[:2], ("sbx-test", "create"))
        self.assertEqual(create_call[create_call.index("--cpus") + 1], "2")
        self.assertEqual(create_call[create_call.index("--memory") + 1], "768m")
        self.assertEqual(create_call[create_call.index("--deny-network") + 1], "**")
        self.assertEqual(create_call[-1], "shell")
        self.assertNotIn(str(source), create_call)
        upload_call = next(call for call in runner.calls if call[1] == "cp")
        self.assertTrue(upload_call[2].endswith("program.py"))
        action_call = next(
            call for call in runner.calls if call[-2:] == ("python", "program.py")
        )
        self.assertNotIn("bash", action_call)
        self.assertNotIn("sh", action_call)
        self.assertEqual(runner.calls[-1][1:3], ("rm", "--force"))

    def test_rejects_resources_before_creating_a_microvm(self) -> None:
        runner = FakeSbxRunner()
        sandbox = SbxSandbox(
            limits=SandboxLimits(memory_mb=4096),
            runner=runner,
        )
        try:
            with self.assertRaisesRegex(PermissionError, "Memory request"):
                sandbox.start(SecurityPolicy())
        finally:
            sandbox.close()

        self.assertEqual(runner.calls, [])

    def test_refreshes_agent_edits_before_each_execution(self) -> None:
        runner = FakeSbxRunner()
        with TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "source"
            source.mkdir()
            (source / "program.py").write_text("version one\n", encoding="utf-8")
            sandbox = SbxSandbox(runner=runner)
            try:
                sandbox.import_workspace(source)
                sandbox.start(SecurityPolicy())
                (sandbox.workspace / "program.py").write_text(
                    "version two\n", encoding="utf-8"
                )
                sandbox.execute(ActionRequest("python", ("program.py",)), SecurityPolicy())
            finally:
                sandbox.close()

        self.assertEqual(runner.uploaded_text, ["version one\n", "version two\n"])

    def test_timeout_stops_the_whole_microvm(self) -> None:
        runner = FakeSbxRunner(
            outcomes=[
                subprocess.CompletedProcess([], 0, b"v0.42.1", b""),
                subprocess.CompletedProcess([], 0, b"", b""),
                subprocess.CompletedProcess([], 0, b"", b""),
                subprocess.CompletedProcess([], 0, b"", b""),
                subprocess.TimeoutExpired(
                    cmd=["sbx", "exec"], timeout=0.1, output=b"partial"
                ),
                subprocess.CompletedProcess([], 0, b"", b""),
                subprocess.CompletedProcess([], 0, b"", b""),
            ]
        )
        sandbox = SbxSandbox(runner=runner)
        try:
            result = sandbox.execute(
                ActionRequest("python", timeout_seconds=0.1), SecurityPolicy()
            )
        finally:
            sandbox.close()

        self.assertTrue(result.timed_out)
        self.assertEqual(result.stdout, "partial")
        self.assertIn(("sbx", "stop", sandbox.sandbox_name), runner.calls)

    def test_exports_downloaded_workspace_to_a_new_destination(self) -> None:
        runner = FakeSbxRunner(downloaded_files={"program.py": "print('repaired')\n"})
        with TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "output"
            sandbox = SbxSandbox(runner=runner)
            try:
                exported = sandbox.export_workspace(output)
            finally:
                sandbox.close()

            self.assertEqual(len(exported.entries), 1)
            self.assertEqual(
                (output / "program.py").read_text(encoding="utf-8"),
                "print('repaired')\n",
            )

    def test_rejects_import_after_microvm_creation(self) -> None:
        runner = FakeSbxRunner()
        with TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "source"
            source.mkdir()
            sandbox = SbxSandbox(runner=runner)
            try:
                sandbox.start(SecurityPolicy())
                with self.assertRaisesRegex(SandboxLifecycleError, "after.*microVM"):
                    sandbox.import_workspace(source)
            finally:
                sandbox.close()
