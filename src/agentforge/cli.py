"""Small developer CLI. Service and agent commands arrive in later sections."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from agentforge.agents.debugger import DebuggerAgent
from agentforge.agents.developer import DeveloperAgent
from agentforge.agents.explorer import EXPLORATION_ARTIFACT, RepositoryExplorerAgent
from agentforge.agents.planner import PLAN_ARTIFACT, PlannerAgent
from agentforge.agents.reviewer import REVIEW_ARTIFACT, ReviewerAgent
from agentforge.agents.tester import TesterAgent
from agentforge.configuration import credential_status, load_project_environment
from agentforge.core.approval import (
    ApprovalCapability,
    ApprovalCheckpoint,
    ApprovalRequiredError,
)
from agentforge.core.models import ActionRequest, Task
from agentforge.evaluation.comparison import (
    ComparisonError,
    compare_variants,
    load_variant_runs,
)
from agentforge.evaluation.repair import (
    BenchmarkHistoryError,
    append_repair_benchmark,
    append_repair_benchmark_suite,
    load_repair_benchmark_suites,
    load_repair_benchmarks,
    run_repair_benchmark_suite,
    run_seeded_repair_benchmark,
    summarize_repair_benchmark_suites,
    summarize_repair_benchmarks,
)
from agentforge.llm.huggingface import HuggingFaceProvider
from agentforge.orchestration.repair import RepairOrchestrator
from agentforge.orchestration.runner import MultiAgentOrchestrator
from agentforge.policy.config import PolicyConfigurationError, load_security_policy
from agentforge.policy.engine import SecurityPolicy
from agentforge.sandbox.e2b import E2BSandbox
from agentforge.sandbox.health import check_all_sandbox_health, check_sandbox_health
from agentforge.sandbox.local import LocalSandbox
from agentforge.sandbox.orphans import OrphanRegistry, reap_sbx_orphans
from agentforge.sandbox.sbx import (
    SbxSandbox,
    SubprocessSbxCommandRunner,
    _resolve_sbx_executable,
)
from agentforge.sandbox.startup import measure_sandbox_startup
from agentforge.telemetry.metrics import (
    MetricsError,
    SqliteMetricsStore,
    TokenPricing,
    load_trajectory,
    summarize_trajectory,
)
from agentforge.telemetry.trajectory import (
    JsonlTrajectoryRecorder,
    contains_potential_secret,
    redact_sensitive_text,
)
from agentforge.tools.clone import (
    RepositoryCloneError,
    RepositoryClonePolicy,
    SafeRepositoryCloner,
)
from agentforge.tools.editor import RepositoryEditor
from agentforge.tools.repository import RepositoryInspector


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentforge",
        description="AgentForge development utilities",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "credentials-check",
        help="check whether Hugging Face and E2B settings are present without showing secrets",
    )
    benchmark_parser = subparsers.add_parser(
        "benchmark-repair",
        help="run the deterministic offline repair benchmark",
    )
    benchmark_parser.add_argument(
        "--history",
        type=Path,
        default=Path("artifacts") / "repair-benchmark-history.jsonl",
        help="JSONL history path (default: artifacts/repair-benchmark-history.jsonl)",
    )
    suite_parser = subparsers.add_parser(
        "benchmark-suite",
        help="run direct, debugger-retry, and review-retry benchmark cases",
    )
    suite_parser.add_argument(
        "--history",
        type=Path,
        default=Path("artifacts") / "repair-benchmark-suite-history.jsonl",
        help="suite JSONL history path",
    )
    suite_report_parser = subparsers.add_parser(
        "benchmark-suite-report",
        help="compare the latest suite run with its predecessor case by case",
    )
    suite_report_parser.add_argument(
        "--history",
        type=Path,
        default=Path("artifacts") / "repair-benchmark-suite-history.jsonl",
        help="suite JSONL history path",
    )
    report_parser = subparsers.add_parser(
        "benchmark-report",
        help="summarize repair benchmark history and detect regressions",
    )
    report_parser.add_argument(
        "--history",
        type=Path,
        default=Path("artifacts") / "repair-benchmark-history.jsonl",
        help="JSONL history path (default: artifacts/repair-benchmark-history.jsonl)",
    )
    policy_parser = subparsers.add_parser(
        "policy-check",
        help="show whether the default policy would allow a structured command",
    )
    policy_parser.add_argument("--policy", type=Path, help="optional YAML policy file")
    policy_parser.add_argument("argv", nargs=argparse.REMAINDER)
    explore_parser = subparsers.add_parser(
        "live-explore",
        help="run Planner and Repository Explorer through Hugging Face",
    )
    explore_parser.add_argument("--workspace", type=Path, required=True)
    explore_parser.add_argument("--task", required=True)
    explore_parser.add_argument("--policy", type=Path, help="optional YAML policy file")
    explore_parser.add_argument(
        "--allow-code-transmission",
        action="store_true",
        help="confirm that selected repository excerpts may be sent to Hugging Face",
    )
    explore_parser.add_argument(
        "--trajectory",
        type=Path,
        help="optional JSONL output path (defaults below trajectories/)",
    )
    repair_parser = subparsers.add_parser(
        "live-repair",
        help="repair an imported snapshot with Hugging Face in an isolated microVM",
    )
    repair_parser.add_argument("--workspace", type=Path, required=True)
    repair_parser.add_argument("--output", type=Path, required=True)
    repair_parser.add_argument("--task", required=True)
    repair_parser.add_argument("--max-attempts", type=int, default=3)
    repair_parser.add_argument("--policy", type=Path, help="optional YAML policy file")
    repair_parser.add_argument(
        "--sandbox-backend",
        choices=("sbx", "e2b"),
        default="sbx",
        help="microVM provider (default: sbx)",
    )
    repair_parser.add_argument(
        "--e2b-template",
        help="optional E2B template name; only used with --sandbox-backend e2b",
    )
    repair_parser.add_argument(
        "--allow-code-transmission",
        action="store_true",
        help="confirm that selected repository text may be sent to Hugging Face",
    )
    repair_parser.add_argument(
        "--allow-sandbox-execution",
        action="store_true",
        help="confirm that model-selected test commands may run in a disposable microVM",
    )
    repair_parser.add_argument(
        "--trajectory",
        type=Path,
        help="optional JSONL output path (defaults below trajectories/)",
    )
    serve_parser = subparsers.add_parser(
        "serve",
        help="start the local-only AgentForge HTTP service",
    )
    serve_parser.add_argument("--port", type=int, default=8765)
    metrics_parser = subparsers.add_parser(
        "trajectory-report",
        help="summarize one JSONL trajectory and optionally save metrics to SQLite",
    )
    metrics_parser.add_argument("--trajectory", type=Path, required=True)
    metrics_parser.add_argument("--database", type=Path)
    metrics_parser.add_argument("--input-price", type=float)
    metrics_parser.add_argument("--output-price", type=float)
    health_parser = subparsers.add_parser(
        "sandbox-health",
        help="check sandbox prerequisites without creating a cloud microVM",
    )
    health_parser.add_argument(
        "--backend", choices=("all", "local", "sbx", "e2b"), default="all"
    )
    startup_parser = subparsers.add_parser(
        "sandbox-startup",
        help="measure bounded local cold starts without creating cloud resources",
    )
    startup_parser.add_argument("--iterations", type=int, default=3)
    clone_parser = subparsers.add_parser(
        "clone-snapshot",
        help="create a filtered snapshot from an explicitly approved HTTPS Git host",
    )
    clone_parser.add_argument("--url", required=True)
    clone_parser.add_argument("--output", type=Path, required=True)
    clone_parser.add_argument("--allow-host", action="append", required=True)
    clone_parser.add_argument("--allow-network-access", action="store_true")
    comparison_parser = subparsers.add_parser(
        "compare-runs",
        help="compare labelled model and policy results from a local JSONL file",
    )
    comparison_parser.add_argument("--input", type=Path, required=True)
    reaper_parser = subparsers.add_parser(
        "reap-sbx-orphans",
        help="remove only sbx microVMs recorded as owned by interrupted AgentForge runs",
    )
    reaper_parser.add_argument(
        "--registry",
        type=Path,
        default=Path("artifacts") / "sbx-orphans.json",
    )
    reaper_parser.add_argument("--allow-cleanup", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    load_project_environment()
    arguments = build_parser().parse_args(argv)
    if arguments.command == "reap-sbx-orphans":
        if not arguments.allow_cleanup:
            raise SystemExit(
                "Reaping removes exact registered microVMs. Re-run with "
                "--allow-cleanup after reviewing the registry."
            )
        executable = _resolve_sbx_executable()
        if executable is None:
            raise SystemExit("Docker Sandboxes CLI was not found")
        reaper_result = reap_sbx_orphans(
            OrphanRegistry(arguments.registry),
            executable=executable,
            runner=SubprocessSbxCommandRunner(),
        )
        print(json.dumps(asdict(reaper_result), indent=2))
        return 0 if not reaper_result.retained else 1

    if arguments.command == "sandbox-startup":
        try:
            startup_metrics = measure_sandbox_startup(
                LocalSandbox, lambda sandbox: None, iterations=arguments.iterations
            )
        except ValueError as error:
            raise SystemExit(f"Invalid startup benchmark: {error}") from None
        print(json.dumps(asdict(startup_metrics), indent=2))
        return 0

    if arguments.command == "compare-runs":
        try:
            comparison = compare_variants(load_variant_runs(arguments.input))
        except ComparisonError as error:
            raise SystemExit(f"Invalid comparison input: {error}") from None
        print(json.dumps(asdict(comparison), indent=2))
        return 0

    if arguments.command == "clone-snapshot":
        if not arguments.allow_network_access:
            raise SystemExit(
                "clone-snapshot contacts the approved Git host. Re-run with "
                "--allow-network-access after reviewing the URL and host."
            )
        try:
            clone_result = SafeRepositoryCloner(
                RepositoryClonePolicy(frozenset(arguments.allow_host))
            ).clone_snapshot(arguments.url, arguments.output)
        except (RepositoryCloneError, ValueError) as error:
            raise SystemExit(f"Repository clone failed: {error}") from None
        print(
            json.dumps(
                {
                    "host": clone_result.host,
                    "output": str(arguments.output.resolve()),
                    "files": len(clone_result.manifest.entries),
                    "total_bytes": clone_result.manifest.total_bytes,
                    "skipped_paths": len(clone_result.manifest.skipped_paths),
                },
                indent=2,
            )
        )
        return 0

    if arguments.command == "sandbox-health":
        reports = (
            check_all_sandbox_health()
            if arguments.backend == "all"
            else (check_sandbox_health(arguments.backend),)
        )
        print(json.dumps([asdict(report) for report in reports], indent=2))
        return 0 if all(report.ready for report in reports) else 2

    if arguments.command == "trajectory-report":
        supplied_prices = (arguments.input_price, arguments.output_price)
        if (supplied_prices[0] is None) != (supplied_prices[1] is None):
            raise SystemExit("Both --input-price and --output-price are required together")
        try:
            pricing = (
                None
                if supplied_prices[0] is None or supplied_prices[1] is None
                else TokenPricing(*supplied_prices)
            )
            metrics = summarize_trajectory(
                load_trajectory(arguments.trajectory), pricing=pricing
            )
            if arguments.database is not None:
                SqliteMetricsStore(arguments.database).save(metrics)
        except (MetricsError, ValueError) as error:
            raise SystemExit(f"Invalid trajectory metrics: {error}") from None
        metrics_document = asdict(metrics)
        if arguments.database is not None:
            metrics_document["database"] = str(arguments.database.resolve())
        print(json.dumps(metrics_document, indent=2))
        return 0 if metrics.succeeded else 1

    if arguments.command == "serve":
        if not 1024 <= arguments.port <= 65535:
            raise SystemExit("serve --port must be between 1024 and 65535")
        try:
            import uvicorn
        except ImportError:
            raise SystemExit(
                "The local service requires: pip install -e .[service]"
            ) from None
        from agentforge.service.app import create_app

        uvicorn.run(create_app(), host="127.0.0.1", port=arguments.port)
        return 0

    if arguments.command == "benchmark-repair":
        result = run_seeded_repair_benchmark()
        history_path = arguments.history.resolve()
        append_repair_benchmark(history_path, result)
        document = asdict(result)
        document["history"] = str(history_path)
        print(json.dumps(document, indent=2))
        return 0 if result.succeeded else 1

    if arguments.command == "benchmark-suite":
        suite_result = run_repair_benchmark_suite()
        suite_history_path = arguments.history.resolve()
        append_repair_benchmark_suite(suite_history_path, suite_result)
        suite_document = asdict(suite_result)
        suite_document["history"] = str(suite_history_path)
        print(json.dumps(suite_document, indent=2))
        return 0 if suite_result.succeeded else 1

    if arguments.command == "benchmark-suite-report":
        try:
            suite_summary = summarize_repair_benchmark_suites(
                load_repair_benchmark_suites(arguments.history)
            )
        except BenchmarkHistoryError as error:
            raise SystemExit(f"Invalid benchmark suite history: {error}") from None
        print(json.dumps(asdict(suite_summary), indent=2))
        return 1 if suite_summary.regression_detected else 0

    if arguments.command == "benchmark-report":
        try:
            summary = summarize_repair_benchmarks(
                load_repair_benchmarks(arguments.history)
            )
        except BenchmarkHistoryError as error:
            raise SystemExit(f"Invalid benchmark history: {error}") from None
        print(json.dumps(asdict(summary), indent=2))
        return 1 if summary.regression_detected else 0

    if arguments.command == "credentials-check":
        status = credential_status()
        print("AgentForge credential configuration")
        if not status.hugging_face_token:
            hugging_face_status = "MISSING (HF_TOKEN)"
        elif not status.hugging_face_token_format:
            hugging_face_status = "INVALID FORMAT (expected an hf_ user access token)"
        else:
            hugging_face_status = "CONFIGURED"
        print("- Hugging Face token: " + hugging_face_status)
        print(
            "- Hugging Face model: "
            + (
                "CONFIGURED"
                if status.hugging_face_model
                else "MISSING (AGENTFORGE_MODEL)"
            )
        )
        print(
            "- E2B Hobby key: "
            + ("CONFIGURED" if status.e2b_api_key else "MISSING (E2B_API_KEY)")
        )
        print("No secret values were displayed. No network request was made.")
        return 0 if status.ready else 2

    if arguments.command == "policy-check":
        command = list(arguments.argv)
        if command and command[0] == "--":
            command.pop(0)
        if not command:
            raise SystemExit("policy-check needs a command after --")

        request = ActionRequest(executable=command[0], arguments=tuple(command[1:]))
        policy = _load_policy(arguments.policy)
        decision = policy.evaluate(request, Path.cwd())
        if decision.allowed:
            print("ALLOWED")
            return 0
        print("DENIED")
        for reason in decision.reasons:
            print(f"- {reason}")
        return 2

    if arguments.command == "live-explore":
        if contains_potential_secret(arguments.task):
            raise SystemExit(
                "Task text appears to contain a credential; remove it before transmission"
            )
        approval = ApprovalCheckpoint(
            code_transmission=arguments.allow_code_transmission
        )
        try:
            approval.require(ApprovalCapability.CODE_TRANSMISSION)
        except ApprovalRequiredError:
            raise SystemExit(
                "live-explore sends selected repository text to Hugging Face. "
                "Re-run with --allow-code-transmission only after reviewing the workspace."
            ) from None
        workspace = arguments.workspace.resolve()
        if not workspace.is_dir():
            raise SystemExit(f"Workspace is not a directory: {workspace}")
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        trajectory_path = arguments.trajectory or (
            Path("trajectories") / f"live-explore-{timestamp}.jsonl"
        )
        recorder = JsonlTrajectoryRecorder(trajectory_path)
        policy = _load_policy(arguments.policy)
        provider = HuggingFaceProvider.from_environment()
        workflow = MultiAgentOrchestrator(
            [
                PlannerAgent(provider, recorder=recorder),
                RepositoryExplorerAgent(
                    provider,
                    RepositoryInspector(workspace, policy=policy),
                    recorder=recorder,
                ),
            ],
            recorder,
        )
        state = workflow.run(
            Task(arguments.task, repository="selected local workspace"),
            approval=approval,
        )
        if state.failed:
            print(
                json.dumps(
                    {"ok": False, "error": redact_sensitive_text(state.failure_reason or "")},
                    indent=2,
                )
            )
            return 1
        print(
            json.dumps(
                {
                    "ok": True,
                    "run_id": state.run_id,
                    "plan": asdict(state.artifacts[PLAN_ARTIFACT]),
                    "exploration": asdict(state.artifacts[EXPLORATION_ARTIFACT]),
                    "trajectory": str(trajectory_path),
                },
                indent=2,
            )
        )
        return 0

    if arguments.command == "live-repair":
        if contains_potential_secret(arguments.task):
            raise SystemExit(
                "Task text appears to contain a credential; remove it before transmission"
            )
        approval = ApprovalCheckpoint(
            code_transmission=arguments.allow_code_transmission,
            sandbox_execution=arguments.allow_sandbox_execution,
        )
        try:
            approval.require(ApprovalCapability.CODE_TRANSMISSION)
        except ApprovalRequiredError:
            raise SystemExit(
                "live-repair may send selected repository text to Hugging Face. "
                "Re-run with --allow-code-transmission only after reviewing the workspace."
            ) from None
        try:
            approval.require(ApprovalCapability.SANDBOX_EXECUTION)
        except ApprovalRequiredError:
            raise SystemExit(
                "live-repair runs model-selected, policy-approved test commands in an "
                "disposable microVM. "
                "Re-run with --allow-sandbox-execution to permit isolated execution."
            ) from None
        if arguments.max_attempts <= 0:
            raise SystemExit("--max-attempts must be greater than zero")

        workspace = arguments.workspace.resolve()
        if not workspace.is_dir():
            raise SystemExit(f"Workspace is not a directory: {workspace}")
        output = arguments.output.resolve()
        if output.exists():
            raise SystemExit(f"Output path must not already exist: {output}")

        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        trajectory_path = arguments.trajectory or (
            Path("trajectories") / f"live-repair-{timestamp}.jsonl"
        )
        recorder = JsonlTrajectoryRecorder(trajectory_path)
        policy = _load_policy(arguments.policy)
        selected_sandbox = (
            E2BSandbox(template=arguments.e2b_template)
            if arguments.sandbox_backend == "e2b"
            else SbxSandbox(
                orphan_registry=OrphanRegistry(
                    Path("artifacts") / "sbx-orphans.json"
                )
            )
        )
        with selected_sandbox as sandbox:
            if isinstance(sandbox, SbxSandbox):
                sandbox.check_available()
            imported = sandbox.import_workspace(workspace)
            sandbox.start(policy)
            inspector = RepositoryInspector(sandbox.workspace, policy=policy)
            editor = RepositoryEditor(inspector)
            provider = HuggingFaceProvider.from_environment()
            repair_workflow = RepairOrchestrator(
                planner=PlannerAgent(provider, recorder=recorder),
                explorer=RepositoryExplorerAgent(
                    provider, inspector, recorder=recorder
                ),
                developer=DeveloperAgent(provider, editor, recorder=recorder),
                tester=TesterAgent(
                    provider,
                    sandbox,
                    policy,
                    recorder=recorder,
                ),
                debugger=DebuggerAgent(provider, recorder=recorder),
                reviewer=ReviewerAgent(provider, editor, recorder=recorder),
                recorder=recorder,
                maximum_repair_attempts=arguments.max_attempts,
            )
            state = repair_workflow.run(
                Task(arguments.task, repository="isolated workspace"),
                approval=approval,
            )
            if state.failed:
                print(
                    json.dumps(
                        {
                            "ok": False,
                            "run_id": state.run_id,
                            "error": redact_sensitive_text(state.failure_reason or ""),
                            "trajectory": str(trajectory_path),
                        },
                        indent=2,
                    )
                )
                return 1
            exported = sandbox.export_workspace(output)
            review = state.artifacts[REVIEW_ARTIFACT]

        print(
            json.dumps(
                {
                    "ok": True,
                    "run_id": state.run_id,
                    "review": asdict(review),
                    "output": str(output),
                    "imported_files": len(imported.entries),
                    "skipped_import_paths": len(imported.skipped_paths),
                    "exported_files": len(exported.entries),
                    "trajectory": str(trajectory_path),
                },
                indent=2,
            )
        )
        return 0

    return 1


def _load_policy(path: Path | None) -> SecurityPolicy:
    if path is None:
        return SecurityPolicy()
    try:
        return load_security_policy(path.resolve())
    except PolicyConfigurationError as error:
        raise SystemExit(f"Invalid policy: {error}") from None
