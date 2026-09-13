from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from agentforge.policy.config import PolicyConfigurationError, load_security_policy


class PolicyConfigurationTests(TestCase):
    def _write(self, content: str) -> Path:
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "policy.yaml"
        path.write_text(content, encoding="utf-8")
        return path

    def test_loads_all_supported_policy_sections(self) -> None:
        path = self._write(
            """
version: 1
commands:
  maximum_timeout_seconds: 30
  forbidden_executables: [sh, sudo]
  allowed_environment_keys: [CI]
  allowed_git_subcommands: [diff]
resources:
  maximum_cpu_count: 1.5
  maximum_memory_mb: 768
  maximum_processes: 32
  maximum_output_bytes: 4096
network:
  allow: false
  allowed_destinations: [pypi.org]
filesystem:
  readable_paths: ["src/*"]
  writable_paths: ["src/*"]
  denied_paths: ["**/.env"]
""".strip()
        )

        policy = load_security_policy(path)

        self.assertEqual(policy.maximum_timeout_seconds, 30)
        self.assertEqual(policy.maximum_cpu_count, 1.5)
        self.assertEqual(policy.maximum_memory_mb, 768)
        self.assertEqual(policy.maximum_processes, 32)
        self.assertEqual(policy.maximum_output_bytes, 4096)
        self.assertEqual(policy.allowed_network_destinations, frozenset({"pypi.org"}))
        self.assertEqual(policy.readable_paths, ("src/*",))

    def test_rejects_unknown_fields_instead_of_ignoring_typos(self) -> None:
        path = self._write("version: 1\nresources:\n  maximum_memry_mb: 512\n")

        with self.assertRaisesRegex(PolicyConfigurationError, "maximum_memry_mb"):
            load_security_policy(path)

    def test_rejects_wrong_types_and_unsafe_yaml_tags(self) -> None:
        wrong_type = self._write("version: 1\nnetwork:\n  allow: yes-please\n")
        unsafe_tag = self._write("!!python/object/apply:os.system ['echo unsafe']\n")

        with self.assertRaisesRegex(PolicyConfigurationError, "true or false"):
            load_security_policy(wrong_type)
        with self.assertRaises(PolicyConfigurationError):
            load_security_policy(unsafe_tag)

    def test_requires_supported_schema_version(self) -> None:
        path = self._write("version: 2\n")

        with self.assertRaisesRegex(PolicyConfigurationError, "version must be 1"):
            load_security_policy(path)
