"""Read-only, boundary-checked repository inspection tools."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from agentforge.policy.engine import FileAccessRequest, FileOperation, SecurityPolicy


class RepositoryToolError(RuntimeError):
    """A repository tool request was invalid or exceeded a safety boundary."""


class RepositoryInspector:
    DEFAULT_IGNORED_NAMES = frozenset(
        {
            ".git",
            ".mypy_cache",
            ".pytest_cache",
            ".ruff_cache",
            ".venv",
            "__pycache__",
            "build",
            "dist",
            "node_modules",
        }
    )
    DEFAULT_SENSITIVE_FILES = frozenset(
        {
            ".env",
            ".env.development",
            ".env.local",
            ".env.production",
            ".netrc",
            ".npmrc",
            ".pypirc",
            "credentials.json",
            "id_ed25519",
            "id_rsa",
            "service-account.json",
        }
    )
    DEFAULT_SENSITIVE_SUFFIXES = frozenset({".key", ".p12", ".pem"})

    def __init__(
        self,
        root: Path,
        *,
        maximum_file_bytes: int = 100_000,
        maximum_files: int = 2_000,
        maximum_search_results: int = 50,
        policy: SecurityPolicy | None = None,
    ) -> None:
        self.root = root.resolve()
        if not self.root.is_dir():
            raise ValueError("Repository root must be an existing directory")
        if min(maximum_file_bytes, maximum_files, maximum_search_results) <= 0:
            raise ValueError("Repository inspection limits must be greater than zero")
        self.maximum_file_bytes = maximum_file_bytes
        self.maximum_files = maximum_files
        self.maximum_search_results = maximum_search_results
        self.policy = policy or SecurityPolicy()

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> object:
        if tool_name == "list_files":
            return self.list_files(self._text(arguments, "path", default="."))
        if tool_name == "read_file":
            return self.read_file(self._text(arguments, "path"))
        if tool_name == "search_code":
            return self.search_code(
                self._text(arguments, "query"),
                self._text(arguments, "path", default="."),
            )
        raise RepositoryToolError(f"Unknown repository tool '{tool_name}'")

    def list_files(self, path: str = ".") -> list[str]:
        location = self._resolve(path)
        if not location.is_dir():
            raise RepositoryToolError(f"Not a directory: {path}")
        self._assert_visible(location)

        files: list[str] = []
        ignored_names = {name.casefold() for name in self.DEFAULT_IGNORED_NAMES}
        for current_directory, directories, filenames in os.walk(
            location, followlinks=False
        ):
            directories[:] = [
                name for name in directories if name.casefold() not in ignored_names
            ]
            current_path = Path(current_directory)
            for filename in filenames:
                candidate = current_path / filename
                if self._is_sensitive_file(candidate):
                    continue
                if candidate.is_file() and self._policy_allows(candidate, "read"):
                    relative = candidate.relative_to(self.root)
                    files.append(relative.as_posix())
                if len(files) >= self.maximum_files:
                    return sorted(files)
        return sorted(files)

    def read_file(self, path: str) -> str:
        location = self._resolve_file(path)
        self._assert_policy_access(location, "read")
        size = location.stat().st_size
        if size > self.maximum_file_bytes:
            raise RepositoryToolError(
                f"File exceeds the {self.maximum_file_bytes}-byte inspection limit: {path}"
            )
        content = location.read_bytes()
        if b"\x00" in content:
            raise RepositoryToolError(f"Binary files cannot be read as text: {path}")
        return content.decode("utf-8", errors="replace")

    def search_code(self, query: str, path: str = ".") -> list[dict[str, object]]:
        if not query.strip():
            raise RepositoryToolError("Search query cannot be empty")

        results: list[dict[str, object]] = []
        for relative_path in self.list_files(path):
            try:
                content = self.read_file(relative_path)
            except RepositoryToolError:
                continue
            for line_number, line in enumerate(content.splitlines(), start=1):
                if query.casefold() in line.casefold():
                    results.append(
                        {
                            "path": relative_path,
                            "line": line_number,
                            "preview": line.strip()[:300],
                        }
                    )
                    if len(results) >= self.maximum_search_results:
                        return results
        return results

    def validate_file_reference(
        self, path: str, *, operation: FileOperation = "read"
    ) -> str:
        location = self._resolve_file(path)
        self._assert_policy_access(location, operation)
        return location.relative_to(self.root).as_posix()

    def _resolve(self, path: str) -> Path:
        if not path.strip():
            raise RepositoryToolError("Path cannot be empty")
        candidate = (self.root / path).resolve()
        if not candidate.is_relative_to(self.root):
            raise RepositoryToolError("Path escapes the repository workspace")
        return candidate

    def _resolve_file(self, path: str) -> Path:
        candidate = self._resolve(path)
        self._assert_visible(candidate)
        if not candidate.is_file():
            raise RepositoryToolError(f"Not a file: {path}")
        return candidate

    def _assert_visible(self, candidate: Path) -> None:
        relative = candidate.relative_to(self.root)
        ignored_names = {name.casefold() for name in self.DEFAULT_IGNORED_NAMES}
        if any(part.casefold() in ignored_names for part in relative.parts):
            raise RepositoryToolError("Path belongs to an ignored repository directory")
        if self._is_sensitive_file(candidate):
            raise RepositoryToolError("Sensitive credential files cannot be inspected")

    def _is_sensitive_file(self, candidate: Path) -> bool:
        name = candidate.name.casefold()
        sensitive_names = {value.casefold() for value in self.DEFAULT_SENSITIVE_FILES}
        sensitive_suffixes = {
            value.casefold() for value in self.DEFAULT_SENSITIVE_SUFFIXES
        }
        return name in sensitive_names or candidate.suffix.casefold() in sensitive_suffixes

    def _assert_policy_access(
        self, candidate: Path, operation: FileOperation
    ) -> None:
        decision = self.policy.evaluate_file_access(
            FileAccessRequest(candidate.relative_to(self.root), operation), self.root
        )
        if not decision.allowed:
            raise RepositoryToolError("; ".join(decision.reasons))

    def _policy_allows(self, candidate: Path, operation: FileOperation) -> bool:
        return self.policy.evaluate_file_access(
            FileAccessRequest(candidate.relative_to(self.root), operation), self.root
        ).allowed

    def _text(self, arguments: dict[str, Any], key: str, *, default: str | None = None) -> str:
        value = arguments.get(key, default)
        if not isinstance(value, str):
            raise RepositoryToolError(f"'{key}' must be text")
        return value
