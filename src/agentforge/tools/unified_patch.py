"""A conservative parser and applier for existing-file unified diffs."""

from __future__ import annotations

import re
from dataclasses import dataclass


class UnifiedPatchError(ValueError):
    """A unified patch is malformed, ambiguous, or unsupported."""


HUNK_HEADER = re.compile(
    r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: .*)?(?:\r?\n)?$"
)


@dataclass(frozen=True, slots=True)
class HunkLine:
    operation: str
    text: str


@dataclass(frozen=True, slots=True)
class UnifiedHunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: tuple[HunkLine, ...]


@dataclass(frozen=True, slots=True)
class UnifiedFilePatch:
    path: str
    hunks: tuple[UnifiedHunk, ...]

    def apply(self, content: str) -> str:
        newline = "\r\n" if "\r\n" in content else "\n"
        final_newline = content.endswith(("\n", "\r"))
        source = content.splitlines()
        output: list[str] = []
        cursor = 0

        for hunk in self.hunks:
            hunk_start = hunk.old_start - 1 if hunk.old_count else hunk.old_start
            if hunk_start < cursor or hunk_start > len(source):
                raise UnifiedPatchError(
                    f"Hunk for {self.path} starts outside the expected source range"
                )
            output.extend(source[cursor:hunk_start])
            cursor = hunk_start
            for line in hunk.lines:
                if line.operation in {" ", "-"}:
                    if cursor >= len(source) or source[cursor] != line.text:
                        raise UnifiedPatchError(
                            f"Hunk context does not match {self.path} at line {cursor + 1}"
                        )
                    if line.operation == " ":
                        output.append(source[cursor])
                    cursor += 1
                elif line.operation == "+":
                    output.append(line.text)
                else:
                    raise UnifiedPatchError("Unsupported hunk operation")

        output.extend(source[cursor:])
        result = newline.join(output)
        if final_newline:
            result += newline
        return result


class UnifiedPatchParser:
    def parse(self, patch_text: str) -> tuple[UnifiedFilePatch, ...]:
        if not patch_text.strip():
            raise UnifiedPatchError("Unified patch cannot be empty")
        if "\x00" in patch_text:
            raise UnifiedPatchError("Unified patch cannot contain null bytes")

        lines = patch_text.splitlines(keepends=True)
        patches: list[UnifiedFilePatch] = []
        index = 0
        while index < len(lines):
            line = lines[index]
            if line.startswith(("diff --git ", "index ")) or not line.strip():
                index += 1
                continue
            if not line.startswith("--- "):
                raise UnifiedPatchError(f"Expected old-file header, found: {line.rstrip()}")
            old_path = self._header_path(line, "--- ")
            index += 1
            if index >= len(lines) or not lines[index].startswith("+++ "):
                raise UnifiedPatchError("Old-file header must be followed by a new-file header")
            new_path = self._header_path(lines[index], "+++ ")
            index += 1
            if old_path == "/dev/null" or new_path == "/dev/null":
                raise UnifiedPatchError("File creation and deletion are not supported yet")
            old_path = self._strip_git_prefix(old_path, "a/")
            new_path = self._strip_git_prefix(new_path, "b/")
            if old_path != new_path:
                raise UnifiedPatchError("File renames are not supported yet")

            hunks: list[UnifiedHunk] = []
            while index < len(lines) and lines[index].startswith("@@ "):
                header = HUNK_HEADER.match(lines[index])
                if header is None:
                    raise UnifiedPatchError(f"Malformed hunk header: {lines[index].rstrip()}")
                old_start = int(header.group(1))
                old_count = int(header.group(2) or "1")
                new_start = int(header.group(3))
                new_count = int(header.group(4) or "1")
                index += 1
                hunk_lines: list[HunkLine] = []
                actual_old = 0
                actual_new = 0
                while index < len(lines) and (
                    actual_old < old_count or actual_new < new_count
                ):
                    body_line = lines[index]
                    if body_line.startswith("\\ No newline at end of file"):
                        raise UnifiedPatchError(
                            "Changing final-newline markers is not supported yet"
                        )
                    if not body_line or body_line[0] not in {" ", "+", "-"}:
                        raise UnifiedPatchError(f"Malformed hunk line: {body_line.rstrip()}")
                    hunk_lines.append(
                        HunkLine(body_line[0], body_line[1:].rstrip("\r\n"))
                    )
                    if body_line[0] in {" ", "-"}:
                        actual_old += 1
                    if body_line[0] in {" ", "+"}:
                        actual_new += 1
                    if actual_old > old_count or actual_new > new_count:
                        raise UnifiedPatchError(
                            "Hunk contains more lines than its declared old/new ranges"
                        )
                    index += 1

                if actual_old != old_count or actual_new != new_count:
                    raise UnifiedPatchError(
                        "Hunk line counts do not match its declared old/new ranges"
                    )
                hunks.append(
                    UnifiedHunk(
                        old_start,
                        old_count,
                        new_start,
                        new_count,
                        tuple(hunk_lines),
                    )
                )
            if not hunks:
                raise UnifiedPatchError(f"File patch for {old_path} has no hunks")
            patches.append(UnifiedFilePatch(old_path, tuple(hunks)))

        if not patches:
            raise UnifiedPatchError("Unified patch contains no file changes")
        paths = [patch.path for patch in patches]
        if len(paths) != len(set(paths)):
            raise UnifiedPatchError("A file may appear only once in a unified patch")
        return tuple(patches)

    def _header_path(self, line: str, prefix: str) -> str:
        value = line[len(prefix) :].rstrip("\r\n").split("\t", 1)[0]
        if not value:
            raise UnifiedPatchError("File header path cannot be empty")
        if value.startswith('"'):
            raise UnifiedPatchError("Quoted Git paths are not supported yet")
        return value

    def _strip_git_prefix(self, path: str, prefix: str) -> str:
        return path[len(prefix) :] if path.startswith(prefix) else path
