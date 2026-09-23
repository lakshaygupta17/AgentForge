"""Run AgentForge against SWE-bench repositories hosted in E2B microVMs.

The model sees only the issue text and repository files.  Gold patches and the
held-out grading tests are deliberately not passed to any AgentForge agent.
"""

from __future__ import annotations

import json
import re
import shlex
import tempfile
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from importlib import import_module
from pathlib import Path, PurePosixPath
from types import TracebackType
from typing import Any

from agentforge.agents.debugger import DebuggerAgent
from agentforge.agents.developer import DeveloperAgent
from agentforge.agents.explorer import RepositoryExplorerAgent
from agentforge.agents.planner import PlannerAgent
from agentforge.agents.reviewer import ReviewerAgent
from agentforge.agents.tester import TesterAgent
from agentforge.core.approval import ApprovalCheckpoint
from agentforge.core.models import ActionRequest, ExecutionResult, SandboxLimits, Task
from agentforge.credentials import HostCredentialBroker
from agentforge.evaluation.batch import CaseStatus, RepairCaseResult
from agentforge.evaluation.swebench import SweBenchCase
from agentforge.llm.budget import (
    BudgetedProvider,
    ModelPricing,
    SharedInferenceBudget,
)
from agentforge.llm.errors import ProviderConnectionError, ProviderRateLimitError
from agentforge.llm.fallback import TransientFallbackProvider
from agentforge.llm.huggingface import HuggingFaceProvider
from agentforge.llm.models import ChatMessage
from agentforge.llm.openai_compatible import RetryPolicy
from agentforge.orchestration.repair import RepairOrchestrator
from agentforge.policy.engine import FileAccessRequest, FileOperation, SecurityPolicy
from agentforge.sandbox.e2b import (
    E2BClient,
    E2BClientFactory,
    E2BCommandTimeout,
    SdkE2BClientFactory,
)
from agentforge.telemetry.trajectory import JsonlTrajectoryRecorder
from agentforge.tools.editor import RepositoryEditor
from agentforge.tools.repository import RepositoryInspector, RepositoryToolError

SWEBENCH_REMOTE_ROOT = PurePosixPath("/testbed")
SWEBENCH_TEMPLATE_PREFIX = "swebench-"
SWEBENCH_CONDA_ACTIVATE = "source /opt/miniconda3/bin/activate testbed >/dev/null 2>&1"
_RETRYABLE_FAILURE_MARKERS = (
    "ProviderResponseError",
    "ProviderBudgetExceededError",
    "Inference budget stopped the run",
    "Provider returned HTTP 402",
    "Provider returned HTTP 408",
    "Provider returned HTTP 429",
    "Provider request failed",
    "SandboxUnavailableError",
    "E2B could not",
    "File exceeds the 100000-byte inspection limit",
)


def swebench_template_name(instance_id: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", instance_id.casefold()).strip("-")
    return f"{SWEBENCH_TEMPLATE_PREFIX}{slug}"


def swebench_instance_image(instance_id: str) -> str:
    """Return the official public SWE-bench x86_64 image for one instance."""

    image_id = instance_id.casefold().replace("__", "_1776_")
    return f"swebench/sweb.eval.x86_64.{image_id}:latest"


def is_retryable_generation_failure(reason: str | None) -> bool:
    """Separate provider/infrastructure failures from genuine repair failures."""

    if reason is None:
        return False
    return any(marker in reason for marker in _RETRYABLE_FAILURE_MARKERS)


def ensure_swebench_template(
    instance_id: str,
    *,
    cpu_count: int = 8,
    memory_mb: int = 8192,
) -> tuple[str, bool]:
    """Build an E2B template from the official image when it is absent."""

    if cpu_count <= 0 or memory_mb <= 0 or memory_mb % 2:
        raise ValueError("Template CPU and positive even memory are required")
    e2b = import_module("e2b")
    api_key = HostCredentialBroker.from_process().require_secret("E2B_API_KEY")
    name = swebench_template_name(instance_id)
    if e2b.Template.exists(name, api_key=api_key):
        return name, False
    builder = (
        e2b.Template()
        .from_image(swebench_instance_image(instance_id))
        .set_workdir(str(SWEBENCH_REMOTE_ROOT))
    )
    e2b.Template.build(
        builder,
        name,
        cpu_count=cpu_count,
        memory_mb=memory_mb,
        api_key=api_key,
    )
    return name, True


class RemoteRepositoryInspector(RepositoryInspector):
    """RepositoryInspector-compatible reads from a prepared E2B ``/testbed``."""

    def __init__(
        self,
        client: E2BClient,
        policy_root: Path,
        *,
        policy: SecurityPolicy,
        maximum_file_bytes: int = 1_000_000,
        maximum_files: int = 2_000,
        maximum_search_results: int = 50,
    ) -> None:
        self._client = client
        self.root = policy_root.resolve()
        self.maximum_file_bytes = maximum_file_bytes
        self.maximum_files = maximum_files
        self.maximum_search_results = maximum_search_results
        self.policy = policy

    def list_files(self, path: str = ".") -> list[str]:
        relative = self._safe_relative(path, allow_root=True)
        remote = SWEBENCH_REMOTE_ROOT / relative
        try:
            candidates = self._client.list_files(str(remote))
        except Exception as error:
            raise RepositoryToolError(f"Could not list remote path {path}: {error}") from error

        files: list[str] = []
        ignored = {name.casefold() for name in self.DEFAULT_IGNORED_NAMES}
        for raw_path in candidates:
            candidate = PurePosixPath(raw_path)
            if not candidate.is_absolute():
                candidate = remote / candidate
            try:
                item = candidate.relative_to(SWEBENCH_REMOTE_ROOT)
            except ValueError:
                continue
            if not item.parts or any(part.casefold() in ignored for part in item.parts):
                continue
            if self._is_sensitive_name(item) or not self._policy_allows_relative(item, "read"):
                continue
            files.append(item.as_posix())
            if len(files) >= self.maximum_files:
                break
        return sorted(set(files))

    def read_file(self, path: str) -> str:
        relative = self._safe_relative(path)
        self._assert_policy_relative(relative, "read")
        if self._is_sensitive_name(relative):
            raise RepositoryToolError("Sensitive credential files cannot be inspected")
        try:
            content = self._client.read_file(str(SWEBENCH_REMOTE_ROOT / relative))
        except Exception as error:
            raise RepositoryToolError(f"Could not read remote file {path}: {error}") from error
        if len(content) > self.maximum_file_bytes:
            raise RepositoryToolError(
                f"File exceeds the {self.maximum_file_bytes}-byte inspection limit: {path}"
            )
        if b"\x00" in content:
            raise RepositoryToolError(f"Binary files cannot be read as text: {path}")
        return content.decode("utf-8", errors="replace")

    def search_code(self, query: str, path: str = ".") -> list[dict[str, object]]:
        if not query.strip():
            raise RepositoryToolError("Search query cannot be empty")
        relative = self._safe_relative(path, allow_root=True)
        target = relative.as_posix() if relative.parts else "."
        excluded_directories = " ".join(
            f"--exclude-dir={shlex.quote(name)}" for name in sorted(self.DEFAULT_IGNORED_NAMES)
        )
        excluded_files = " ".join(
            f"--exclude={shlex.quote(name)}" for name in sorted(self.DEFAULT_SENSITIVE_FILES)
        )
        command = (
            "grep -R -n -i -F --binary-files=without-match "
            f"{excluded_directories} {excluded_files} -- "
            f"{shlex.quote(query)} {shlex.quote(target)} "
            f"| head -n {self.maximum_search_results}"
        )
        try:
            outcome = self._client.run(
                command,
                cwd=str(SWEBENCH_REMOTE_ROOT),
                environment={},
                timeout_seconds=60,
            )
        except Exception as error:
            raise RepositoryToolError(f"Remote code search failed: {error}") from error
        if outcome.exit_code not in {0, 1}:
            raise RepositoryToolError(
                "Remote code search failed: " + (outcome.stderr.strip() or "grep error")
            )
        results: list[dict[str, object]] = []
        for raw_line in outcome.stdout.splitlines():
            parts = raw_line.split(":", 2)
            if len(parts) != 3 or not parts[1].isdigit():
                continue
            try:
                match_path = self._safe_relative(parts[0].removeprefix("./"))
                self._assert_policy_relative(match_path, "read")
            except RepositoryToolError:
                continue
            results.append(
                {
                    "path": match_path.as_posix(),
                    "line": int(parts[1]),
                    "preview": parts[2].strip()[:300],
                }
            )
        return results

    def validate_file_reference(self, path: str, *, operation: FileOperation = "read") -> str:
        relative = self._safe_relative(path)
        self._assert_policy_relative(relative, operation)
        if self._is_sensitive_name(relative):
            raise RepositoryToolError("Sensitive credential files cannot be inspected")
        self.read_file(relative.as_posix())
        return relative.as_posix()

    def _safe_relative(self, path: str, *, allow_root: bool = False) -> PurePosixPath:
        if not path.strip():
            raise RepositoryToolError("Path cannot be empty")
        candidate = PurePosixPath(path.replace("\\", "/"))
        if candidate.is_absolute() or ".." in candidate.parts:
            raise RepositoryToolError("Path escapes the repository workspace")
        normalized = PurePosixPath(*[part for part in candidate.parts if part not in {"", "."}])
        if not normalized.parts and not allow_root:
            raise RepositoryToolError("A repository file path is required")
        return normalized

    def _assert_policy_relative(self, path: PurePosixPath, operation: FileOperation) -> None:
        decision = self.policy.evaluate_file_access(
            FileAccessRequest(Path(*path.parts), operation), self.root
        )
        if not decision.allowed:
            raise RepositoryToolError("; ".join(decision.reasons))

    def _policy_allows_relative(self, path: PurePosixPath, operation: FileOperation) -> bool:
        return self.policy.evaluate_file_access(
            FileAccessRequest(Path(*path.parts), operation), self.root
        ).allowed

    def _is_sensitive_name(self, path: PurePosixPath) -> bool:
        name = path.name.casefold()
        return name in {value.casefold() for value in self.DEFAULT_SENSITIVE_FILES} or any(
            name.endswith(suffix.casefold()) for suffix in self.DEFAULT_SENSITIVE_SUFFIXES
        )


class RemoteRepositoryEditor(RepositoryEditor):
    """Use the normal validated patch engine, committing writes to E2B."""

    def __init__(self, inspector: RemoteRepositoryInspector) -> None:
        super().__init__(inspector)
        self._remote_inspector = inspector

    def _atomic_write(self, path: Path, content: str) -> None:
        try:
            relative = path.relative_to(self.inspector.root).as_posix()
            self._remote_inspector._client.write_file(
                str(SWEBENCH_REMOTE_ROOT / PurePosixPath(relative)),
                content.encode("utf-8"),
            )
        except Exception as error:
            from agentforge.tools.editor import RepositoryEditError

            raise RepositoryEditError(f"Could not write {path.name}: {error}") from error


class SweBenchE2BSandbox:
    """A policy-controlled AgentForge sandbox bound to a prepared SWE-bench image."""

    def __init__(
        self,
        instance_id: str,
        *,
        factory: E2BClientFactory | None = None,
        sandbox_timeout_seconds: float = 3_600,
        maximum_output_bytes: int = 1_000_000,
    ) -> None:
        self.instance_id = instance_id
        self.limits = SandboxLimits(
            cpu_count=8,
            memory_mb=8192,
            maximum_output_bytes=maximum_output_bytes,
        )
        self._factory = factory or SdkE2BClientFactory()
        self._timeout = sandbox_timeout_seconds
        self._maximum_output_bytes = maximum_output_bytes
        self._temporary = tempfile.TemporaryDirectory(prefix="agentforge-swebench-")
        self._workspace = Path(self._temporary.name).resolve()
        self._client: E2BClient | None = None
        self._closed = False

    @property
    def workspace(self) -> Path:
        return self._workspace

    @property
    def client(self) -> E2BClient:
        if self._client is None:
            raise RuntimeError("SWE-bench microVM is not running")
        return self._client

    def start(self, policy: SecurityPolicy) -> None:
        decision = policy.evaluate_sandbox(self.limits)
        if not decision.allowed:
            raise PermissionError("; ".join(decision.reasons))
        self._client = self._factory.create(
            timeout_seconds=self._timeout,
            allow_internet_access=False,
            template=swebench_template_name(self.instance_id),
        )

    def execute(self, request: ActionRequest, policy: SecurityPolicy) -> ExecutionResult:
        decision = policy.evaluate(request, self.workspace)
        if not decision.allowed:
            raise PermissionError("; ".join(decision.reasons))
        relative = PurePosixPath(request.working_directory.as_posix())
        remote_directory = SWEBENCH_REMOTE_ROOT / relative
        command = f"{SWEBENCH_CONDA_ACTIVATE}; {shlex.join(request.argv)}"
        started = time.monotonic()
        try:
            outcome = self.client.run(
                command,
                cwd=str(remote_directory),
                environment=request.environment,
                timeout_seconds=request.timeout_seconds,
            )
        except E2BCommandTimeout:
            return ExecutionResult(None, "", "", time.monotonic() - started, timed_out=True)
        stdout, stdout_truncated = self._truncate(outcome.stdout)
        stderr, stderr_truncated = self._truncate(outcome.stderr)
        return ExecutionResult(
            outcome.exit_code,
            stdout,
            stderr,
            time.monotonic() - started,
            output_truncated=stdout_truncated or stderr_truncated,
        )

    def capture_patch(self) -> str:
        prepared = self.client.run(
            "git -c safe.directory=/testbed add --intent-to-add --all -- .",
            cwd=str(SWEBENCH_REMOTE_ROOT),
            environment={},
            timeout_seconds=60,
        )
        if prepared.exit_code != 0:
            raise RuntimeError(f"Could not prepare patch: {prepared.stderr}")
        result = self.client.run(
            "git -c safe.directory=/testbed diff --no-color --no-ext-diff --binary HEAD --",
            cwd=str(SWEBENCH_REMOTE_ROOT),
            environment={},
            timeout_seconds=60,
        )
        if result.exit_code != 0:
            raise RuntimeError(f"Could not capture patch: {result.stderr}")
        return result.stdout

    def close(self) -> None:
        if self._closed:
            return
        if self._client is not None:
            self._client.kill()
        self._temporary.cleanup()
        self._closed = True

    def _truncate(self, value: str) -> tuple[str, bool]:
        encoded = value.encode("utf-8")
        return (
            encoded[: self._maximum_output_bytes].decode("utf-8", errors="replace"),
            len(encoded) > self._maximum_output_bytes,
        )

    def __enter__(self) -> SweBenchE2BSandbox:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


@dataclass(frozen=True, slots=True)
class SweBenchPrediction:
    instance_id: str
    model_name_or_path: str
    model_patch: str


@dataclass(frozen=True, slots=True)
class ModelRouteHealth:
    economy_available: bool
    repair_available: bool
    economy_failure: str | None = None


def probe_model_routes(
    *,
    economy_model: str,
    repair_model: str,
    economy_pricing: ModelPricing | None = None,
    repair_pricing: ModelPricing | None = None,
    inference_budget: SharedInferenceBudget | None = None,
    economy_timeout_seconds: float = 30.0,
    repair_timeout_seconds: float = 180.0,
) -> ModelRouteHealth:
    """Check both routes before a paid microVM is created.

    The repair route is mandatory and therefore propagates any failure. A transient
    economy-route failure is recorded and causes Tester to start on its fallback.
    """

    broker = HostCredentialBroker.from_process()
    token = broker.require_secret("HF_TOKEN")
    economy_provider: Any = HuggingFaceProvider(
        token=token,
        model=economy_model,
        timeout_seconds=economy_timeout_seconds,
        maximum_output_tokens=16,
        retry_policy=RetryPolicy(maximum_attempts=2),
    )
    repair_provider: Any = HuggingFaceProvider(
        token=token,
        model=repair_model,
        timeout_seconds=repair_timeout_seconds,
        maximum_output_tokens=16,
        retry_policy=RetryPolicy(maximum_attempts=1),
    )
    if inference_budget is not None:
        if economy_pricing is None or repair_pricing is None:
            raise ValueError("Hybrid health probe requires pricing for both model routes")
        economy_provider = BudgetedProvider(
            economy_provider,
            inference_budget,
            pricing=economy_pricing,
            maximum_output_tokens=16,
        )
        repair_provider = BudgetedProvider(
            repair_provider,
            inference_budget,
            pricing=repair_pricing,
            maximum_output_tokens=16,
        )

    probe = [ChatMessage("user", "Reply with exactly: OK")]
    repair_provider.chat(probe, tool_choice="none")
    try:
        economy_provider.chat(probe, tool_choice="none")
    except (ProviderConnectionError, ProviderRateLimitError) as error:
        return ModelRouteHealth(False, True, f"{type(error).__name__}: {error}")
    return ModelRouteHealth(True, True)


def run_agentforge_prediction(
    case: SweBenchCase,
    *,
    output_directory: Path,
    maximum_repair_attempts: int = 3,
    economy_model: str | None = None,
    repair_model: str | None = None,
    economy_pricing: ModelPricing | None = None,
    repair_pricing: ModelPricing | None = None,
    inference_budget: SharedInferenceBudget | None = None,
    maximum_output_tokens: int = 4_096,
    economy_available: bool = True,
    economy_timeout_seconds: float = 30.0,
    repair_timeout_seconds: float = 180.0,
    maximum_case_model_requests: int | None = None,
    maximum_case_model_tokens: int | None = None,
    maximum_case_model_cost_usd: float | None = None,
) -> RepairCaseResult:
    """Generate one prediction; canonical grading happens in a fresh microVM later."""

    started = time.monotonic()
    output = output_directory.resolve()
    trajectories = output / "trajectories"
    predictions = output / "prediction-parts"
    trajectory_path = trajectories / f"{case.instance_id}.jsonl"
    prediction_path = predictions / f"{case.instance_id}.json"
    trajectories.mkdir(parents=True, exist_ok=True)
    predictions.mkdir(parents=True, exist_ok=True)
    policy = SecurityPolicy(
        maximum_timeout_seconds=600,
        maximum_cpu_count=8,
        maximum_memory_mb=8192,
        maximum_processes=128,
        maximum_output_bytes=1_000_000,
    )
    recorder = JsonlTrajectoryRecorder(trajectory_path)
    broker = HostCredentialBroker.from_process()
    token = broker.require_secret("HF_TOKEN")
    fallback_model = broker.require_setting("AGENTFORGE_MODEL")
    economy_name = economy_model or fallback_model
    repair_name = repair_model or fallback_model
    economy_provider = HuggingFaceProvider(
        token=token,
        model=economy_name,
        timeout_seconds=economy_timeout_seconds,
        maximum_output_tokens=maximum_output_tokens,
        retry_policy=RetryPolicy(maximum_attempts=2),
    )
    repair_provider = HuggingFaceProvider(
        token=token,
        model=repair_name,
        timeout_seconds=repair_timeout_seconds,
        maximum_output_tokens=maximum_output_tokens,
        retry_policy=RetryPolicy(maximum_attempts=1),
    )
    case_budget_values = (
        maximum_case_model_requests,
        maximum_case_model_tokens,
        maximum_case_model_cost_usd,
    )
    if any(value is not None for value in case_budget_values) and not all(
        value is not None for value in case_budget_values
    ):
        raise ValueError("All three per-case model budget limits must be provided together")
    case_budget = (
        SharedInferenceBudget(
            maximum_requests=maximum_case_model_requests,
            maximum_tokens=maximum_case_model_tokens,
            maximum_cost_usd=maximum_case_model_cost_usd,
        )
        if maximum_case_model_requests is not None
        and maximum_case_model_tokens is not None
        and maximum_case_model_cost_usd is not None
        else None
    )
    if inference_budget is not None or case_budget is not None:
        if economy_pricing is None or repair_pricing is None:
            raise ValueError("Hybrid budget requires pricing for both model routes")
        economy: Any = economy_provider
        repair: Any = repair_provider
        for budget in (inference_budget, case_budget):
            if budget is None:
                continue
            economy = BudgetedProvider(
                economy,
                budget,
                pricing=economy_pricing,
                maximum_output_tokens=maximum_output_tokens,
            )
            repair = BudgetedProvider(
                repair,
                budget,
                pricing=repair_pricing,
                maximum_output_tokens=maximum_output_tokens,
            )
    else:
        economy = economy_provider
        repair = repair_provider
    tester_provider = TransientFallbackProvider(
        economy,
        repair,
        failure_threshold=1,
        primary_enabled=economy_available,
    )

    with SweBenchE2BSandbox(case.instance_id) as sandbox:
        sandbox.start(policy)
        inspector = RemoteRepositoryInspector(sandbox.client, sandbox.workspace, policy=policy)
        editor = RemoteRepositoryEditor(inspector)
        workflow = RepairOrchestrator(
            planner=PlannerAgent(repair, recorder=recorder),
            explorer=RepositoryExplorerAgent(repair, inspector, recorder=recorder),
            developer=DeveloperAgent(repair, editor, recorder=recorder),
            tester=TesterAgent(tester_provider, sandbox, policy, recorder=recorder),
            debugger=DebuggerAgent(repair, recorder=recorder),
            reviewer=ReviewerAgent(repair, editor, recorder=recorder),
            recorder=recorder,
            maximum_repair_attempts=maximum_repair_attempts,
        )
        state = workflow.run(
            Task(case.problem_statement, repository=case.repository),
            approval=ApprovalCheckpoint(code_transmission=True, sandbox_execution=True),
        )
        patch = sandbox.capture_patch()

    if case_budget is not None:
        case_budgets = output / "case-budgets"
        case_budgets.mkdir(parents=True, exist_ok=True)
        (case_budgets / f"{case.instance_id}.json").write_text(
            json.dumps(asdict(case_budget.snapshot()), indent=2) + "\n",
            encoding="utf-8",
        )

    prediction = SweBenchPrediction(
        case.instance_id,
        f"agentforge-hybrid[reasoning={repair_name};tester={economy_name}->{repair_name}]",
        patch,
    )
    prediction_path.write_text(
        json.dumps(asdict(prediction), ensure_ascii=False) + "\n", encoding="utf-8"
    )
    events = _read_trajectory(trajectory_path)
    attempts = sum(event.get("event_type") == "repair_attempt_started" for event in events)
    tokens = sum(
        int(event.get("data", {}).get("total_tokens", 0))
        for event in events
        if event.get("event_type") == "llm_responded" and isinstance(event.get("data"), dict)
    )
    accepted = not state.failed and bool(patch.strip())
    status: CaseStatus = (
        "error"
        if is_retryable_generation_failure(state.failure_reason)
        else "resolved"
        if accepted
        else "unresolved"
    )
    return RepairCaseResult(
        case.instance_id,
        status,
        time.monotonic() - started,
        repair_attempts=attempts,
        total_tokens=tokens,
        trajectory=str(trajectory_path),
        error=state.failure_reason if state.failed else None,
    )


def combine_prediction_parts(output_directory: Path, cases: Sequence[SweBenchCase]) -> Path:
    """Create official predictions JSONL in stable selected-case order."""

    output = output_directory.resolve()
    destination = output / "predictions.jsonl"
    lines: list[str] = []
    for case in cases:
        path = output / "prediction-parts" / f"{case.instance_id}.json"
        if path.exists():
            lines.append(path.read_text(encoding="utf-8").strip())
        else:
            lines.append(
                json.dumps(
                    asdict(SweBenchPrediction(case.instance_id, "agentforge", "")),
                    ensure_ascii=False,
                )
            )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return destination


def _read_trajectory(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
