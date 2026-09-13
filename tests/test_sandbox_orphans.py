import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from agentforge.policy.engine import SecurityPolicy
from agentforge.sandbox.errors import SandboxLifecycleError
from agentforge.sandbox.orphans import OrphanRegistry, reap_sbx_orphans
from agentforge.sandbox.sbx import SbxSandbox


@dataclass
class FakeRunner:
    return_codes: list[int] = field(default_factory=list)
    calls: list[tuple[str, ...]] = field(default_factory=list)

    def run(
        self, arguments: Sequence[str], *, timeout: float | None = None
    ) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(tuple(arguments))
        code = self.return_codes.pop(0) if self.return_codes else 0
        return subprocess.CompletedProcess(arguments, code, b"", b"failed")


class OrphanRegistryTests(TestCase):
    def test_sbx_lifecycle_registers_then_forgets_the_owned_resource(self) -> None:
        with TemporaryDirectory() as directory:
            registry = OrphanRegistry(Path(directory) / "orphans.json")
            sandbox = SbxSandbox(runner=FakeRunner(), orphan_registry=registry)
            sandbox.start(SecurityPolicy())
            self.assertEqual(registry.load()[0].resource_id, sandbox.sandbox_name)
            sandbox.close()
            self.assertEqual(registry.load(), ())

    def test_failed_sbx_cleanup_leaves_the_exact_record_for_recovery(self) -> None:
        runner = FakeRunner()
        runner.return_codes = [0, 0, 0, 0, 1]
        with TemporaryDirectory() as directory:
            registry = OrphanRegistry(Path(directory) / "orphans.json")
            sandbox = SbxSandbox(runner=runner, orphan_registry=registry)
            sandbox.start(SecurityPolicy())
            with self.assertRaisesRegex(SandboxLifecycleError, "remove"):
                sandbox.close()
            self.assertEqual(registry.load()[0].resource_id, sandbox.sandbox_name)

    def test_registers_and_forgets_exact_owned_sandbox(self) -> None:
        with TemporaryDirectory() as directory:
            registry = OrphanRegistry(Path(directory) / "orphans.json")
            registry.register_sbx("agentforge-012345abcdef")
            self.assertEqual(registry.load()[0].resource_id, "agentforge-012345abcdef")
            registry.forget("sbx", "agentforge-012345abcdef")
            self.assertEqual(registry.load(), ())

    def test_rejects_wildcards_and_unrecognized_names(self) -> None:
        with TemporaryDirectory() as directory:
            registry = OrphanRegistry(Path(directory) / "orphans.json")
            for resource_id in ("*", "agentforge-*", "somebody-elses-vm"):
                with self.subTest(resource_id=resource_id):
                    with self.assertRaisesRegex(SandboxLifecycleError, "unrecognized"):
                        registry.register_sbx(resource_id)

    def test_reaper_removes_only_exact_records_and_retains_failures(self) -> None:
        with TemporaryDirectory() as directory:
            registry = OrphanRegistry(Path(directory) / "orphans.json")
            registry.register_sbx("agentforge-012345abcdef")
            registry.register_sbx("agentforge-fedcba654321")
            runner = FakeRunner(return_codes=[0, 1])

            result = reap_sbx_orphans(registry, executable="sbx", runner=runner)

            self.assertEqual(result.removed, ("agentforge-012345abcdef",))
            self.assertEqual(result.retained, ("agentforge-fedcba654321",))
            self.assertEqual(
                runner.calls,
                [
                    ("sbx", "rm", "--force", "agentforge-012345abcdef"),
                    ("sbx", "rm", "--force", "agentforge-fedcba654321"),
                ],
            )
            self.assertEqual(
                tuple(record.resource_id for record in registry.load()),
                ("agentforge-fedcba654321",),
            )
