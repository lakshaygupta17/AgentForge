"""Validated patch-style edits constrained to an inspected repository."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from difflib import unified_diff
from pathlib import Path
from typing import Any

from agentforge.tools.repository import RepositoryInspector, RepositoryToolError
from agentforge.tools.unified_patch import UnifiedPatchError, UnifiedPatchParser


class RepositoryEditError(RuntimeError):
    """A proposed repository edit was ambiguous or outside allowed boundaries."""


@dataclass(frozen=True, slots=True)
class TextReplacement:
    path: str
    old_text: str
    new_text: str


class RepositoryEditor:
    """Apply exact replacements only after the entire patch validates."""

    def __init__(
        self,
        inspector: RepositoryInspector,
        *,
        maximum_changes_per_patch: int = 20,
    ) -> None:
        if maximum_changes_per_patch <= 0:
            raise ValueError("Maximum changes per patch must be greater than zero")
        self.inspector = inspector
        self.maximum_changes_per_patch = maximum_changes_per_patch
        self.changed_files: set[str] = set()
        self.patch_count = 0
        self._original_contents: dict[str, str] = {}
        self._unified_parser = UnifiedPatchParser()

    def apply_patch(self, arguments: dict[str, Any]) -> dict[str, object]:
        raw_changes = arguments.get("changes")
        if not isinstance(raw_changes, list) or not raw_changes:
            raise RepositoryEditError("Patch needs at least one text replacement")
        if len(raw_changes) > self.maximum_changes_per_patch:
            raise RepositoryEditError(
                f"Patch exceeds the {self.maximum_changes_per_patch}-change limit"
            )

        replacements = tuple(self._parse_change(change) for change in raw_changes)
        staged: dict[str, str] = {}
        for replacement in replacements:
            try:
                relative_path = self.inspector.validate_file_reference(
                    replacement.path, operation="write"
                )
                current = staged.get(relative_path)
                if current is None:
                    current = self.inspector.read_file(relative_path)
            except RepositoryToolError as error:
                raise RepositoryEditError(str(error)) from error

            if relative_path not in self._original_contents:
                self._original_contents[relative_path] = current

            current_for_match, old_text, new_text, newline = (
                self._prepare_exact_replacement(current, replacement)
            )
            occurrences = current_for_match.count(old_text)
            if occurrences != 1:
                raise RepositoryEditError(
                    f"Expected old_text exactly once in {relative_path}; found {occurrences}"
                )
            updated = current_for_match.replace(old_text, new_text, 1)
            if newline not in {None, "\n"}:
                updated = updated.replace("\n", newline)
            if updated == current:
                raise RepositoryEditError(
                    f"Patch does not change {relative_path} after newline normalization"
                )
            if len(updated.encode("utf-8")) > self.inspector.maximum_file_bytes:
                raise RepositoryEditError(
                    f"Edited file exceeds the {self.inspector.maximum_file_bytes}-byte limit"
                )
            staged[relative_path] = updated

        self._commit(staged)
        return {
            "changed_files": sorted(staged),
            "replacement_count": len(replacements),
            "patch_count": self.patch_count,
        }

    def apply_unified_patch(self, patch_text: str) -> dict[str, object]:
        try:
            file_patches = self._unified_parser.parse(patch_text)
        except UnifiedPatchError as error:
            raise RepositoryEditError(str(error)) from error
        if len(file_patches) > self.maximum_changes_per_patch:
            raise RepositoryEditError(
                f"Patch exceeds the {self.maximum_changes_per_patch}-file limit"
            )

        staged: dict[str, str] = {}
        for file_patch in file_patches:
            try:
                relative_path = self.inspector.validate_file_reference(
                    file_patch.path, operation="write"
                )
                current = self.inspector.read_file(relative_path)
                updated = file_patch.apply(current)
            except (RepositoryToolError, UnifiedPatchError) as error:
                raise RepositoryEditError(str(error)) from error
            if current == updated:
                raise RepositoryEditError(f"Unified patch does not change {relative_path}")
            if len(updated.encode("utf-8")) > self.inspector.maximum_file_bytes:
                raise RepositoryEditError(
                    f"Edited file exceeds the {self.inspector.maximum_file_bytes}-byte limit"
                )
            self._original_contents.setdefault(relative_path, current)
            staged[relative_path] = updated

        self._commit(staged)
        return {
            "changed_files": sorted(staged),
            "hunk_count": sum(len(file_patch.hunks) for file_patch in file_patches),
            "patch_count": self.patch_count,
        }

    def unified_diff(self, *, maximum_bytes: int = 200_000) -> str:
        if maximum_bytes <= 0:
            raise ValueError("Maximum diff bytes must be greater than zero")
        sections: list[str] = []
        for relative_path in sorted(self.changed_files):
            original = self._original_contents[relative_path]
            current = self.inspector.read_file(relative_path)
            sections.extend(
                unified_diff(
                    original.splitlines(keepends=True),
                    current.splitlines(keepends=True),
                    fromfile=f"a/{relative_path}",
                    tofile=f"b/{relative_path}",
                )
            )
        result = "".join(sections)
        if len(result.encode("utf-8")) > maximum_bytes:
            raise RepositoryEditError(
                f"Final diff exceeds the {maximum_bytes}-byte review limit"
            )
        return result

    def _parse_change(self, value: object) -> TextReplacement:
        if not isinstance(value, dict):
            raise RepositoryEditError("Each patch change must be an object")
        path = value.get("path")
        old_text = value.get("old_text")
        new_text = value.get("new_text")
        if not isinstance(path, str) or not path.strip():
            raise RepositoryEditError("Patch path must be non-empty text")
        if not isinstance(old_text, str) or not old_text:
            raise RepositoryEditError("old_text must be non-empty exact text")
        if not isinstance(new_text, str):
            raise RepositoryEditError("new_text must be text")
        if old_text == new_text:
            raise RepositoryEditError("A replacement must change the file")
        return TextReplacement(path.strip(), old_text, new_text)

    def _prepare_exact_replacement(
        self, current: str, replacement: TextReplacement
    ) -> tuple[str, str, str, str | None]:
        """Normalize uniform line endings for matching without changing file style."""

        without_crlf = current.replace("\r\n", "")
        has_crlf = "\r\n" in current
        has_lf = "\n" in without_crlf
        has_cr = "\r" in without_crlf
        if sum((has_crlf, has_lf, has_cr)) > 1:
            return current, replacement.old_text, replacement.new_text, None

        newline = "\r\n" if has_crlf else "\r" if has_cr else "\n"
        return (
            self._normalize_newlines(current),
            self._normalize_newlines(replacement.old_text),
            self._normalize_newlines(replacement.new_text),
            newline,
        )

    @staticmethod
    def _normalize_newlines(value: str) -> str:
        return value.replace("\r\n", "\n").replace("\r", "\n")

    def _commit(self, staged: dict[str, str]) -> None:
        for relative_path, content in staged.items():
            self._atomic_write(self.inspector.root / relative_path, content)
            if content == self._original_contents[relative_path]:
                self.changed_files.discard(relative_path)
            else:
                self.changed_files.add(relative_path)
        self.patch_count += 1

    def _atomic_write(self, path: Path, content: str) -> None:
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="",
                dir=path.parent,
                prefix=f".{path.name}.agentforge-",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_file.write(content)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
                temporary_name = temporary_file.name
            os.replace(temporary_name, path)
        except OSError as error:
            if temporary_name is not None:
                try:
                    Path(temporary_name).unlink(missing_ok=True)
                except OSError:
                    pass
            raise RepositoryEditError(f"Could not write {path.name}: {error}") from error
