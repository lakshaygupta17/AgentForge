from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from agentforge.core.models import ActionRequest, SandboxLimits
from agentforge.policy.engine import SecurityPolicy
from agentforge.sandbox.e2b import (
    E2B_WORKSPACE,
    E2BClient,
    E2BCommandOutcome,
    E2BCommandTimeout,
    E2BSandbox,
)
from agentforge.sandbox.errors import SandboxLifecycleError


@dataclass
class FakeE2BClient:
    outcome: E2BCommandOutcome | BaseException = field(
        default_factory=lambda: E2BCommandOutcome(0, "ok\n", "")
    )
    files: dict[str, bytes] = field(default_factory=dict)
    calls: list[tuple[object, ...]] = field(default_factory=list)
    kill_count: int = 0

    def run(
        self,
        command: str,
        *,
        cwd: str,
        environment: Mapping[str, str],
        timeout_seconds: float,
    ) -> E2BCommandOutcome:
        self.calls.append(("run", command, cwd, dict(environment), timeout_seconds))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome

    def reset_directory(self, path: str) -> None:
        self.calls.append(("reset", path))
        prefix = path.rstrip("/") + "/"
        self.files = {
            name: value
            for name, value in self.files.items()
            if name != path and not name.startswith(prefix)
        }

    def write_file(self, path: str, data: bytes) -> None:
        self.calls.append(("write", path, data))
        self.files[path] = data

    def list_files(self, path: str) -> Sequence[str]:
        self.calls.append(("list", path))
        prefix = path.rstrip("/") + "/"
        return tuple(name for name in sorted(self.files) if name.startswith(prefix))

    def read_file(self, path: str) -> bytes:
        self.calls.append(("read", path))
        return self.files[path]

    def kill(self) -> None:
        self.calls.append(("kill",))
        self.kill_count += 1


@dataclass
class FakeE2BFactory:
    client: FakeE2BClient = field(default_factory=FakeE2BClient)
    calls: list[tuple[float, bool, str | None]] = field(default_factory=list)

    def create(
        self,
        *,
        timeout_seconds: float,
        allow_internet_access: bool,
        template: str | None,
    ) -> E2BClient:
        self.calls.append((timeout_seconds, allow_internet_access, template))
        return self.client


class E2BSandboxTests(TestCase):
    def test_rejects_sessions_beyond_hobby_plan_limit(self) -> None:
        with self.assertRaisesRegex(ValueError, "Hobby one-hour"):
            E2BSandbox(sandbox_timeout_seconds=3_601)

    def test_creates_network_denied_microvm_and_uploads_filtered_files(self) -> None:
        factory = FakeE2BFactory()
        with TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "source"
            source.mkdir()
            (source / "program.py").write_text("print('ok')\n", encoding="utf-8")
            (source / ".env").write_text("SECRET=hidden\n", encoding="utf-8")
            sandbox = E2BSandbox(factory=factory, template="agentforge-test")
            try:
                manifest = sandbox.import_workspace(source)
                sandbox.start(SecurityPolicy())
            finally:
                sandbox.close()

        self.assertEqual(factory.calls, [(300, False, "agentforge-test")])
        self.assertEqual([entry.path for entry in manifest.entries], ["program.py"])
        self.assertIn(".env", manifest.skipped_paths)
        self.assertEqual(
            factory.client.files[f"{E2B_WORKSPACE}/program.py"].decode().splitlines(),
            ["print('ok')"],
        )
        self.assertEqual(factory.client.kill_count, 1)

    def test_executes_structured_arguments_with_posix_quoting_and_separate_env(self) -> None:
        factory = FakeE2BFactory()
        sandbox = E2BSandbox(factory=factory)
        try:
            result = sandbox.execute(
                ActionRequest(
                    "python",
                    ("script.py", "$(touch host)", "two words"),
                    working_directory=Path("src"),
                    timeout_seconds=7,
                    environment={"MODE": "test"},
                ),
                SecurityPolicy(allowed_environment_keys=frozenset({"MODE"})),
            )
        finally:
            sandbox.close()

        self.assertTrue(result.succeeded)
        run_call = next(call for call in factory.client.calls if call[0] == "run")
        self.assertEqual(
            run_call,
            (
                "run",
                "python script.py '$(touch host)' 'two words'",
                f"{E2B_WORKSPACE}/src",
                {"MODE": "test"},
                7,
            ),
        )

    def test_rejects_resources_before_creating_microvm(self) -> None:
        factory = FakeE2BFactory()
        sandbox = E2BSandbox(
            limits=SandboxLimits(memory_mb=4096),
            factory=factory,
        )
        try:
            with self.assertRaisesRegex(PermissionError, "Memory request"):
                sandbox.start(SecurityPolicy())
        finally:
            sandbox.close()
        self.assertEqual(factory.calls, [])

    def test_timeout_kills_microvm_and_returns_timeout_result(self) -> None:
        factory = FakeE2BFactory(client=FakeE2BClient(outcome=E2BCommandTimeout()))
        sandbox = E2BSandbox(factory=factory)
        result = sandbox.execute(
            ActionRequest("python", timeout_seconds=0.1), SecurityPolicy()
        )
        sandbox.close()

        self.assertTrue(result.timed_out)
        self.assertIsNone(result.exit_code)
        self.assertEqual(factory.client.kill_count, 1)

    def test_exports_remote_files_through_transfer_filter(self) -> None:
        remote = f"{E2B_WORKSPACE}/package/program.py"
        factory = FakeE2BFactory(client=FakeE2BClient(files={remote: b"repaired\n"}))
        with TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory) / "output"
            sandbox = E2BSandbox(factory=factory)
            sandbox.start(SecurityPolicy())
            factory.client.files[remote] = b"repaired\n"
            try:
                manifest = sandbox.export_workspace(destination)
            finally:
                sandbox.close()

            self.assertEqual([entry.path for entry in manifest.entries], ["package/program.py"])
            self.assertEqual((destination / "package" / "program.py").read_bytes(), b"repaired\n")

    def test_rejects_remote_paths_outside_workspace(self) -> None:
        factory = FakeE2BFactory(
            client=FakeE2BClient(files={"/home/user/stolen.txt": b"no"})
        )
        sandbox = E2BSandbox(factory=factory)
        sandbox.start(SecurityPolicy())
        factory.client.list_files = lambda path: ("/home/user/stolen.txt",)  # type: ignore[method-assign]
        try:
            with TemporaryDirectory() as temporary_directory:
                with self.assertRaisesRegex(SandboxLifecycleError, "outside"):
                    sandbox.export_workspace(Path(temporary_directory) / "output")
        finally:
            sandbox.close()
