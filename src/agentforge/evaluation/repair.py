"""Deterministic end-to-end repair benchmark for the personal project."""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from agentforge.agents.debugger import DebuggerAgent
from agentforge.agents.developer import DeveloperAgent
from agentforge.agents.explorer import RepositoryExplorerAgent
from agentforge.agents.models import ReviewReport, TestReport
from agentforge.agents.planner import PlannerAgent
from agentforge.agents.reviewer import REVIEW_ARTIFACT, ReviewerAgent
from agentforge.agents.tester import TEST_ARTIFACT, TesterAgent
from agentforge.core.models import Task
from agentforge.llm.mock import MockLLMProvider
from agentforge.llm.models import ChatMessage, ChatResponse, ToolCall
from agentforge.orchestration.repair import RepairOrchestrator
from agentforge.policy.engine import SecurityPolicy
from agentforge.sandbox.local import LocalSandbox
from agentforge.telemetry.trajectory import InMemoryTrajectoryRecorder, TrajectoryEvent
from agentforge.tools.editor import RepositoryEditor
from agentforge.tools.repository import RepositoryInspector


@dataclass(frozen=True, slots=True)
class RepairBenchmarkResult:
    succeeded: bool
    tests_passed: bool
    review_accepted: bool
    repair_attempts: int
    completed_agent_steps: int
    trajectory_events: int
    duration_seconds: float
    case_name: str = "parity_direct"


@dataclass(frozen=True, slots=True)
class RepairBenchmarkSuiteResult:
    succeeded: bool
    case_count: int
    passed_cases: int
    total_repair_attempts: int
    total_duration_seconds: float
    cases: tuple[RepairBenchmarkResult, ...]


@dataclass(frozen=True, slots=True)
class RepairBenchmarkSuiteRecord:
    schema_version: int
    recorded_at: str
    result: RepairBenchmarkSuiteResult


@dataclass(frozen=True, slots=True)
class RepairBenchmarkSuiteSummary:
    record_count: int
    successful_runs: int
    latest_passed_cases: int
    latest_case_count: int
    latest_total_repair_attempts: int
    functional_regressions: tuple[str, ...]

    @property
    def regression_detected(self) -> bool:
        return bool(self.functional_regressions)


@dataclass(frozen=True, slots=True)
class RepairBenchmarkRecord:
    schema_version: int
    recorded_at: str
    result: RepairBenchmarkResult


@dataclass(frozen=True, slots=True)
class RepairBenchmarkSummary:
    record_count: int
    successful_runs: int
    success_rate: float
    average_duration_seconds: float
    latest_duration_seconds: float
    duration_change_seconds: float | None
    performance_warning: bool
    functional_regressions: tuple[str, ...]

    @property
    def regression_detected(self) -> bool:
        return bool(self.functional_regressions)


class BenchmarkHistoryError(ValueError):
    """A benchmark history file is absent, empty, or malformed."""


BenchmarkEventObserver = Callable[[str, TrajectoryEvent], None]


def append_repair_benchmark(
    path: Path, result: RepairBenchmarkResult
) -> RepairBenchmarkRecord:
    """Append one non-secret benchmark result as a JSON Lines record."""

    record = RepairBenchmarkRecord(
        schema_version=1,
        recorded_at=datetime.now(UTC).isoformat(),
        result=result,
    )
    output = path.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("a", encoding="utf-8") as history:
        json.dump(asdict(record), history, separators=(",", ":"))
        history.write("\n")
    return record


def append_repair_benchmark_suite(
    path: Path, result: RepairBenchmarkSuiteResult
) -> RepairBenchmarkSuiteRecord:
    """Append one complete suite result to its own JSON Lines history."""

    record = RepairBenchmarkSuiteRecord(
        schema_version=1,
        recorded_at=datetime.now(UTC).isoformat(),
        result=result,
    )
    output = path.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("a", encoding="utf-8") as history:
        json.dump(asdict(record), history, separators=(",", ":"))
        history.write("\n")
    return record


def load_repair_benchmarks(path: Path) -> tuple[RepairBenchmarkRecord, ...]:
    try:
        lines = path.resolve().read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise BenchmarkHistoryError(f"Could not read benchmark history: {error}") from error
    if not lines:
        raise BenchmarkHistoryError("Benchmark history is empty")
    records: list[RepairBenchmarkRecord] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            raise BenchmarkHistoryError(
                f"Benchmark history line {line_number} is empty"
            )
        try:
            document = json.loads(line)
            records.append(_parse_benchmark_record(document))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            raise BenchmarkHistoryError(
                f"Invalid benchmark history record on line {line_number}: {error}"
            ) from error
    return tuple(records)


def load_repair_benchmark_suites(
    path: Path,
) -> tuple[RepairBenchmarkSuiteRecord, ...]:
    """Load and validate every multi-case suite history record."""

    try:
        lines = path.resolve().read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise BenchmarkHistoryError(f"Could not read suite history: {error}") from error
    if not lines:
        raise BenchmarkHistoryError("Benchmark suite history is empty")
    records: list[RepairBenchmarkSuiteRecord] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            raise BenchmarkHistoryError(
                f"Benchmark suite history line {line_number} is empty"
            )
        try:
            records.append(_parse_benchmark_suite_record(json.loads(line)))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            raise BenchmarkHistoryError(
                f"Invalid benchmark suite history record on line {line_number}: {error}"
            ) from error
    return tuple(records)


def summarize_repair_benchmarks(
    records: tuple[RepairBenchmarkRecord, ...],
) -> RepairBenchmarkSummary:
    if not records:
        raise BenchmarkHistoryError("At least one benchmark record is required")
    latest = records[-1].result
    previous = records[-2].result if len(records) > 1 else None
    duration_change = (
        latest.duration_seconds - previous.duration_seconds
        if previous is not None
        else None
    )
    performance_warning = (
        previous is not None
        and duration_change is not None
        and duration_change > 0.1
        and latest.duration_seconds > previous.duration_seconds * 1.5
    )
    regressions: list[str] = []
    if previous is not None:
        for field_name, label in (
            ("succeeded", "workflow no longer succeeds"),
            ("tests_passed", "tests no longer pass"),
            ("review_accepted", "review no longer accepts the repair"),
        ):
            if getattr(previous, field_name) and not getattr(latest, field_name):
                regressions.append(label)
        if latest.repair_attempts > previous.repair_attempts:
            regressions.append("repair attempts increased")
    successful_runs = sum(record.result.succeeded for record in records)
    return RepairBenchmarkSummary(
        record_count=len(records),
        successful_runs=successful_runs,
        success_rate=successful_runs / len(records),
        average_duration_seconds=sum(
            record.result.duration_seconds for record in records
        )
        / len(records),
        latest_duration_seconds=latest.duration_seconds,
        duration_change_seconds=duration_change,
        performance_warning=performance_warning,
        functional_regressions=tuple(regressions),
    )


def summarize_repair_benchmark_suites(
    records: tuple[RepairBenchmarkSuiteRecord, ...],
) -> RepairBenchmarkSuiteSummary:
    """Compare the latest suite with its predecessor by stable case name."""

    if not records:
        raise BenchmarkHistoryError("At least one benchmark suite record is required")
    latest = records[-1].result
    previous = records[-2].result if len(records) > 1 else None
    regressions: list[str] = []
    if previous is not None:
        latest_cases = {case.case_name: case for case in latest.cases}
        for old_case in previous.cases:
            new_case = latest_cases.get(old_case.case_name)
            if new_case is None:
                regressions.append(f"{old_case.case_name}: case is missing")
                continue
            for field_name, label in (
                ("succeeded", "workflow no longer succeeds"),
                ("tests_passed", "tests no longer pass"),
                ("review_accepted", "review no longer accepts the repair"),
            ):
                if getattr(old_case, field_name) and not getattr(new_case, field_name):
                    regressions.append(f"{old_case.case_name}: {label}")
            if new_case.repair_attempts > old_case.repair_attempts:
                regressions.append(f"{old_case.case_name}: repair attempts increased")
    return RepairBenchmarkSuiteSummary(
        record_count=len(records),
        successful_runs=sum(record.result.succeeded for record in records),
        latest_passed_cases=latest.passed_cases,
        latest_case_count=latest.case_count,
        latest_total_repair_attempts=latest.total_repair_attempts,
        functional_regressions=tuple(regressions),
    )


def _parse_benchmark_record(document: object) -> RepairBenchmarkRecord:
    if not isinstance(document, dict):
        raise TypeError("record must be an object")
    if document.get("schema_version") != 1:
        raise ValueError("schema_version must be 1")
    recorded_at = document["recorded_at"]
    if not isinstance(recorded_at, str):
        raise TypeError("recorded_at must be text")
    datetime.fromisoformat(recorded_at)
    result = _parse_benchmark_result(document["result"])
    return RepairBenchmarkRecord(1, recorded_at, result)


def _parse_benchmark_result(result_document: object) -> RepairBenchmarkResult:
    if not isinstance(result_document, dict):
        raise TypeError("result must be an object")
    boolean_fields = ("succeeded", "tests_passed", "review_accepted")
    integer_fields = ("repair_attempts", "completed_agent_steps", "trajectory_events")
    for key in boolean_fields:
        if not isinstance(result_document.get(key), bool):
            raise TypeError(f"result.{key} must be true or false")
    for key in integer_fields:
        value = result_document.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise TypeError(f"result.{key} must be a non-negative integer")
    duration = result_document.get("duration_seconds")
    if (
        isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or duration < 0
    ):
        raise TypeError("result.duration_seconds must be a non-negative number")
    case_name = result_document.get("case_name", "parity_direct")
    if not isinstance(case_name, str) or not case_name.strip():
        raise TypeError("result.case_name must be non-empty text")
    return RepairBenchmarkResult(
        succeeded=result_document["succeeded"],
        tests_passed=result_document["tests_passed"],
        review_accepted=result_document["review_accepted"],
        repair_attempts=result_document["repair_attempts"],
        completed_agent_steps=result_document["completed_agent_steps"],
        trajectory_events=result_document["trajectory_events"],
        duration_seconds=float(duration),
        case_name=case_name,
    )


def _parse_benchmark_suite_record(document: object) -> RepairBenchmarkSuiteRecord:
    if not isinstance(document, dict):
        raise TypeError("record must be an object")
    if document.get("schema_version") != 1:
        raise ValueError("schema_version must be 1")
    recorded_at = document["recorded_at"]
    if not isinstance(recorded_at, str):
        raise TypeError("recorded_at must be text")
    datetime.fromisoformat(recorded_at)
    result_document = document["result"]
    if not isinstance(result_document, dict):
        raise TypeError("result must be an object")
    if not isinstance(result_document.get("succeeded"), bool):
        raise TypeError("result.succeeded must be true or false")
    integer_fields = ("case_count", "passed_cases", "total_repair_attempts")
    for key in integer_fields:
        value = result_document.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise TypeError(f"result.{key} must be a non-negative integer")
    duration = result_document.get("total_duration_seconds")
    if (
        isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or duration < 0
    ):
        raise TypeError("result.total_duration_seconds must be a non-negative number")
    case_documents = result_document.get("cases")
    if not isinstance(case_documents, list):
        raise TypeError("result.cases must be a list")
    cases = tuple(_parse_benchmark_result(case) for case in case_documents)
    case_names = [case.case_name for case in cases]
    if len(set(case_names)) != len(case_names):
        raise ValueError("result.cases must have unique case names")
    passed_cases = sum(case.succeeded for case in cases)
    attempts = sum(case.repair_attempts for case in cases)
    if result_document["case_count"] != len(cases):
        raise ValueError("result.case_count does not match cases")
    if result_document["passed_cases"] != passed_cases:
        raise ValueError("result.passed_cases does not match cases")
    if result_document["total_repair_attempts"] != attempts:
        raise ValueError("result.total_repair_attempts does not match cases")
    succeeded = result_document["succeeded"]
    if succeeded != (passed_cases == len(cases)):
        raise ValueError("result.succeeded does not match cases")
    result = RepairBenchmarkSuiteResult(
        succeeded=succeeded,
        case_count=len(cases),
        passed_cases=passed_cases,
        total_repair_attempts=attempts,
        total_duration_seconds=float(duration),
        cases=cases,
    )
    return RepairBenchmarkSuiteRecord(1, recorded_at, result)


def _tool_response(
    call_id: str, name: str, arguments: dict[str, object]
) -> ChatResponse:
    return ChatResponse(
        ChatMessage(
            "assistant",
            None,
            tool_calls=(ToolCall(call_id, name, arguments),),
        ),
        "mock-benchmark-model",
    )


def _direct_repair_responses(test_call: dict[str, object]) -> list[ChatResponse]:
    return [
        _tool_response(
            "plan",
            "submit_plan",
            {
                "steps": ["Inspect predicate", "Correct it", "Run tests"],
                "rationale": "The expected parity behavior is directly testable.",
            },
        ),
        _tool_response(
            "explore",
            "finish_exploration",
            {
                "summary": "The parity comparison is reversed",
                "relevant_files": ["calculator.py", "spec.py"],
                "findings": ["is_even compares the remainder with one"],
            },
        ),
        _tool_response(
            "patch",
            "apply_patch",
            {
                "changes": [
                    {
                        "path": "calculator.py",
                        "old_text": "return value % 2 == 1",
                        "new_text": "return value % 2 == 0",
                    }
                ]
            },
        ),
        _tool_response(
            "development",
            "finish_development",
            {
                "summary": "Corrected the parity comparison",
                "changed_files": ["calculator.py"],
            },
        ),
        _tool_response("test", "run_tests", test_call),
        _tool_response(
            "test-result",
            "finish_testing",
            {"passed": True, "summary": "Both parity checks pass"},
        ),
        _tool_response(
            "review",
            "submit_review",
            {
                "accepted": True,
                "summary": "The minimal change matches the specification",
                "issues": [],
            },
        ),
    ]


def _retry_repair_responses(test_call: dict[str, object]) -> list[ChatResponse]:
    responses = _direct_repair_responses(test_call)[:2]
    responses.extend(
        [
            _tool_response(
                "bad-patch",
                "apply_patch",
                {
                    "changes": [
                        {
                            "path": "calculator.py",
                            "old_text": "return value % 2 == 1",
                            "new_text": "return value % 2 == 2",
                        }
                    ]
                },
            ),
            _tool_response(
                "bad-development",
                "finish_development",
                {
                    "summary": "Changed the remainder comparison",
                    "changed_files": ["calculator.py"],
                },
            ),
            _tool_response("failed-run", "run_tests", test_call),
            _tool_response(
                "failed-report",
                "finish_testing",
                {"passed": False, "summary": "The parity tests still fail"},
            ),
            _tool_response(
                "diagnosis",
                "submit_diagnosis",
                {
                    "hypothesis": "A remainder modulo two can never equal two",
                    "evidence": ["Both parity assertions failed after the first patch"],
                    "recommended_changes": ["Compare the remainder with zero"],
                },
            ),
            _tool_response(
                "good-patch",
                "apply_patch",
                {
                    "changes": [
                        {
                            "path": "calculator.py",
                            "old_text": "return value % 2 == 2",
                            "new_text": "return value % 2 == 0",
                        }
                    ]
                },
            ),
            _tool_response(
                "good-development",
                "finish_development",
                {
                    "summary": "Applied the debugger's corrected comparison",
                    "changed_files": ["calculator.py"],
                },
            ),
            _tool_response("passing-run", "run_tests", test_call),
            _tool_response(
                "passing-report",
                "finish_testing",
                {"passed": True, "summary": "Both parity checks pass"},
            ),
            _tool_response(
                "review",
                "submit_review",
                {
                    "accepted": True,
                    "summary": "The corrected retry matches the specification",
                    "issues": [],
                },
            ),
        ]
    )
    return responses


def _review_retry_responses(test_call: dict[str, object]) -> list[ChatResponse]:
    responses = _direct_repair_responses(test_call)[:2]
    responses.extend(
        [
            _tool_response(
                "equivalent-patch",
                "apply_patch",
                {
                    "changes": [
                        {
                            "path": "calculator.py",
                            "old_text": "return value % 2 == 1",
                            "new_text": "return value % 2 != 1",
                        }
                    ]
                },
            ),
            _tool_response(
                "first-development",
                "finish_development",
                {
                    "summary": "Applied an equivalent parity condition",
                    "changed_files": ["calculator.py"],
                },
            ),
            _tool_response("first-test", "run_tests", test_call),
            _tool_response(
                "first-test-result",
                "finish_testing",
                {"passed": True, "summary": "Both parity checks pass"},
            ),
            _tool_response(
                "rejected-review",
                "submit_review",
                {
                    "accepted": False,
                    "summary": "The condition obscures the intended even remainder",
                    "issues": ["Compare the remainder explicitly with zero"],
                },
            ),
            _tool_response(
                "review-fix",
                "apply_patch",
                {
                    "changes": [
                        {
                            "path": "calculator.py",
                            "old_text": "return value % 2 != 1",
                            "new_text": "return value % 2 == 0",
                        }
                    ]
                },
            ),
            _tool_response(
                "second-development",
                "finish_development",
                {
                    "summary": "Applied the reviewer's explicit remainder comparison",
                    "changed_files": ["calculator.py"],
                },
            ),
            _tool_response("second-test", "run_tests", test_call),
            _tool_response(
                "second-test-result",
                "finish_testing",
                {"passed": True, "summary": "Both parity checks still pass"},
            ),
            _tool_response(
                "accepted-review",
                "submit_review",
                {
                    "accepted": True,
                    "summary": "The explicit repair is clear and tested",
                    "issues": [],
                },
            ),
        ]
    )
    return responses


def _run_seeded_repair_case(
    case_name: str,
    responses_factory: Callable[[dict[str, object]], list[ChatResponse]],
    event_observer: BenchmarkEventObserver | None = None,
) -> RepairBenchmarkResult:
    """Run one deterministic parity-repair scenario against real local tests."""

    started_at = time.monotonic()
    with LocalSandbox() as sandbox:
        (sandbox.workspace / "calculator.py").write_text(
            "def is_even(value: int) -> bool:\n    return value % 2 == 1\n",
            encoding="utf-8",
        )
        (sandbox.workspace / "spec.py").write_text(
            "from calculator import is_even\n\n"
            "def test_even():\n    assert is_even(4)\n\n"
            "def test_odd():\n    assert not is_even(3)\n",
            encoding="utf-8",
        )
        test_call: dict[str, object] = {
            "executable": sys.executable,
            "arguments": ["-B", "-m", "pytest", "spec.py", "-q"],
            "timeout_seconds": 10,
        }
        provider = MockLLMProvider(responses_factory(test_call))
        recorder = InMemoryTrajectoryRecorder(
            on_record=(
                None
                if event_observer is None
                else lambda event: event_observer(case_name, event)
            )
        )
        inspector = RepositoryInspector(sandbox.workspace)
        editor = RepositoryEditor(inspector)
        workflow = RepairOrchestrator(
            planner=PlannerAgent(provider, recorder=recorder),
            explorer=RepositoryExplorerAgent(provider, inspector, recorder=recorder),
            developer=DeveloperAgent(provider, editor, recorder=recorder),
            tester=TesterAgent(
                provider, sandbox, SecurityPolicy(), recorder=recorder
            ),
            debugger=DebuggerAgent(provider, recorder=recorder),
            reviewer=ReviewerAgent(provider, editor, recorder=recorder),
            recorder=recorder,
            maximum_repair_attempts=2,
        )
        state = workflow.run(Task("Fix is_even so the parity checks pass"))

    tests = state.artifacts.get(TEST_ARTIFACT)
    review = state.artifacts.get(REVIEW_ARTIFACT)
    repair_attempts = sum(
        event.event_type == "repair_attempt_started" for event in recorder.events
    )
    return RepairBenchmarkResult(
        succeeded=not state.failed,
        tests_passed=isinstance(tests, TestReport) and tests.passed,
        review_accepted=isinstance(review, ReviewReport) and review.accepted,
        repair_attempts=repair_attempts,
        completed_agent_steps=len(state.completed_agents),
        trajectory_events=len(recorder.events),
        duration_seconds=time.monotonic() - started_at,
        case_name=case_name,
    )


def run_seeded_repair_benchmark(
    event_observer: BenchmarkEventObserver | None = None,
) -> RepairBenchmarkResult:
    """Repair a known parity bug directly with fixed model responses."""

    return _run_seeded_repair_case(
        "parity_direct", _direct_repair_responses, event_observer
    )


def run_seeded_retry_benchmark(
    event_observer: BenchmarkEventObserver | None = None,
) -> RepairBenchmarkResult:
    """Make a bad repair, diagnose its failed tests, then retry successfully."""

    return _run_seeded_repair_case(
        "parity_debug_retry", _retry_repair_responses, event_observer
    )


def run_seeded_review_retry_benchmark(
    event_observer: BenchmarkEventObserver | None = None,
) -> RepairBenchmarkResult:
    """Pass tests, recover from a rejected review, and gain final acceptance."""

    return _run_seeded_repair_case(
        "parity_review_retry", _review_retry_responses, event_observer
    )


def run_repair_benchmark_suite(
    event_observer: BenchmarkEventObserver | None = None,
) -> RepairBenchmarkSuiteResult:
    """Run all deterministic repair scenarios and aggregate their outcomes."""

    cases = (
        run_seeded_repair_benchmark(event_observer),
        run_seeded_retry_benchmark(event_observer),
        run_seeded_review_retry_benchmark(event_observer),
    )
    passed_cases = sum(case.succeeded for case in cases)
    return RepairBenchmarkSuiteResult(
        succeeded=passed_cases == len(cases),
        case_count=len(cases),
        passed_cases=passed_cases,
        total_repair_attempts=sum(case.repair_attempts for case in cases),
        total_duration_seconds=sum(case.duration_seconds for case in cases),
        cases=cases,
    )
