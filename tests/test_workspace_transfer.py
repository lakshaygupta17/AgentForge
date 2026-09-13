import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from agentforge.sandbox.workspace import (
    WorkspaceTransfer,
    WorkspaceTransferError,
    WorkspaceTransferPolicy,
)


class WorkspaceTransferTests(TestCase):
    def test_copies_a_bounded_snapshot_and_excludes_credentials_and_build_state(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "source"
            destination = root / "destination"
            (source / "src").mkdir(parents=True)
            (source / ".git").mkdir()
            (source / ".venv").mkdir()
            (source / "src" / "app.py").write_text("print('safe')\n", encoding="utf-8")
            binary = b"\x00\x01\x02"
            (source / "asset.bin").write_bytes(binary)
            expected_total = (source / "src" / "app.py").stat().st_size + len(binary)
            (source / ".env").write_text("TOKEN=secret", encoding="utf-8")
            (source / "private.pem").write_text("secret", encoding="utf-8")
            (source / ".git" / "config").write_text("git", encoding="utf-8")
            (source / ".venv" / "installed.txt").write_text("venv", encoding="utf-8")

            manifest = WorkspaceTransfer().copy(source, destination)

            self.assertEqual((destination / "src" / "app.py").read_text(), "print('safe')\n")
            self.assertEqual((destination / "asset.bin").read_bytes(), binary)
            self.assertFalse((destination / ".env").exists())
            self.assertFalse((destination / "private.pem").exists())
            self.assertFalse((destination / ".git").exists())
            self.assertFalse((destination / ".venv").exists())
            self.assertEqual(
                {entry.path for entry in manifest.entries}, {"asset.bin", "src/app.py"}
            )
            binary_entry = next(
                entry for entry in manifest.entries if entry.path == "asset.bin"
            )
            self.assertEqual(binary_entry.sha256, hashlib.sha256(binary).hexdigest())
            self.assertEqual(manifest.total_bytes, expected_total)
            self.assertEqual(set(manifest.skipped_paths), {".env", "private.pem"})

    def test_validates_size_before_creating_the_destination(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "source"
            destination = root / "destination"
            source.mkdir()
            (source / "large.txt").write_text("12345", encoding="utf-8")
            transfer = WorkspaceTransfer(WorkspaceTransferPolicy(maximum_file_bytes=4))

            with self.assertRaisesRegex(WorkspaceTransferError, "exceeds"):
                transfer.copy(source, destination)

            self.assertFalse(destination.exists())

    def test_refuses_to_overwrite_a_nonempty_destination(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "source"
            destination = root / "destination"
            source.mkdir()
            destination.mkdir()
            (source / "new.txt").write_text("new", encoding="utf-8")
            existing = destination / "existing.txt"
            existing.write_text("keep", encoding="utf-8")

            with self.assertRaisesRegex(WorkspaceTransferError, "absent or empty"):
                WorkspaceTransfer().copy(source, destination)

            self.assertEqual(existing.read_text(encoding="utf-8"), "keep")

    def test_refuses_overlapping_source_and_destination(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "source"
            source.mkdir()

            with self.assertRaisesRegex(WorkspaceTransferError, "must not overlap"):
                WorkspaceTransfer().copy(source, source / "nested")
