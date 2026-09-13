# AgentForge Development Tracker

This file is the living, section-by-section record of what is planned, what is being built, and what is verified. Update it whenever behavior changes—not only when a whole milestone finishes.

Status legend: `[x]` complete, `[~]` externally blocked, `[—]` intentionally outside the agreed scope, `[ ]` not started.

Scope constraint: AgentForge is an individual, non-deployed project. E2B support must remain compatible with the free Hobby plan and must not require Pro or enterprise features.

## Section 0 — Project definition and learning material

Goal: make the purpose, boundaries, and roadmap understandable before adding complexity.

- [x] Define AgentForge as a multi-agent autonomous software engineering platform.
- [x] Make the policy engine and pluggable sandboxes the main differentiators.
- [x] Add a technical root README.
- [x] Add a continuously maintained plain-language project guide.
- [x] Add this development tracker.

Acceptance check: a newcomer can explain the task flow and why generated code needs a sandbox after reading `PROJECT_GUIDE.md`.

## Section 1 — Core runtime contracts

Goal: define stable vocabulary that later integrations can share.

- [x] Task and structured command request models.
- [x] Command execution result model.
- [x] Shared workflow state.
- [x] Agent protocol and agent result.
- [x] Sandbox protocol.
- [x] Trajectory event and recorder protocol.
- [x] Sequential multi-agent orchestration with fail-fast behavior.
- [x] Deterministic unit tests.

Acceptance check: fake agents can pass state through a complete workflow, and a failed agent prevents later agents from running.

## Section 2 — Security policy engine

Goal: deny unsafe or malformed actions before execution.

- [x] Default-deny validation for empty executable names.
- [x] Configurable forbidden executable list.
- [x] Reject shell-control characters in structured arguments.
- [x] Prevent working-directory escape from the sandbox workspace.
- [x] Return explicit policy reasons suitable for agent observations and audit logs.
- [x] Command-family rules restricting Git to read-only status and diff.
- [x] Network destination allowlist model.
- [x] Filesystem read/write policy model enforced by repository tools.
- [x] Process, CPU, and memory policy caps mapped to sandbox limits.
- [x] Output-budget policy mapping across sandbox limits.
- [x] Strict, versioned YAML policy loading and schema validation.

Acceptance check for current slice: tests prove safe structured commands pass and common escape/bypass attempts fail closed.

## Section 3 — Sandbox backends

Goal: execute allowed actions through a replaceable isolation layer.

- [x] Backend-neutral sandbox interface.
- [x] Temporary local development backend.
- [x] No implicit shell invocation.
- [x] Timeout and output-size enforcement.
- [x] Consistent stdout, stderr, exit code, timeout, and duration results.
- [x] Docker image and lifecycle manager.
- [x] Docker CPU, memory, PID, filesystem, and network-disable controls.
- [x] Temporary workspace bind mount and forced container cleanup.
- [x] Docker daemon health check and actionable unavailable-backend errors.
- [x] Install Docker Sandboxes `sbx` CLI for the current Windows user.
- [x] Mountless `sbx` microVM lifecycle with forced cleanup.
- [x] Map CPU and memory limits and add a sandbox-scoped deny-all network rule.
- [x] Copy the filtered workspace into and out of the microVM without mounting the source.
- [~] Live `sbx` integration test (implemented; blocked by Docker's Windows `docker.sock` self-connect issue).
- [—] Hostname proxy enforcement is intentionally out of personal scope; live sandboxes remain safer deny-all.
- [x] Explicit workspace import/export helpers.
- [x] E2B adapter with filtered file transfer, network control, timeouts, and cleanup.
- [x] Enforce the E2B Hobby one-hour session ceiling locally.
- [x] Live E2B integration test verified on the Hobby plan with explicit opt-in.
- [x] Sandbox health checks and bounded cold-start measurement; persistent pools are intentionally disabled for one-user Hobby scope.

Acceptance check for current slice: a trusted command runs in a temporary workspace, timeout is reported, and workspace traversal is blocked by policy.

## Section 4 — Model provider layer

Goal: keep model choice independent from orchestration.

- [x] Provider-neutral chat and tool-call types.
- [x] Hugging Face Inference Providers adapter.
- [x] Dependency-free OpenAI-compatible chat-completions adapter.
- [x] Model configuration and token loading through environment variables.
- [x] Project-local `.env` loading with process-environment precedence.
- [x] Offline credential-readiness command that never displays secret values.
- [x] Retry, rate-limit, token-usage, and structured-error handling.
- [x] Mock provider for deterministic tests.
- [x] Execute an opt-in live Hugging Face request with a fixed, repository-free prompt.
- [x] Explicit-consent, read-only live Planner/Explorer CLI entry point.
- [x] Explicit-consent full repair CLI entry point (not executed during development).

Acceptance check: the same orchestration test passes against a mock provider and can make one opt-in hosted-model request.

## Section 5 — Multi-agent graph

Goal: turn the generic runner into an iterative software-engineering team.

- [x] Generic ordered agent runner.
- [x] Planner agent with validated structured output and step limit.
- [x] Repository Explorer agent with bounded read-only tool loop.
- [x] Developer agent with validated patch-only editing.
- [x] Tester agent with policy-gated sandbox execution.
- [x] Debugger agent grounded in failed test evidence.
- [x] Reviewer agent with accept/reject decision and complete diff.
- [x] Explicit dependency-free repair state machine and bounded retry edges.
- [x] Step, iteration, and repair-attempt limits.
- [x] Human approval/checkpoint state with separate scopes and safe trajectory recording.

Acceptance check: a fixture repository flows through plan, inspect, edit, test, review, and either finishes or stops at a configured limit.

## Section 6 — Repository repair tools

Goal: solve tasks against real repositories inside the sandbox.

- [x] Safe local import and explicit-consent HTTPS clone with exact host allowlisting and filtered snapshot output.
- [x] Boundary-checked list, search, and read tools.
- [x] Exact-replacement and restricted unified-diff editing for existing text files.
- [x] Dedicated fixed pytest, Ruff, and MyPy tools routed through policy without a shell.
- [x] Policy-gated Git status and diff tools.
- [x] Final unified diff and plain-language review report.
- [x] Protection for repository boundaries, secrets, binary files, and large files.

Acceptance check: AgentForge fixes a seeded bug in a small fixture repository and returns a minimal passing diff.

## Section 7 — Repository context and memory

Goal: find useful context without placing an entire large repository in every model request.

- [x] Syntax-aware Python indexing for the project's supported language scope.
- [x] Ignore generated, secret, binary, oversized, and malformed files during retrieval.
- [x] Keyword and Python-symbol retrieval baseline.
- [x] Embedding-backed retrieval with offline hashing baseline and approval-gated OpenAI-compatible learned provider.
- [x] Run-scoped memory with deterministic budgeting and approval-gated model-assisted compaction.
- [x] Retrieval quality evaluation baseline with hit rate and mean reciprocal rank.

## Section 8 — Service and interface

Goal: expose runs safely and make progress observable.

- [x] FastAPI run creation and status endpoints for the offline benchmark suite.
- [x] Server-sent lifecycle event stream with resumable sequence cursor.
- [x] Cancellation and executor cleanup for local service runs.
- [x] Minimal local web interface for benchmark creation, timeline, status, and results.
- [x] Authentication and per-user isolation design with a remote-access implementation gate.

## Section 9 — Observability and evaluation

Goal: measure reliability rather than relying on impressive demos.

- [x] In-memory trajectory recorder.
- [x] JSONL trajectory recorder.
- [x] Local SQLite persistence for sanitized per-run metric aggregates.
- [x] Shared redaction for trajectory data, provider errors, CLI task boundaries, and returned failure text.
- [x] Metrics: success, steps, retries, latency, tokens, sandbox time, and optional cost.
- [x] Small reproducible offline repair benchmark.
- [x] Versioned local JSONL benchmark history.
- [x] Benchmark history summary and newest-run regression detection.
- [x] Multi-case repair benchmark covering direct success and debug/retry recovery.
- [x] Reviewer-rejection recovery loop and benchmark case.
- [x] Separate multi-case history and case-by-case regression report.
- [x] Model and policy comparison reports.
- [x] Strict local SWE-bench subset metadata adapter.

## Section 10 — Personal-use hardening and delivery

- [x] Threat model and abuse cases.
- [x] Host credential broker plus unconditional denial of credential-shaped sandbox environment variables.
- [x] Crash-safe exact-ID registry and opt-in orphaned-`sbx` reaper.
- [x] One-worker scheduling, bounded retained-run capacity, and Hobby session cap.
- [—] Deployment guide and CI are deliberately excluded from the non-deployed personal scope.
- [x] Reproducible offline demo script and architecture write-up; a recorded video is optional presentation material, not runtime work.

## Development log

### 2026-09-08 — Foundation slice

- Created the Python package and zero-runtime-dependency core.
- Added core task, action, execution, and workflow models.
- Added agent, sandbox, and trajectory interfaces.
- Added a configurable policy engine with initial command and path protections.
- Added a temporary local backend for trusted tests only.
- Added a sequential multi-agent orchestrator and trajectory recording.
- Added the policy-check CLI, tests, README, and layman-friendly guide.
- Verified the foundation with 14 passing automated tests and a bytecode compilation pass.
- Created the project-local `.venv` and verified all tests using its Python interpreter; no project dependencies were installed globally.

### 2026-09-08 — Docker sandbox slice

- Added policy-approved CPU, memory, process, network, and root-filesystem limits.
- Added lazy Docker health checking and disposable container create/start/exec/kill/remove lifecycle.
- Kept commands as argument arrays all the way to `docker exec`; no implicit command shell was added.
- Added fail-closed timeout handling that kills the entire container.
- Added deterministic Docker adapter tests because Docker Desktop is not present on this machine.
- Verified the expanded runtime with 20 passing tests inside the project-local virtual environment.

Next target: finish the Docker live integration test when the daemon is available, then build Section 4's mock/provider-neutral LLM contract.

### 2026-09-11 — Model provider slice

- Added vendor-neutral conversation, tool-definition, tool-call, response, and token-usage models.
- Added a dependency-free adapter for OpenAI-compatible chat-completion endpoints.
- Added Hugging Face environment configuration for `HF_TOKEN` and `AGENTFORGE_MODEL`.
- Added bounded retries for connection, rate-limit, and server failures; authentication and invalid requests fail immediately.
- Added strict parsing for malformed responses and tool arguments.
- Added a scripted mock provider so agent workflows remain deterministic and free during development.
- Verified the entire repository with 28 passing tests inside `.venv`; no hosted inference call was made.
- Installed the editable project and development tools only inside `.venv`; Ruff, MyPy strict mode, and pytest all pass.

Next target: Section 5's first real Planner and Repository Explorer agents, backed by the mock provider and trajectory events.

### 2026-09-11 — Planner and Repository Explorer slice

- Added a Planner that accepts only one validated `submit_plan` tool call and enforces a step budget.
- Added a Repository Explorer that iterates through list, search, and read tools before submitting a grounded report.
- Added shared `Plan` and `ExplorationReport` artifacts between agents.
- Added run IDs to workflow state so orchestration, model, and tool events share one trace.
- Added path traversal, ignored-directory, credential-file, binary-file, file-size, result-count, and iteration protections.
- Return rejected inspection requests to the model as observations so it can recover safely.
- Added a network-free executable demo and an end-to-end mock-provider workflow test.
- Verified 34 tests plus Ruff and MyPy strict mode inside `.venv`.

Next target: Developer and Tester agents with patch-only edits, policy-gated test execution, and a seeded repair fixture.

### 2026-09-11 — Developer and Tester slice

- Added transactional patch validation and atomic per-file replacement for existing repository text files.
- Reject ambiguous matches, unchanged replacements, oversized patches, path escapes, secrets, and sensitive files.
- Added a Developer tool loop that may read, patch, recover from rejected edits, and submit only a report matching the files actually changed.
- Added a Tester tool loop that converts structured calls into `ActionRequest` values and routes them exclusively through policy and sandbox layers.
- Require the Tester's final claim to match the latest exit code and timeout status.
- Added a seeded buggy calculator fixture and a full Planner → Explorer → Developer → Tester repair test.
- Verified 38 tests plus Ruff and MyPy strict mode inside `.venv`.

Next target: Debugger and Reviewer agents, retry edges between testing and development, and a minimal Git-style final diff.

### 2026-09-11 — Debug, retry, and review slice

- Added failed-test stdout and stderr to the machine-evidence artifact.
- Added a Debugger that must provide a structured hypothesis, evidence, and recommended changes.
- Feed the latest diagnosis into the next Developer attempt and require a genuinely new patch each cycle.
- Added `RepairOrchestrator` with bounded develop → test → debug → retry edges.
- Added an independent Reviewer that requires passing tests and a non-empty effective diff.
- Retain original file contents across retries and generate one unified original-to-final diff.
- Added tests for successful recovery from a wrong first patch and terminal retry-budget exhaustion.
- Restricted the local development backend to a small host-environment allowlist so tokens are not inherited.
- Verified 41 tests plus Ruff and MyPy strict mode inside `.venv`.

Next target: replace exact-text editing with a fuller unified-patch parser, add Git status support, then connect the repair graph to an opt-in live Hugging Face model run.

### 2026-09-11 — Unified patch, Git, and live exploration slice

- Added a restricted unified-diff parser with file headers, multiple files, multiple hunks, line-count checks, exact context verification, and CRLF preservation.
- Validate every file and hunk before writing; an invalid later file leaves earlier files unchanged.
- Exposed unified patches to the Developer while retaining exact replacements for small changes.
- Kept creation, deletion, rename, quoted-path, and final-newline semantics disabled pending dedicated policies.
- Added fixed Git status/diff tools routed through sandbox and policy layers.
- Added Git subcommand policy so mutating operations fail closed.
- Added an explicit-consent `live-explore` command; it is read-only and avoids transmitting the absolute workspace path.
- Verified 50 tests plus Ruff and MyPy strict mode inside `.venv`; no live provider request was made.

Next target: migrate the live repair backend from Docker Engine to Docker Sandboxes.

### 2026-09-11 — Isolated live-repair boundary

- Added preflight workspace snapshots with file-count, per-file, and total-size limits.
- Exclude source-control metadata, environments, caches, common credential files, private-key formats, and symbolic links from import and export.
- Record copied path, size, and SHA-256 fingerprints in workspace manifests.
- Require an absent or empty destination so transfers never overwrite existing project content.
- Added Docker import before container creation and export before sandbox cleanup.
- Initially added an opt-in Docker Engine smoke test; this was superseded by the `sbx` microVM adapter below.
- Added `live-repair` with separate consent for hosted code transmission and isolated command execution.
- Keep the source workspace unchanged and export only an accepted repair to a new output path.
- Verified 59 tests plus Ruff and MyPy strict mode inside `.venv`; one Docker test skipped because Docker is unavailable, and no hosted inference call was made.

Next target: replace the Docker Engine live-repair backend with Docker Sandboxes.

### 2026-09-11 — Docker Sandboxes (`sbx`) migration

- Installed the official Docker Sandboxes `v0.42.1` MSI for the current user; no Docker Desktop, Docker Engine, or global Python packages were installed.
- Added `SbxSandbox` as the primary live-repair backend while retaining `DockerSandbox` as a legacy adapter.
- Create a uniquely named, mountless `shell` microVM and copy only the filtered snapshot into `/home/agent/workspace`.
- Add a sandbox-scoped `**` network deny rule at creation, map CPU and memory limits, and pass commands to `sbx exec` as argument arrays without a host shell.
- Stop the entire microVM on command timeout and force-remove only that run's sandbox during cleanup.
- Download the resulting VM workspace to a temporary staging area and apply the same secret and size filters before exporting to a new destination.
- Changed `live-repair` to pre-create the `sbx` microVM before any hosted-model request, so missing login, virtualization, or template setup fails before source is transmitted.
- Added deterministic lifecycle tests and a gated real-microVM test controlled by `AGENTFORGE_RUN_SBX_TESTS=1`.
- Verified the deterministic suite plus Ruff and MyPy strict mode inside `.venv`; no hosted inference call was made.
- Confirmed virtualization support, started a healthy detached daemon with the `deny-all` preset, and completed Docker authentication as `lakshayg17`.
- Preserved the first failed daemon state as `sandboxd-stale-20260911-232448` before starting cleanly; it can be inspected or removed later.
- Ran the gated integration test. Docker `sbx v0.42.1` failed before VM creation because its Windows backend could not connect to its own internal `docker.sock`, matching Docker issue #157; `sbx ls` confirmed that no sandbox remained.
- Added explicit host-to-VM refresh before every policy-approved execution so tests always run against the Developer's latest staged edits.
- Final verification: 66 deterministic tests pass, one live `sbx` test skips by default, Ruff passes, and strict MyPy passes.
- Restored the editable build backend by installing Setuptools 84.0.0 inside `.venv` only, then refreshed the editable AgentForge installation; no Python dependency was installed globally.

Next target: retest the gated microVM flow after Docker fixes its Windows backend, then evaluate one explicitly approved hosted repair.

### 2026-09-11 — Python symbol retrieval slice

- Added a dependency-free Python AST index for classes, functions, asynchronous functions, methods, nested qualified names, source ranges, and parameter names.
- Added the Explorer's read-only `find_symbol` tool so it can locate definitions before requesting full file contents.
- Rank exact symbol names before substring matches and allow optional class/function/method filtering.
- Reuse repository boundary, ignored-directory, credential, binary, file-size, and file-count protections.
- Skip non-Python and syntactically malformed files without making the whole repository unsearchable.
- Bound both total indexed symbols and returned matches; cache each scoped index and provide explicit refresh after changes.
- Added direct index tests and a complete Planner → symbol lookup → file read → grounded Explorer report test.
- Verified 72 tests plus Ruff and strict MyPy inside `.venv`; one real-`sbx` test remains skipped by default.

Next target: add run-scoped context memory and context budgeting, then build a small retrieval-quality fixture before considering embeddings.

### 2026-09-12 — Bounded run memory and retrieval evaluation

- Added an independent `RunContextMemory` to every workflow state; memory cannot cross between runs unless a future persistence layer deliberately permits it.
- Store plans, repository observations, exploration findings, and agent summaries with source, category, priority, and insertion order.
- Deduplicate identical observations, truncate oversized entries, cap entry count, and evict the oldest low-priority entry first.
- Rank memory views by explicit priority and task-term overlap while enforcing a hard total character budget.
- Added JSON tool-observation budgeting with visible truncation metadata and trajectory events.
- Feed a bounded task-relevant memory view into the Developer while keeping validated plan and exploration artifacts authoritative.
- Added a reusable retrieval evaluator for text and Python-symbol search with top-k hit rate, mean reciprocal rank, and explicit missed queries.
- Added a small shop repository fixture and benchmark cases covering checkout totals, stock reservation, text retrieval, and misses.
- Verified 80 tests plus Ruff and strict MyPy inside `.venv`; one real-`sbx` test remains skipped by default and no hosted-model call was made.

Next target: extend context budgeting to test/debug/review evidence, add redaction rules before trajectory persistence, and then evaluate whether embeddings materially improve the retrieval benchmark.

### 2026-09-12 — Real microVM verification retry

- Re-ran the gated `tests/integration/test_sbx_live.py` flow without making any hosted-model request.
- The first attempt stopped before provisioning because an inactive daemon had left an inaccessible `containerd.sock.ttrpc` endpoint behind.
- Confirmed that no sandbox runtime process was active, then preserved the stale state as `sandboxd-stale-20260912-102302` instead of deleting it.
- Started a fresh `sbx v0.42.1` daemon and verified all 12 diagnostic checks, including Windows virtualization, daemon health, storage, version matching, socket configuration, and authentication.
- The clean retry again failed before VM creation: the backend could not connect to its own advertised internal `docker.sock`, so it could not pull `docker/sandbox-templates:shell-docker`.
- Confirmed with `sbx ls --json` that the failed attempt left zero sandboxes behind. The live verification remains blocked by Docker's Windows runtime issue rather than AgentForge's adapter.
- Tried Docker's official `sbx reset --force --preserve-secrets` workaround. The reset itself could not unlink the stale `containerd.sock.ttrpc`, and it signed the Docker account out despite the preserve-secrets option.
- Preserved the remaining partial-reset state as `sandboxd-partial-reset-20260912-104505`, reinitialized the daemon, restored authentication as `lakshayg17`, and passed all 12 diagnostic checks again.
- Re-ran the gated test after the reset and authentication. It produced the same internal `docker.sock` self-connect failure before VM creation; `sbx ls --json` again reported zero sandboxes.
- Repeated the experiment with an explicit `sbx logout --yes`, a newly initialized daemon, and a fresh device-code login as `lakshayg17`. Authentication and all 12 diagnostics passed, but image creation still failed through the internal `docker.sock`; authentication is therefore ruled out as the cause.

Next target: repeat the gated verification when Docker publishes an `sbx` build containing the Windows internal-socket fix.

### 2026-09-12 — Bounded repair evidence and trajectory redaction

- Added separate hard context allowances for Tester, Debugger, and Reviewer inputs.
- Bound test-command observations returned to the model and emit visible `tool_observation_truncated` metadata when output is shortened.
- Store full test evidence and structured diagnoses as high-priority, per-run memory; the memory layer still applies its independent entry-size and eviction limits.
- Include task-relevant run memory in testing and debugging without allowing it to replace authoritative artifacts.
- Make final review fail closed when the complete diff and machine evidence cannot fit the configured allowance, rather than asking a model to approve a partial patch.
- Recursively redact sensitive trajectory fields, inline token assignments, bearer credentials, and private-key blocks at both standard recorder boundaries while preserving ordinary metrics such as `total_tokens`.
- Added focused tests for bounded test output, bounded debugger evidence, reviewer refusal, and in-memory/JSONL credential redaction.
- Verified 84 tests inside `.venv`; one real-`sbx` integration test skips by default. Ruff and strict MyPy both pass, and no hosted-model call was made.

Next target: evaluate an embedding-backed retrieval implementation against the existing deterministic benchmark, and add a second production sandbox backend while the Windows `sbx` defect remains upstream.

### 2026-09-12 — Provider-neutral embedding retrieval experiment

- Added an `EmbeddingProvider` contract that returns fixed-width vectors without coupling repository retrieval to one model or service.
- Added a dependency-free `HashingEmbeddingProvider` that deterministically splits natural-language words, snake_case names, and camelCase names into normalized lexical vectors.
- Added a bounded `RepositoryEmbeddingIndex` that uses the existing repository visibility, credential, file-count, and file-size protections before ranking files by cosine similarity.
- Cache one indexed scope at a time, expose explicit refresh, validate provider vector counts and dimensions, and return only positive-scoring bounded results.
- Added an optional Explorer `semantic_search` tool. It is absent from the model's tool list unless an embedding index is deliberately configured.
- Extended the retrieval evaluator with embedding mode and side-by-side text-versus-vector hit-rate and mean-reciprocal-rank changes.
- On three paraphrased fixture queries at top one, exact whole-phrase retrieval scored 0% and the offline vector baseline scored 100%. This is a controlled lexical-overlap result, not a claim of learned semantic understanding.
- Added four focused tests covering vector stability and normalization, file ranking, comparative metrics, and Explorer integration.
- Verified 88 tests inside `.venv`; one real-`sbx` integration test skips by default. Ruff and strict MyPy both pass, and no package or hosted-model call was required.

Next target: implement a provider-neutral second production sandbox adapter with deterministic lifecycle tests, then evaluate a learned embedding provider on a larger repository benchmark before enabling it by default.

### 2026-09-12 — E2B hosted microVM fallback

- Added `E2BSandbox` as a second production isolation backend while keeping `sbx` as the CLI default.
- Added a narrow client/factory seam so lifecycle, transfer, timeout, quoting, and cleanup behavior is tested without spending cloud credits.
- Lazily load the optional E2B SDK, which is pinned as the `e2b` project extra and installed only inside `.venv`.
- Create E2B sandboxes in secure mode, deny guest internet access by default, and support an optional provider template.
- Upload only AgentForge's already-filtered temporary snapshot, one file at a time, rather than mounting or transmitting the original directory wholesale.
- Convert structured argument arrays with POSIX quoting and pass environment values through the SDK's separate environment channel.
- Destroy the entire hosted VM after an action timeout and on normal close; cleanup is idempotent.
- Download only regular files under the fixed remote workspace, reject paths outside that root, and apply the same secret and size filters before export.
- Added `--sandbox-backend {sbx,e2b}` and `--e2b-template` to `live-repair`.
- Documented the trust and billing difference: E2B uploads the filtered snapshot to a third party and can consume credits.
- Documented the enforcement gap honestly: E2B's creation API accepts network and lifetime controls, while CPU, memory, and process sizing comes from the selected provider template.
- Added a gated real-cloud round-trip test controlled by `AGENTFORGE_RUN_E2B_TESTS=1` and `E2B_API_KEY`; it remains skipped unless deliberately enabled.
- Final verification: 95 tests pass, the real `sbx` and E2B tests skip by default, Ruff passes, and strict MyPy passes.

Next target: run the E2B smoke test with an explicitly supplied account key, then add a human approval/checkpoint state before enabling broader live repairs.

### 2026-09-12 — Project-local environment loading

- Added automatic `.env` loading at CLI startup from the directory where AgentForge is launched.
- Existing process environment variables take precedence over values in the file.
- Added `python-dotenv` as a small runtime dependency and installed it only inside `.venv`.
- Kept `.env` excluded from version control; implementation and verification never print secret values.
- Added tests for file loading and environment precedence.
- Final verification: 98 tests pass, two live microVM tests skip by default, Ruff passes, and strict MyPy passes.

Next target: perform deliberately approved Hugging Face and E2B smoke tests, then add a human approval/checkpoint state.

### 2026-09-12 — Individual-project and Hobby-plan scope

- Fixed the product scope as a personal learning/development tool rather than a deployed multi-user service.
- Made E2B Hobby a permanent design boundary; Pro and enterprise capabilities are not roadmap requirements.
- Added a local validation rule that rejects E2B lifetimes above the Hobby plan's one-hour ceiling.
- Kept the operational default at one sequential sandbox with a five-minute lifetime, well inside the plan limits.

Next target: complete the explicitly opted-in E2B smoke test, then continue improving the personal local workflow without adding deployment infrastructure.

### 2026-09-12 — Offline credential readiness check

- Added `agentforge credentials-check` to verify the presence of `HF_TOKEN`, `AGENTFORGE_MODEL`, and `E2B_API_KEY` after `.env` loading.
- Report only configuration and safe token-format status or the missing variable name; secret values are never returned.
- Make no Hugging Face, E2B, or other network request during the check.
- Return exit code 0 when all three settings are present and 2 when setup is incomplete, making the result script-friendly.
- Added tests covering successful readiness, missing settings, and non-disclosure of supplied test secrets.
- Final verification: 101 tests pass, two live microVM tests skip by default, Ruff passes, and strict MyPy passes.

Next target: populate the project-root `.env`, rerun the offline check, and then perform only an explicitly approved live smoke test.

### 2026-09-12 — Minimal Hugging Face smoke test

- Added a gated integration test that sends one fixed harmless prompt and no repository content.
- Disable request retries in the smoke test so one opt-in run makes at most one inference request.
- Confirmed all three required `.env` settings are present without displaying their values.
- Reached the Hugging Face router, which rejected authentication with HTTP 401 before inference.
- Safely confirmed that the configured `HF_TOKEN` lacks the standard `hf_` user-access-token prefix; no secret characters were displayed.
- Improved `credentials-check` to distinguish presence from valid Hugging Face token shape.

Next target: replace `HF_TOKEN` with the complete `hf_...` fine-grained inference token, rerun the offline check, and retry the one-request smoke test.

### 2026-09-12 — Capability-scoped human approval checkpoint

- Added an immutable approval checkpoint with separate code-transmission and sandbox-execution capabilities.
- Replaced scattered live-command boolean checks with the shared fail-closed gate.
- Preserved the existing explicit CLI flags and actionable refusal messages.
- Proved that granting transmission does not grant execution, an empty checkpoint denies both operations, and both can be deliberately approved.
- Final verification: 105 tests pass, three live tests skip by default, Ruff passes, and strict MyPy passes.

Next target: persist the granted capability names in the run trajectory without recording tokens or other secret values.

### 2026-09-12 — Verified E2B Hobby round trip

- Updated the gated E2B test to use the same project-root `.env` loader as the CLI.
- Created a real disposable E2B Hobby microVM and uploaded only a temporary `message.txt` fixture.
- Successfully ran a structured `cat message.txt` request inside the VM with guest networking disabled.
- Found that E2B SDK 2.49.1 returns downloaded binary files as `bytearray` despite its bytes-oriented API surface.
- Added a narrow conversion to backend-neutral `bytes`, retaining rejection of non-binary responses.
- Re-ran the complete upload, execution, download, export, verification, and cleanup path successfully.
- Confirmed the normal offline suite still has 102 passing tests; Ruff and strict MyPy pass.

Next target: correct the Hugging Face token and complete its one-request smoke test. E2B is now live verified and needs no paid-tier upgrade.

### 2026-09-12 — Approval trajectory persistence

- Added the immutable approval checkpoint to workflow state for both sequential and repair orchestrators.
- Record `human_approval_recorded` immediately after workflow start when the user supplied an explicit checkpoint.
- Persist only granted capability names, never tokens, API keys, `.env` values, or credential metadata.
- Keep mock and offline workflows backward compatible with an empty default checkpoint and no invented approval event.
- Added a focused test proving the state and trajectory contain only the granted `code_transmission` label.
- Final verification: 106 tests pass, three live tests skip by default, Ruff passes, and strict MyPy passes.

Next target: correct the Hugging Face token and complete its one-request smoke test, then choose the next personal-workflow improvement.

### 2026-09-12 — Verified Hugging Face inference

- Confirmed the corrected fine-grained `hf_` token, configured model, and E2B key are present without displaying their values.
- Ran the gated Hugging Face integration test with retries disabled.
- Sent only the fixed smoke-test sentence; no repository path, source file, trajectory, or E2B content was transmitted.
- Successfully verified authentication, model routing, HTTPS transport, chat-completion parsing, and a non-empty model response.
- Kept the integration test opt-in so the normal suite remains offline and does not consume inference credits.

Next target: add one small reproducible repair benchmark using the fully offline mock workflow before considering a deliberately approved live repository experiment.

### 2026-09-12 — Reproducible offline repair benchmark

- Added `agentforge benchmark-repair` as a deterministic, credit-free end-to-end health check.
- Generate a temporary broken parity implementation and executable specification for every run.
- Run Planner, Explorer, Developer, Tester, and Reviewer through the real repair orchestrator using fixed mock-model tool calls.
- Execute the repaired specification through `LocalSandbox` and the normal security policy.
- Report machine-readable success, test status, review verdict, repair attempts, completed agent steps, trajectory events, and duration.
- Verified a successful one-attempt repair with five completed agent steps and 36 recorded events.
- Final verification: 108 tests pass, three external integration tests skip by default, Ruff passes, and strict MyPy passes.

Next target: add a small benchmark history writer so personal runs can be compared over time without requiring a database.

### 2026-09-12 — Local benchmark history

- Added a schema-versioned benchmark record with a UTC timestamp and the complete non-secret result.
- Append each `agentforge benchmark-repair` run to `artifacts/repair-benchmark-history.jsonl` by default.
- Added `--history PATH` so a personal comparison file can be stored elsewhere.
- Keep history independent from hosted models, E2B, repository contents, prompts, and credentials.
- Created the first local record: successful tests and review in one repair attempt, five completed agent steps, and 36 trajectory events.
- Added tests for directory creation, JSON structure, append behavior, and CLI reporting.
- Final verification: 109 tests pass, three external integration tests skip by default, Ruff passes, and strict MyPy passes.

Next target: add a compact command that summarizes benchmark history and highlights regressions between the newest two runs.

### 2026-09-12 — Benchmark regression report

- Added strict loading and schema validation for every JSONL benchmark-history record.
- Added `agentforge benchmark-report` with record count, successful runs, success rate, average/latest duration, and newest-run timing change.
- Detect functional regressions when workflow success, tests, or review changes from pass to fail, or when repair attempts increase.
- Keep timing noise separate: warn only when the latest run is over 0.1 seconds and 50% slower than the previous run.
- Return exit code 1 for a functional regression and 0 for a clean comparison, making the report usable from personal scripts.
- Added tests for valid history, append/load round trips, malformed line reporting, functional regressions, performance warnings, and CLI output.
- Created a second real offline history entry; both runs succeeded in one attempt with five agent steps and 36 trajectory events, with no regression detected.
- Final verification: 126 tests pass, three external tests skip by default, Ruff passes, and strict MyPy passes.

Next target: expand the repair benchmark from one parity fixture to a small multi-case suite that exercises debugging and retry behavior.

### 2026-09-12 — Configurable policy layer

- Added `maximum_output_bytes` to backend-neutral sandbox limits and enforce the policy cap before Local, Docker, `sbx`, or E2B execution.
- Added separate filesystem read and write allowlists plus always-denied path patterns.
- Enforce filesystem policy during repository listing, direct reads, exact replacements, and unified-diff editing.
- Added exact-host network destination evaluation that rejects malformed and lookalike hostnames.
- Kept actual sandbox network access deny-all by default; hostname-level enforcement still requires a controlled proxy.
- Added a strict version-1 YAML schema covering commands, Git, environment keys, resources, output, filesystem, and network rules.
- Use safe YAML loading and reject unknown fields, unsupported versions, unsafe tags, wrong types, empty strings, and non-positive limits.
- Added `--policy` support to `policy-check`, `live-explore`, and `live-repair`, plus `agentforge-policy.example.yaml`.
- Installed PyYAML and its MyPy stubs only inside `.venv`.
- Final verification: 122 tests pass, three external tests skip by default, the example policy passes the CLI check, Ruff passes, and strict MyPy passes.

Next target: add the compact benchmark-history regression report, then expand the offline repair benchmark beyond one parity case.

### 2026-09-12 — Multi-case repair and retry benchmark

- Added `agentforge benchmark-suite` as a fully offline aggregate health check.
- Retained the original direct parity repair and gave every result a stable case name.
- Added a forced-failure case that applies an invalid first patch, observes real failing tests, creates a debugger diagnosis, schedules a second development attempt, passes the retest, and completes review.
- Report case count, passed cases, total repair attempts, total duration, and every individual case result as JSON.
- Kept existing schema-version-1 benchmark history readable by defaulting older records to the direct case name.
- Added focused coverage for the retry case, suite aggregation, CLI output, and legacy history compatibility.
- Final verification: both benchmark cases pass in three total repair attempts; 130 offline tests pass, three external integrations skip by default, Ruff passes, and strict MyPy passes.

Next target: persist suite runs separately and compare results case by case, without mixing their metrics into the original single-case history.

### 2026-09-12 — Case-by-case suite history

- Save `benchmark-suite` results to a dedicated versioned JSONL file, separate from the original direct-repair history.
- Added `--history PATH` for alternate personal storage locations.
- Added strict suite-history loading that validates timestamps, result types, unique case names, and consistency between aggregate totals and individual cases.
- Added `agentforge benchmark-suite-report` to compare the latest two runs by stable case name.
- Detect a missing case, newly failed workflow, newly failed tests, newly rejected review, or increased repair attempts and identify the affected case in JSON output.
- Added focused round-trip, clean-summary, regression, and CLI-report tests.
- Created the first real suite-history record; both cases passed and the report found no regression.
- Final verification: 133 tests pass, three external integrations skip by default, Ruff passes, and strict MyPy passes inside `.venv`.

Next target: add a small third benchmark case that exercises reviewer rejection and controlled repair, then keep the suite bounded for Hobby-plan development.

### 2026-09-12 — Reviewer-feedback repair loop

- Changed the repair state machine so a rejected review can schedule another bounded repair attempt instead of ending immediately.
- Preserve the review summary and concrete issues in run-scoped memory for the next Developer attempt.
- Added explicit `review_rejected` and reason-tagged `repair_retry_scheduled` trajectory events.
- The configured maximum-repair-attempt limit still applies, so repeated rejection fails closed rather than looping indefinitely.
- Added `parity_review_retry`, which passes tests, receives a deliberate rejection, applies the feedback, retests, and gains final approval.
- Expanded the offline suite to three cases and five total expected repair attempts.
- Added direct state-machine tests proving review feedback reaches the next Developer attempt and retry-budget exhaustion fails closed.
- Recorded two consecutive three-case baselines; all six case executions passed and the latest comparison reports no regression.
- Final verification: 136 tests pass, three external integrations skip by default, Ruff passes, and strict MyPy passes inside `.venv`.

Next target: expose structured workflow status through the first local-only service endpoint, without adding deployment infrastructure.

### 2026-09-12 — Local observable service

- Added a FastAPI application with health, run creation, run status, cancellation, and server-sent event endpoints.
- Restricted creation to the deterministic offline benchmark suite; arbitrary live tasks, repository paths, model calls, and sandbox execution are not accepted through this first boundary.
- Added a thread-safe in-memory run manager with one worker, at most 100 retained records, terminal-record pruning, and HTTP 429 capacity responses.
- Expose queued, running, succeeded, failed, and cancelled states with UTC timestamps and monotonic event sequence numbers.
- Make running cancellation truthful: `cancel_requested` appears immediately, while terminal `cancelled` is recorded only after work reaches a safe boundary.
- Sanitize worker failures to exception type instead of returning arbitrary exception text through the API.
- Added resumable SSE output through `?after=SEQUENCE`, with disconnect detection and no-buffer headers.
- Added `agentforge serve`, fixed to the loopback address, with a validated non-privileged port.
- Installed FastAPI, Uvicorn, Pydantic, Starlette, and their dependencies only inside `.venv`; HTTPX remains a development-only test dependency.
- Added service and CLI coverage, including a real three-case offline benchmark executed through the API boundary.
- Verified a real Uvicorn process on `127.0.0.1`: health returned local-only, run creation returned HTTP 202, and all three benchmark cases completed successfully through status polling.
- Distinguish infrastructure exceptions and unsuccessful workflow results while retaining the structured result for diagnosis.
- Final verification: 147 tests pass without warnings, three external integrations skip by default, Ruff passes, and strict MyPy passes inside `.venv`.

Next target: add a minimal local dashboard for run creation, status, event timeline, and benchmark results without enabling remote access.

### 2026-09-12 — Local dashboard and access-boundary design

- Added a responsive, dependency-free dashboard for health, run creation, cancellation, status, timestamps, lifecycle events, aggregate metrics, and case results.
- Serve all HTML, CSS, and JavaScript locally with no CDN, analytics, fonts, or other remote browser dependencies.
- Added restrictive content-security, framing, MIME-sniffing, referrer, and browser-permission headers.
- Reject non-loopback Host values and state-changing requests carrying a non-loopback Origin.
- Disabled CDN-backed interactive API documentation; retain the local OpenAPI JSON endpoint.
- Added `SECURITY_MODEL.md` defining the current one-person boundary and mandatory identity, session, ownership, sandbox, credential-broker, quota, audit, retention, and cleanup controls for any future shared mode.
- Keep remote binding unavailable: the CLI has a port option but deliberately has no host option.
- Added dashboard asset, security-header, hostile-host, and hostile-origin tests.
- Final verification: 149 tests pass without warnings, three external integrations skip by default, Ruff passes, and strict MyPy passes inside `.venv`.

Section 8 is complete for the individual, non-deployed scope. Next target: complete the remaining observability metrics for offline and live runs before considering a broader task dashboard.

### 2026-09-12 — Personal-scope roadmap completion

- Added per-agent token accounting, strict trajectory summaries, optional caller-supplied cost estimates, and sanitized SQLite metric upserts.
- Added honest sandbox readiness reports and bounded cold-start measurement without keeping paid or persistent pools alive.
- Added explicit-consent, exact-host HTTPS repository cloning with shallow, no-tag, no-file-protocol Git operation followed by the normal filtered snapshot boundary.
- Added fixed pytest, Ruff, and MyPy quality tools that pass structured arguments through policy without invoking a shell.
- Added approval-gated model-assisted memory compaction that retains original entries whenever summarization fails or returns invalid output.
- Added an approval-gated OpenAI-compatible learned embedding adapter with strict vector validation, while retaining the offline hashing default.
- Added strict local SWE-bench subset metadata loading and local JSONL model/policy comparison reports.
- Extended redaction across provider errors, task transmission checks, trajectory records, and returned CLI failure text.
- Added a host-only credential broker; credential-shaped environment variables are denied to guest commands even when mistakenly allowlisted.
- Added an exact-ID `sbx` ownership registry and opt-in orphan reaper. Failed cleanup remains registered; wildcard and unrecognized targets are rejected.
- Added `ARCHITECTURE.md`, `THREAT_MODEL.md`, and a reproducible offline personal demo script.
- Reconciled the roadmap with the agreed one-person Hobby scope. A controlled network proxy, public deployment, CI delivery, multi-user access, and persistent sandbox pooling are explicit non-goals rather than hidden unfinished work.
- Ran the complete offline demo successfully: all three repair cases passed in five total attempts, credentials were checked without disclosure, and backend readiness was reported without creating a cloud VM.
- Final verification: 192 tests pass, three external integration tests skip by default, 17 subtests pass, Ruff passes, and strict MyPy passes across 69 source files.

The personal, non-deployed AgentForge roadmap is complete. The sole external integration limitation is the installed Docker `sbx` Windows runtime's upstream internal-socket failure; the already verified E2B Hobby backend remains the usable live microVM path.

### 2026-09-13 — Final external-boundary audit

- Audited every tracker marker. The only in-scope non-complete marker remains the real Windows `sbx` integration.
- Confirmed from Docker's official release channel that `v0.42.1` is still the latest stable release; `v0.43.0-rc3` does not list a fix for the open Windows self-connect defect, so the stable installation was not replaced with an unrelated prerelease.
- Retried the real integration. An old `containerd.sock.ttrpc` state first prevented daemon startup.
- A broad destructive `sbx reset` was rejected. Verified that no `sbx` process was running and preserved the exact state directory as `sandboxd-stale-20260913-0110` instead of deleting it.
- The cleanly rebuilt daemon then reproduced Docker issue #157 exactly: its image pull cannot connect back to its own Windows `docker.sock`. `sbx ls --json` confirmed zero sandboxes after the failure.
- Added a two-file synthetic repair fixture so the complete Hugging Face + E2B path is ready for a minimal live check without sharing the AgentForge repository.
- The hosted check was not run because sending even synthetic source to external providers requires the user's specific informed authorization; command-line consent flags are not a substitute for that authorization.

No AgentForge implementation work remains for the agreed scope. External completion now depends on either a Docker release fixing the Windows runtime or explicit user authorization for the prepared synthetic Hugging Face + E2B verification.

### 2026-09-13 — Verified complete hosted repair

- Received explicit authorization to send only the two-file synthetic calculator fixture to Hugging Face and one disposable E2B Hobby microVM.
- The first live run connected successfully but exposed a real workflow issue: Qwen repeatedly inspected already-read files until the Explorer exhausted all eight turns.
- Made the Explorer's final bounded turn offer only `finish_exploration`, with a direct finalization instruction and a recorded `exploration_finalization_required` event.
- Added a regression test proving the last turn cannot advertise more inspection work.
- Retried the same authorized fixture. Planner, Explorer, Developer, Tester, and Reviewer completed successfully in one repair attempt.
- E2B ran both unit tests successfully, the Reviewer accepted the exact `left - right` to `left + right` change, and two filtered files were exported to `artifacts/live-smoke-repaired`.
- Independently reran both exported tests locally; both passed.
- Saved the successful sanitized trajectory to `trajectories/live-smoke-e2b-hf-retry.jsonl` and its aggregate metrics to `artifacts/metrics.sqlite3`: five agent steps, one repair attempt, 13 model requests, 17,050 tokens, and 34.37 seconds.
- Final verification: 193 tests pass, three opt-in external tests skip by default, 17 subtests pass, Ruff passes, and strict MyPy passes across 69 source files.

The entire Hugging Face + E2B Hobby repair path is now live verified. Docker `sbx` remains only an optional local backend blocked by Docker's open Windows runtime defect; it is no longer a blocker for AgentForge's completed personal workflow.

### 2026-09-13 — Repeated hosted reliability check

- Repeated the same explicitly authorized synthetic Hugging Face + E2B repair without changing or overwriting the first successful artifacts.
- The workflow again completed in one repair attempt: five agent steps, one test run, no retries or review rejection, and the same minimal `left - right` to `left + right` diff.
- Exported the second result to `artifacts/live-smoke-repaired-repeat` and independently reran both unit tests locally; both passed.
- Stored its sanitized trajectory at `trajectories/live-smoke-e2b-hf-repeat.jsonl` and its aggregate in `artifacts/metrics.sqlite3`.
- The repeat used 19,755 reported model tokens across 14 requests and took 226.40 seconds, showing provider latency variability while preserving functional reliability.

### 2026-09-13 — AgentForge Studio frontend

- Rebuilt the minimal benchmark page as a complete local visual control center without adding a browser dependency or external asset.
- Added a responsive hero architecture orbit and project capability summary.
- Added selectable cards for all six specialist agents, each explaining its tools, required evidence, output artifact, and permission boundary.
- Added an animated query walkthrough that demonstrates the test-failure → debugger → developer retry branch.
- Added capability cards for retrieval, editing, quality execution, observability, sandbox isolation, and evaluation.
- Preserved the real local benchmark controls, cancellation, resumable server-sent lifecycle events, metrics, and three per-case outcomes in a redesigned Live Lab.
- Added a layered safety visualization for approval, repository filtering, policy, microVM execution, review, and export.
- Kept the original loopback-only access controls, restrictive browser headers, self-contained CSS/JavaScript, and no-CDN rule.
- Verified the JavaScript syntax with Node, all service tests, and headless Edge renders at desktop and full-page sizes.

### 2026-09-13 — Private GitHub repository and round-trip verification

- Initialized the project as a Git repository on the `main` branch and created the first release commit.
- Added the MIT license declared by the Python package metadata.
- Verified that `.env`, `.venv`, generated artifacts, and trajectories are excluded from version control.
- Scanned the publishable files for common API-token patterns without printing secret values; none were found.
- Created the private `lakshaygupta17/AgentForge` repository and pushed `main` over HTTPS.
- Cloned the private repository into a fresh ignored artifact directory to test the real GitHub transport and authentication path.
- Ran the complete test suite from that fresh clone with the original project-local virtual environment: 193 tests passed, three opt-in external tests skipped, and 17 subtests passed.

The local workspace and private GitHub repository are now connected. Normal development can proceed through small commits on `main` or feature branches, while credentials and generated run data remain local.

### 2026-09-13 — Explorer finalization recovery

- Diagnosed the first repair launched from the fresh GitHub clone using its sanitized trajectory.
- Confirmed that GitHub cloning, planning, and model inference succeeded; on Explorer iteration eight, the hosted model invented the Tester's `run_tests` tool even though AgentForge offered only `finish_exploration`.
- Preserved the role boundary: an unadvertised finalization tool is recorded and rejected without execution.
- Added exactly one correction-only model turn that again exposes only `finish_exploration` and explains the protocol error.
- Kept the workflow bounded and fail-closed: a missing or invalid report on the correction retry ends exploration without modifying or exporting the repository.
- Added regression tests for successful correction and repeated-invalid-tool failure.
- Final offline verification: 195 tests pass, three opt-in external tests skip, 17 subtests pass, Ruff passes, and strict MyPy passes across 69 source files.

### 2026-09-13 — Real-time agent visualizer

- Connected the benchmark trajectory recorder to the local service through an optional sanitized event observer.
- Stream real orchestrator events through the existing server-sent event endpoint as `agent_progress` records; no frontend timer invents agent activity.
- Enriched each specialist's completion event with its safe structured output: plan, exploration findings, changed files, test evidence, diagnosis, or review verdict.
- Added a six-stage live rail showing active, complete, failed, waiting, and skipped roles for the current benchmark case.
- Added per-agent output histories that retain results across direct repair, debugger retry, and reviewer retry cases.
- Added cursor-based EventSource reconnection so the browser resumes after the last received sequence instead of duplicating the timeline.
- Kept output rendering text-only, passed events through trajectory redaction before publication, and avoided displaying the absolute Python executable path.
- Added observer-redaction, benchmark-progress, service-stream, and dashboard-asset regression tests.
- Visually verified an in-flight Tester stage and the completed three-case output histories in the local browser.
- Final verification: 198 tests pass, three opt-in external tests skip, 17 subtests pass, JavaScript syntax passes, Ruff passes, and strict MyPy passes across 69 source files.

### 2026-09-13 — Multi-file checkout repair challenge

- Added a six-file synthetic checkout fixture with an issue-style `TASK.md`, four implementation modules, and eight dependency-free acceptance tests.
- Covered per-line half-up discount rounding, taxable versus exempt merchandise, discounted free-shipping eligibility, empty baskets, and invalid inputs.
- Proved the acceptance contract first against the correct reference implementation: all eight tests passed.
- Seeded three independent defects across the discount, tax, and checkout modules. The final challenge passes four tests and fails four for the intended reasons.
- Kept the fixture fully synthetic and dependency-free so it is suitable for the personal E2B Hobby workflow without exposing user code. No hosted inference or microVM run was performed as part of fixture creation.

### 2026-09-13 — Developer patch recovery hardening

- Diagnosed the first hosted checkout attempt from its sanitized trajectory: E2B and Hugging Face worked, but all five Developer edits were rejected before any source changed.
- Confirmed the fresh Windows Git clone used CRLF while the source fixture and typical model patches used LF.
- Made exact replacements newline-tolerant for uniformly styled LF, CRLF, and legacy CR files while preserving the file's original style; mixed-newline files retain strict matching.
- Added explicit Developer guidance to trace imported helpers, reread after rejection, and prefer small exact replacements over hand-counted unified hunks.
- Added one bounded correction-only patch turn after rejected edits and a report-only finalization turn after an accepted last-turn patch. Invalid correction or finalization calls still fail closed.
- Strengthened Explorer guidance to include every inspected helper module that may need a change rather than reporting only the coordinating file.
- Added regression coverage for LF-to-CRLF and CRLF-to-LF replacements, successful rejected-patch recovery, report-only finalization, and failure after one invalid correction.
- Final offline verification: 203 tests pass, three external integrations skip by default, 17 subtests pass, Ruff passes, and strict MyPy passes across 69 source files. No hosted provider or microVM was contacted during this change.
