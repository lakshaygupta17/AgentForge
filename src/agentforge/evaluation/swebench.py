"""Strict loader for small, user-supplied SWE-bench-compatible subsets."""

from __future__ import annotations

import json
import re
from collections import defaultdict, deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any

_COMMIT_PATTERN = re.compile(r"^[0-9a-fA-F]{7,64}$")


class SweBenchSubsetError(ValueError):
    """A local SWE-bench subset is absent, malformed, or too large."""


@dataclass(frozen=True, slots=True)
class SweBenchCase:
    instance_id: str
    repository: str
    base_commit: str
    problem_statement: str
    fail_to_pass: tuple[str, ...]
    pass_to_pass: tuple[str, ...]


def load_swebench_subset(
    path: Path,
    *,
    maximum_cases: int = 500,
    maximum_problem_characters: int = 20_000,
) -> tuple[SweBenchCase, ...]:
    """Load metadata only; this never clones repositories or executes tests."""

    if maximum_cases <= 0 or maximum_problem_characters <= 0:
        raise ValueError("SWE-bench subset limits must be greater than zero")
    try:
        lines = path.resolve().read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise SweBenchSubsetError(f"Could not read SWE-bench subset: {error}") from error
    if not lines:
        raise SweBenchSubsetError("SWE-bench subset is empty")
    if len(lines) > maximum_cases:
        raise SweBenchSubsetError(f"Subset exceeds the {maximum_cases}-case limit")
    cases: list[SweBenchCase] = []
    identifiers: set[str] = set()
    for line_number, line in enumerate(lines, start=1):
        try:
            case = _parse_case(json.loads(line), maximum_problem_characters)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            raise SweBenchSubsetError(
                f"Invalid SWE-bench case on line {line_number}: {error}"
            ) from error
        if case.instance_id in identifiers:
            raise SweBenchSubsetError(f"Duplicate instance_id '{case.instance_id}'")
        identifiers.add(case.instance_id)
        cases.append(case)
    return tuple(cases)


def select_balanced_swebench_cases(
    rows: Iterable[Mapping[str, Any]], *, limit: int = 100
) -> tuple[SweBenchCase, ...]:
    """Round-robin repositories so a prefix is not dominated by one project."""

    if limit <= 0:
        raise ValueError("SWE-bench selection limit must be greater than zero")
    buckets: dict[str, deque[SweBenchCase]] = defaultdict(deque)
    for row in rows:
        case = _parse_case(dict(row), 100_000)
        buckets[case.repository].append(case)
    for bucket in buckets.values():
        ordered = sorted(bucket, key=lambda case: case.instance_id)
        bucket.clear()
        bucket.extend(ordered)
    selected: list[SweBenchCase] = []
    repositories = sorted(buckets)
    while len(selected) < limit:
        progressed = False
        for repository in repositories:
            if buckets[repository] and len(selected) < limit:
                selected.append(buckets[repository].popleft())
                progressed = True
        if not progressed:
            break
    return tuple(selected)


def download_verified_subset(*, limit: int = 100) -> tuple[SweBenchCase, ...]:
    """Download metadata from the official Verified test split and balance it."""

    try:
        datasets = import_module("datasets")
    except ImportError as error:
        raise SweBenchSubsetError(
            "Install AgentForge's swebench extra inside the project virtual environment"
        ) from error
    try:
        rows = datasets.load_dataset(
            "princeton-nlp/SWE-bench_Verified", split="test"
        )
    except Exception as error:
        raise SweBenchSubsetError(f"Could not download SWE-bench Verified: {error}") from error
    return select_balanced_swebench_cases(rows, limit=limit)


def write_swebench_subset(path: Path, cases: Iterable[SweBenchCase]) -> None:
    """Persist only task metadata; never store the benchmark's reference patch."""

    output = path.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps(
            {
                "instance_id": case.instance_id,
                "repo": case.repository,
                "base_commit": case.base_commit,
                "problem_statement": case.problem_statement,
                "FAIL_TO_PASS": list(case.fail_to_pass),
                "PASS_TO_PASS": list(case.pass_to_pass),
            },
            ensure_ascii=False,
        )
        for case in cases
    ]
    output.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _parse_case(document: object, maximum_problem_characters: int) -> SweBenchCase:
    if not isinstance(document, dict):
        raise TypeError("case must be an object")
    required = {
        "instance_id",
        "repo",
        "base_commit",
        "problem_statement",
        "FAIL_TO_PASS",
        "PASS_TO_PASS",
    }
    missing = required - document.keys()
    if missing:
        raise KeyError("missing fields: " + ", ".join(sorted(missing)))
    text_values = {
        name: document[name]
        for name in ("instance_id", "repo", "base_commit", "problem_statement")
    }
    if not all(isinstance(value, str) and value.strip() for value in text_values.values()):
        raise TypeError("identity, repository, commit, and problem must be non-empty text")
    problem = text_values["problem_statement"].strip()
    if len(problem) > maximum_problem_characters:
        raise ValueError("problem_statement exceeds the configured character limit")
    commit = text_values["base_commit"].strip()
    if not _COMMIT_PATTERN.fullmatch(commit):
        raise ValueError("base_commit must be a hexadecimal Git object id")
    fail_to_pass = _test_list(document["FAIL_TO_PASS"], "FAIL_TO_PASS")
    pass_to_pass = _test_list(document["PASS_TO_PASS"], "PASS_TO_PASS")
    if not fail_to_pass:
        raise ValueError("FAIL_TO_PASS must contain at least one test")
    return SweBenchCase(
        text_values["instance_id"].strip(),
        text_values["repo"].strip(),
        commit,
        problem,
        fail_to_pass,
        pass_to_pass,
    )


def _test_list(value: object, label: str) -> tuple[str, ...]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as error:
            raise ValueError(f"{label} string must contain a JSON list") from error
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise TypeError(f"{label} must be a list of non-empty test names")
    return tuple(item.strip() for item in value)
