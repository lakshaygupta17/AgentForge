from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from unittest import TestCase

from agentforge.core.models import ActionRequest
from agentforge.evaluation.swebench_e2b import (
    RemoteRepositoryEditor,
    RemoteRepositoryInspector,
    SweBenchE2BSandbox,
    is_retryable_generation_failure,
    swebench_instance_image,
    swebench_template_name,
)
from agentforge.policy.engine import SecurityPolicy
from agentforge.sandbox.e2b import E2BCommandOutcome


class FakeClient:
    def __init__(self) -> None:
        self.files = {
            "/testbed/example.py": b"def answer():\n    return 41\n",
            "/testbed/tests/test_example.py": b"from example import answer\n",
            "/testbed/.env": b"SECRET=never-read\n",
            "/testbed/.git/config": b"ignored\n",
        }
        self.commands: list[tuple[str, str]] = []
        self.killed = False

    def run(
        self,
        command: str,
        *,
        cwd: str,
        environment: dict[str, str],
        timeout_seconds: float,
    ) -> E2BCommandOutcome:
        self.commands.append((command, cwd))
        if command.startswith("grep "):
            return E2BCommandOutcome(0, "example.py:2:    return 41\n", "")
        if " diff " in command:
            return E2BCommandOutcome(0, "diff --git a/example.py b/example.py\n", "")
        return E2BCommandOutcome(0, "2 passed\n", "")

    def reset_directory(self, path: str) -> None:
        return None

    def write_file(self, path: str, data: bytes) -> None:
        self.files[path] = data

    def list_files(self, path: str) -> tuple[str, ...]:
        root = PurePosixPath(path)
        return tuple(
            name for name in self.files if PurePosixPath(name).is_relative_to(root)
        )

    def read_file(self, path: str) -> bytes:
        return self.files[path]

    def kill(self) -> None:
        self.killed = True


class FakeFactory:
    def __init__(self, client: FakeClient) -> None:
        self.client = client
        self.template: str | None = None

    def create(
        self,
        *,
        timeout_seconds: float,
        allow_internet_access: bool,
        template: str | None,
    ) -> FakeClient:
        self.template = template
        return self.client


class SweBenchE2BTests(TestCase):
    def test_only_infrastructure_and_provider_failures_are_retryable(self) -> None:
        self.assertTrue(
            is_retryable_generation_failure(
                "repository_explorer raised ProviderResponseError: Provider returned HTTP 402"
            )
        )
        self.assertTrue(
            is_retryable_generation_failure(
                "File exceeds the 100000-byte inspection limit: large.py"
            )
        )
        self.assertFalse(is_retryable_generation_failure("Tests still failed after 3 attempts"))

    def test_maps_instance_to_official_image_and_safe_template_name(self) -> None:
        instance_id = "django__django-11099"

        self.assertEqual(swebench_template_name(instance_id), "swebench-django-django-11099")
        self.assertEqual(
            swebench_instance_image(instance_id),
            "swebench/sweb.eval.x86_64.django_1776_django-11099:latest",
        )

    def test_remote_inspection_and_validated_edit_use_testbed(self) -> None:
        client = FakeClient()
        policy = SecurityPolicy()
        with TemporaryDirectory() as directory:
            inspector = RemoteRepositoryInspector(client, Path(directory), policy=policy)
            editor = RemoteRepositoryEditor(inspector)

            self.assertEqual(
                inspector.list_files(), ["example.py", "tests/test_example.py"]
            )
            self.assertEqual(inspector.search_code("return")[0]["path"], "example.py")
            editor.apply_patch(
                {
                    "changes": [
                        {
                            "path": "example.py",
                            "old_text": "return 41",
                            "new_text": "return 42",
                        }
                    ]
                }
            )

        self.assertIn(b"return 42", client.files["/testbed/example.py"])
        self.assertIn("return 42", editor.unified_diff())

    def test_sandbox_runs_in_testbed_and_closes_client(self) -> None:
        client = FakeClient()
        factory = FakeFactory(client)
        policy = SecurityPolicy(
            maximum_cpu_count=8,
            maximum_memory_mb=8192,
            maximum_timeout_seconds=600,
        )

        with SweBenchE2BSandbox("django__django-11099", factory=factory) as sandbox:
            sandbox.start(policy)
            result = sandbox.execute(
                ActionRequest("pytest", ("-q",), timeout_seconds=120), policy
            )
            patch = sandbox.capture_patch()

        self.assertTrue(result.succeeded)
        self.assertEqual(factory.template, "swebench-django-django-11099")
        self.assertTrue(any(cwd == "/testbed" for _, cwd in client.commands))
        self.assertIn("diff --git", patch)
        self.assertTrue(client.killed)

    def test_remote_paths_cannot_escape_testbed(self) -> None:
        client = FakeClient()
        with TemporaryDirectory() as directory:
            inspector = RemoteRepositoryInspector(
                client, Path(directory), policy=SecurityPolicy()
            )

            with self.assertRaisesRegex(Exception, "escapes"):
                inspector.read_file("../secret.txt")
