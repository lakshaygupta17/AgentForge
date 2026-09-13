"""Artifacts produced by the first specialist agents."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Plan:
    steps: tuple[str, ...]
    rationale: str


@dataclass(frozen=True, slots=True)
class ExplorationReport:
    summary: str
    relevant_files: tuple[str, ...]
    findings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DevelopmentReport:
    summary: str
    changed_files: tuple[str, ...]
    patch_count: int


@dataclass(frozen=True, slots=True)
class TestReport:
    passed: bool
    summary: str
    command: tuple[str, ...]
    exit_code: int | None
    timed_out: bool
    stdout: str
    stderr: str


@dataclass(frozen=True, slots=True)
class Diagnosis:
    hypothesis: str
    evidence: tuple[str, ...]
    recommended_changes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReviewReport:
    accepted: bool
    summary: str
    issues: tuple[str, ...]
    diff: str
