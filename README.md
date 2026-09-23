# AgentForge

Measured live repair results and qualified resume wording are recorded in
[BENCHMARK_RESULTS.md](BENCHMARK_RESULTS.md).

AgentForge is a security-focused, multi-agent software engineering runtime. Its eventual job is to accept a repository and a task, let specialist AI agents inspect and change the code, run the result in an isolated sandbox, and return a tested patch with a complete audit trail.

This is intentionally an individual learning and development project, not a deployment product or hosted service. Its E2B integration targets the free Hobby plan only; the architecture does not assume a future paid E2B tier.

The distinguishing idea is simple: **an AI request never goes straight to a machine**. Every tool action must pass through a policy engine before a replaceable sandbox backend can execute it.

> Project status: complete mock-driven repair loop plus an opt-in live repair entry point. Live repair copies a filtered repository snapshot into either a local Docker Sandboxes (`sbx`) microVM or an E2B-hosted microVM, runs policy-approved tests there, and exports an accepted result to a new directory without changing the source workspace.

## Start here

- [PROJECT_GUIDE.md](PROJECT_GUIDE.md) explains the project in everyday language and introduces each concept as it appears.
- [DEVELOPMENT.md](DEVELOPMENT.md) is the section-by-section build tracker and roadmap.
- `src/agentforge/` contains the runtime.
- `tests/` contains executable examples of the expected behavior.

## Current architecture

```text
Task
  |
  v
MultiAgentOrchestrator ---> specialist agents (planner, explorer, coder, ...)
  |                         |            |
  |                         v            v
  |                    LLMProvider   structured tool request
  |                  /            \         |
  |          Hugging Face        Mock        |
  |                                      |
  v                                      v
TrajectoryRecorder              SecurityPolicyEngine
                                         |
                                  allow or reject
                                         |
                                         v
                                  Sandbox interface
                           /        |          |             \
             Local (trusted)   sbx microVM   E2B cloud   Docker Engine (legacy)
```

The interfaces are intentionally small. LangGraph, additional model providers, FastAPI, and a database can be added later without coupling the core rules to one vendor.

## Run it

AgentForge requires Python 3.11+, `python-dotenv` for local configuration, and PyYAML for strict policy files. E2B support is an optional extra. All packages belong in the project-local virtual environment.

Create and activate a project-local virtual environment before installing anything:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

After activation, `python` and `pip` point inside `.venv`, keeping project packages out of the global Python installation. The `.venv` directory is excluded from version control.

To use the E2B backend, install its SDK into that active environment only:

```powershell
python -m pip install -e ".[e2b]"
```

```bash
python -m unittest discover -s tests -v
```

Check whether a command would pass the default policy:

```bash
PYTHONPATH=src python -m agentforge policy-check -- python -m unittest
```

On PowerShell, use:

```powershell
$env:PYTHONPATH = "src"
python -m agentforge policy-check -- python -m unittest
```

Use the documented policy template to test custom rules:

```powershell
agentforge policy-check `
  --policy .\agentforge-policy.example.yaml `
  -- python -m pytest
```

The versioned YAML schema configures forbidden commands, environment keys, Git subcommands, timeouts, CPU, memory, processes, output bytes, filesystem read/write patterns, denied paths, and network hostnames. Unknown fields and wrong types fail closed instead of being ignored. `live-explore` and `live-repair` also accept `--policy`.

Filesystem patterns are enforced by repository listing, reading, and editing. Output budgets are carried in `SandboxLimits` and checked before every backend executes. Network destination allowlists can be validated today, but guest networking stays deny-all by default because the current backends cannot enforce hostname-level rules without a controlled proxy.

The `LocalSandbox` exists for development and tests. It restricts the working directory and never invokes a shell, but it is **not a security boundary**. Untrusted code must eventually run through the Docker or E2B backend.

## Docker Sandboxes (`sbx`)

`SbxSandbox` is the primary isolation backend. It uses Docker's `sbx` CLI to create a disposable Linux microVM without requiring Docker Desktop or Docker Engine. AgentForge creates it without a host workspace mount, then copies in only the filtered temporary snapshot.

- outbound network denied for the individual sandbox;
- no raw model credentials or host environment inherited by commands;
- one CPU and 512 MB memory by default;
- the original repository never mounted into the microVM;
- the whole microVM stopped when an execution times out;
- forced removal of the uniquely named sandbox when the run closes.

The agent has full control inside the disposable VM, including a writable VM filesystem and a private Docker Engine. The security boundary is the microVM and its restricted access to the host—not a read-only guest filesystem. The older `DockerSandbox` adapter remains as a tested legacy backend, but `live-repair` now selects `SbxSandbox`.

Workspace import skips virtual environments, source-control metadata, caches, common credential files, private-key formats, and symbolic links. It applies file-count and byte limits before populating the sandbox and records a SHA-256 manifest of copied files. Export uses the same rules and refuses to overwrite a non-empty destination.

Docker Sandboxes `v0.42.1` is installed for the current Windows user under `%LOCALAPPDATA%\DockerSandboxes`; virtualization is supported, the daemon is healthy, and Docker authentication is configured. The microVM smoke test safely skips by default; deliberately enable it with:

```powershell
$env:AGENTFORGE_RUN_SBX_TESTS = "1"
python -m pytest tests\integration\test_sbx_live.py -q
```

The first smoke-test attempt exposed an upstream Windows issue: the healthy `sandboxd` process cannot connect to its own internal `docker.sock`, so the `shell-docker` template pull fails before a microVM is created. The issue is tracked by Docker as [sbx-releases #157](https://github.com/docker/sbx-releases/issues/157). No sandbox was left behind. Retest after Docker ships a Windows fix. Installation and sandbox images are unrelated to the project-local Python virtual environment.

## E2B hosted microVMs

`E2BSandbox` is the second production adapter and the usable fallback while local `sbx` is blocked on Windows. It creates a secure, disposable Linux microVM through E2B, uploads the filtered snapshot one file at a time, runs a POSIX-quoted command with environment values passed separately, downloads the result through the same transfer filters, and kills the VM on timeout or close.

Set `E2B_API_KEY` in the process environment and select it explicitly:

```powershell
agentforge live-repair `
  --sandbox-backend e2b `
  --workspace .\example-project `
  --output .\example-project-repaired `
  --task "Fix the failing calculator test" `
  --allow-code-transmission `
  --allow-sandbox-execution
```

E2B is a hosted service: source files in the filtered snapshot leave the computer, and creating a VM may consume account credits. AgentForge disables internet access inside the VM by default. E2B's normal API does not expose AgentForge's requested CPU, memory, and process values per creation call; use a named `--e2b-template` to choose provider-side resources. Policy still rejects requests above AgentForge's configured caps, but the adapter does not pretend those three limits are independently enforced by E2B.

AgentForge enforces the Hobby plan's one-hour maximum session length locally. The current default is five minutes, workflows run sequentially, and the project does not target deployment-scale concurrency, uptime guarantees, or paid-tier-only capacity.

The real smoke test is gated so the normal suite never creates a cloud VM:

```powershell
$env:AGENTFORGE_RUN_E2B_TESTS = "1"
python -m pytest tests\integration\test_e2b_live.py -q
```

The opted-in smoke test completed successfully on 2026-09-12 using the Hobby plan: AgentForge created a secure VM, uploaded one filtered fixture file, ran a command, downloaded and verified the file, and deleted the VM. A compatibility fix converts E2B SDK `bytearray` downloads into the backend-neutral `bytes` result expected by AgentForge.

## Model provider

The first hosted-model adapter uses Hugging Face's OpenAI-compatible chat-completions endpoint without adding an SDK dependency. Copy `.env.example` to `.env`, keep the token local, and set:

```text
HF_TOKEN=your_private_token
AGENTFORGE_MODEL=Qwen/Qwen3-Coder-480B-A35B-Instruct:cheapest
```

The AgentForge CLI automatically reads `.env` from the directory where it is launched. Existing process environment variables take precedence, so `.env` cannot silently replace a value supplied by the shell. The file is excluded from version control and its values are never printed by the loader. Tests use `MockLLMProvider`, so the normal suite never spends credits or sends repository data over the network.

Confirm that all personal live-service settings are present without displaying them or making a network request:

```powershell
agentforge credentials-check
```

The check also rejects a Hugging Face value that is not shaped like an `hf_` user access token. This catches copied labels, inference URLs, and incomplete values before a paid request is attempted; only Hugging Face can confirm whether a correctly shaped token is active and has inference permission.

The minimal Hugging Face smoke test sends only a fixed instruction and no repository content. It is disabled unless deliberately enabled:

```powershell
$env:AGENTFORGE_RUN_HF_TESTS = "1"
python -m pytest tests\integration\test_huggingface_live.py -q
```

## Try the first agent workflow

After activating `.venv`, run the deterministic demo from the project root:

```powershell
python examples\mock_agent_workflow.py
```

It runs Planner → Repository Explorer against this repository using scripted model responses. The Explorer performs a real boundary-checked search, and the run prints its structured plan, exploration report, and trajectory-event count. It requires no token, network request, Docker daemon, or inference credits.

Repository inspection excludes virtual environments, caches, binary/oversized files, `.env`, private keys, and common credential files. Direct attempts to read those paths are rejected too; merely hiding them from file listings would not be sufficient.

## Repository symbol index

The Explorer can use `find_symbol` to locate Python classes, functions, and methods by their parsed names before reading source files. For example, it can find `CheckoutService.checkout`, its line range, and parameter names without searching every occurrence of the word “checkout” in comments or documentation.

The index uses Python's syntax parser, ignores the same generated and sensitive paths as normal repository inspection, skips malformed and non-Python files, and enforces symbol-count and result-count limits. Results are cached for the exploration phase and can be explicitly refreshed after repository changes.

## Run context and retrieval evaluation

Each workflow now owns an independent `RunContextMemory`. Agents add plans, grounded repository observations, exploration findings, and short completion summaries to that run only. Entries are deduplicated, individually truncated, capped by count, and ranked by priority plus task-word overlap when another agent requests context. Low-priority old entries are evicted first.

Tool output sent back to a model also has a hard character budget. Oversized observations become explicit JSON previews containing `truncated: true` and their original size; truncation is recorded in the trajectory rather than happening invisibly. Developer, Tester, and Debugger prompts receive task-relevant bounded evidence. Test results and structured diagnoses are kept as high-priority run memory.

The Reviewer is stricter: it must see the complete diff and test evidence, so AgentForge fails closed instead of inviting a verdict on a truncated patch. Before either standard trajectory recorder stores an event, nested credential fields, token assignments, bearer credentials, and private-key blocks are recursively replaced with `[REDACTED]`. This protects the audit trail, but it does not make arbitrary model prompts safe to persist elsewhere.

The deterministic retrieval benchmark measures top-k hit rate and mean reciprocal rank against a small fixture repository. It reports missed queries rather than discarding them. AgentForge includes a provider-neutral embedding contract, an offline hashing-vector implementation, and an approval-gated OpenAI-compatible HTTPS embedding adapter. When configured, the Explorer gains a `semantic_search` tool that ranks safe repository files by cosine similarity; it is omitted entirely when no embedding index is supplied.

On three deliberately paraphrased fixture queries, exact whole-phrase search scores 0% top-1 hit rate while the hashing-vector baseline scores 100%. That demonstrates why ranked vector retrieval is useful, but it is not evidence of human-like semantic understanding: the local baseline recognizes overlapping normalized words, not synonyms or intent. Learned embeddings therefore remain opt-in and should be compared on representative local cases before becoming the personal default.

## Repair and test guarantees

The Developer cannot submit a whole replacement repository or an arbitrary write command. It can use exact `old_text` → `new_text` replacements or a restricted standard unified diff. AgentForge validates the entire patch before writing, rejects mismatched hunks and sensitive paths, caps patch size, and replaces each changed file atomically. Creation, deletion, renaming, quoted Git paths, and final-newline changes remain deliberately unsupported.

Exact replacements normalize uniform LF, CRLF, or legacy CR line endings only
while comparing and applying the requested text, then write the result using the
file's original style. This lets a model-generated LF replacement safely edit a
Windows Git checkout without rewriting the file to LF. Mixed-line-ending files
retain strict byte-for-text matching rather than being silently normalized.

If every regular Developer turn ends without an accepted patch after at least
one rejected edit, AgentForge grants one correction-only patch turn. An accepted
last-turn patch receives a separate report-only turn exposing only
`finish_development`. Both recoveries are bounded; another invalid response still
fails closed. Rejection feedback tells the Developer to reread the file and use a
small exact replacement, while Explorer and Developer prompts explicitly trace
imported helper modules for multi-file defects.

The structured list of changed files is derived from successful patch-tool
results for the current repair attempt, not copied from the model and not taken
from the editor's cumulative review diff. This keeps retries separate: a second
Developer may accurately report only its new file while the Reviewer still sees
the complete change across every attempt. A legacy model-supplied list is
reconciled to the confirmed evidence rather than aborting the workflow.

The Tester converts a structured model tool call into an `ActionRequest`. That request goes through `SecurityPolicy` and then the selected `Sandbox`; the agent never calls a process API itself. Once one command actually executes, the next turn exposes only `finish_testing`, and its pass/fail claim must exactly match that result. It cannot hide a broad failing suite by running a narrower command afterward. Rejected commands may be corrected within the normal bound because they produced no execution evidence. If the model nevertheless invents another command on the report-only turn, AgentForge rejects it without execution and permits exactly one `finish_testing` correction before failing closed.

When tests fail, `RepairOrchestrator` sends their command, exit status, stdout, and stderr to the Debugger. The route is available only when the current Tester invocation returned a validated failed `TestReport`; a missing report or stale artifact stops with a clear protocol failure instead of crashing the Debugger. The resulting structured diagnosis becomes context for the next Developer attempt. A configurable repair-attempt limit prevents an endless model loop.

After tests pass, the Reviewer receives the complete diff and test evidence. It can accept the patch or reject it with concrete issues. A rejected review is a failed workflow; AgentForge never silently labels it successful.

The local development sandbox now passes only a small operational environment allowlist to child processes. Arbitrary host variables—including model tokens—are not inherited.

Run the seeded end-to-end repair test:

```powershell
python -m pytest tests\test_repair_workflow.py -q
```

This copies a deliberately broken calculator into a temporary workspace, repairs it, runs its specification, verifies the patch, and destroys the workspace. `tests/test_retry_workflow.py` also proves that an incorrect first patch follows the fail → diagnose → repatch → pass → review route. Both use the local backend only with trusted fixture code.

Run the reusable offline benchmark for a compact machine-readable health check:

```powershell
agentforge benchmark-repair
```

It creates a temporary broken parity function, runs Planner → Explorer → Developer → Tester → Reviewer with deterministic mock responses, executes the repaired specification locally, and reports success, repair attempts, completed agent steps, trajectory-event count, and wall-clock duration. Each run is appended to `artifacts/repair-benchmark-history.jsonl`; pass `--history PATH` to choose another location. It uses no hosted model, cloud sandbox, source repository, or credits.

Summarize that history and compare the newest two runs with:

```powershell
agentforge benchmark-report
```

The report returns a nonzero exit code if a previously successful workflow, test result, or review now fails, or if repair attempts increase. Timing is reported separately as a warning only when the newest run is both more than 0.1 seconds and 50% slower, avoiding false functional failures from ordinary machine noise. Every JSONL record is schema-validated before comparison.

Run the broader three-case benchmark with:

```powershell
agentforge benchmark-suite
```

The first case checks the normal one-attempt repair. The second intentionally makes an incorrect patch, confirms that the tests fail, asks the Debugger for a diagnosis, retries the Developer, reruns the tests, and completes review. The third passes its tests but receives a rejected review; the feedback is carried into another development attempt, followed by a retest and accepted review. The JSON result includes each named case and totals for passed cases, repair attempts, and duration. Each run is saved separately in `artifacts/repair-benchmark-suite-history.jsonl`; use `--history PATH` to override it. It remains fully offline and does not use Hugging Face, E2B, Docker Sandboxes, or API credits.

Compare the newest two suite runs case by case with:

```powershell
agentforge benchmark-suite-report
```

The report names the exact case that disappeared, stopped succeeding, lost passing tests or review approval, or needed more repair attempts. It exits with code 1 when one of those regressions is present, making it useful in a personal verification script.

## Local status service

Install the service dependencies inside the existing project environment:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[service]"
```

Start the service with:

```powershell
.\.venv\Scripts\agentforge.exe serve
```

AgentForge always binds this command to `127.0.0.1`; it is not exposed to other computers. Open `http://127.0.0.1:8765/` for the self-contained local dashboard. The machine-readable OpenAPI description remains at `http://127.0.0.1:8765/openapi.json`. The initial API deliberately accepts only the safe, deterministic `benchmark-suite` run kind:

```text
GET    /health                 local process health
POST   /runs                   create a benchmark-suite run
GET    /runs/{run_id}          read current status and final result
GET    /runs/{run_id}/events   stream lifecycle events as SSE
DELETE /runs/{run_id}          request cancellation
```

Only one run executes at a time, matching the project's personal Hobby-plan scope. A cancellation request is reported separately from actual cancellation: running Python work is allowed to reach a safe boundary before the status becomes `cancelled`. Run records are held only in this process and disappear when the service stops.

The redesigned **AgentForge Studio** dashboard is also the visual guide to the project. It includes an animated query walkthrough, selectable cards for all six agents, capability and sandbox maps, the five security boundaries, service health, live benchmark lifecycle events, aggregate metrics, and all three recovery-case results. A real-time monitor consumes sanitized orchestrator events, highlights the currently active specialist, marks completed, failed, and skipped stages, and keeps a per-agent history of structured outputs across all three cases. Its sequence cursor resumes an interrupted event stream without duplicating earlier entries. The dashboard HTML, CSS, and JavaScript are served by AgentForge itself; it loads no CDN, analytics, font, or other internet resource. Non-local Host and Origin values are rejected. The current and future access boundaries are specified in [SECURITY_MODEL.md](SECURITY_MODEL.md).

## Trajectory metrics

Turn a completed JSONL trajectory into validated metrics with:

```powershell
.\.venv\Scripts\agentforge.exe trajectory-report `
  --trajectory .\trajectories\RUN.jsonl `
  --database .\artifacts\metrics.sqlite3
```

The report measures success, elapsed time, completed agent steps, repair attempts, retries, test runs, failed tests, review rejections, model requests, input/output/total tokens, sandbox execution time, and model names. Add both `--input-price` and `--output-price` with the provider's current per-million-token prices to calculate an estimated cost; AgentForge does not embed prices that can become stale. SQLite persistence stores only the aggregate, sanitized metrics and replaces an existing row with the same run ID.

## Maintenance and recovery commands

Inspect local, `sbx`, and E2B readiness without creating a cloud VM, and measure the lightweight local startup path:

```powershell
.\.venv\Scripts\agentforge.exe sandbox-health --backend all
.\.venv\Scripts\agentforge.exe sandbox-startup --iterations 3
```

Create a safe remote snapshot only after explicitly approving network access and the exact HTTPS hostname:

```powershell
.\.venv\Scripts\agentforge.exe clone-snapshot `
  --url https://github.com/OWNER/REPOSITORY.git `
  --output .\repository-snapshot `
  --allow-host github.com `
  --allow-network-access
```

Interrupted `sbx` runs are stored by exact ID in `artifacts/sbx-orphans.json`. Review that file first, then remove only its validated AgentForge-owned entries with:

```powershell
.\.venv\Scripts\agentforge.exe reap-sbx-orphans --allow-cleanup
```

Model/policy experiments can be supplied as strict local JSONL and summarized with `compare-runs --input PATH`. The SWE-bench adapter likewise loads bounded local case metadata only; it does not clone or execute an upstream benchmark implicitly.

For a completely offline walkthrough, run `examples\personal_demo.ps1`. The full component map is in [ARCHITECTURE.md](ARCHITECTURE.md), concrete abuse cases are in [THREAT_MODEL.md](THREAT_MODEL.md), and the local-access boundary is in [SECURITY_MODEL.md](SECURITY_MODEL.md).

## Read-only Git tools

`GitRepositoryTools` exposes fixed `git status --short` and `git diff` operations through the same sandbox and policy boundary. The default policy permits only the `status` and `diff` subcommands; mutating commands such as `checkout`, `commit`, `reset`, and `clean` are denied even when the executable itself is Git.

## Opt-in hosted workflows

The first live Hugging Face entry point is intentionally read-only. It may send the task and model-selected, non-secret repository excerpts to Hugging Face, so it refuses to run without an explicit acknowledgement:

```powershell
# Set HF_TOKEN and AGENTFORGE_MODEL in this process first.
agentforge live-explore `
  --workspace . `
  --task "Explain the sandbox architecture" `
  --allow-code-transmission
```

The command writes a local JSONL trajectory and prints structured plan/exploration results. It never sends the absolute local workspace path.

The full repair command has two separate acknowledgements because it both transmits selected source text and executes model-selected test commands. Commands remain constrained by `SecurityPolicy`, run in the selected disposable microVM with networking denied, and never run directly in the source directory. The default backend remains `sbx`; add `--sandbox-backend e2b` for the hosted fallback.

Those acknowledgements now become an immutable `ApprovalCheckpoint`. Code transmission and sandbox execution are separate capabilities: granting one never grants the other, and missing approval stops the command before the sensitive operation. Granted capability names are attached to workflow state and recorded in the trajectory without tokens, keys, or other secret values.

```powershell
# Set HF_TOKEN and AGENTFORGE_MODEL in this process first.
agentforge live-repair `
  --workspace .\example-project `
  --output .\example-project-repaired `
  --task "Fix the failing calculator test" `
  --allow-code-transmission `
  --allow-sandbox-execution
```

The output path must not exist and a failed workflow is not exported. The complete Hugging Face + E2B repair path was verified on 2026-09-13 against the two-file synthetic fixture below: Planner, Explorer, Developer, Tester, and Reviewer completed; the Debugger was not needed because the first repair passed. E2B tests passed, review accepted the minimal diff, and the filtered result was exported. The normal suite still never contacts either provider. If the Explorer ignores its forced finalization tool and invents a different tool call, AgentForge rejects that call without executing it and grants exactly one correction-only retry before failing closed.

For a deliberately minimal end-to-end live check, `examples\live_smoke_fixture` contains two synthetic Python files and no user repository data. Use it as the workspace in the command above, choose `--sandbox-backend e2b`, and keep the two approval flags. This still sends that synthetic source to Hugging Face and E2B and may use account quota, so every repeat requires the user's explicit authorization for those destinations.

For a more demanding repair, `examples\checkout_repair_fixture` is a six-file,
dependency-free checkout system. Four of its eight acceptance tests fail because
three defects interact across discount rounding, tax exemptions, and discounted
free-shipping eligibility. `TASK.md` is the issue AgentForge should follow; the
tests are the acceptance contract and must not be edited. This fixture is still
synthetic, but a hosted run transmits it and consumes provider quota, so creating
the fixture does not authorize a live run.

```powershell
agentforge live-repair `
  --sandbox-backend e2b `
  --workspace .\examples\checkout_repair_fixture `
  --output .\artifacts\checkout-repaired `
  --task "Read TASK.md and fix the checkout implementation without modifying tests" `
  --allow-code-transmission `
  --allow-sandbox-execution
```

For the hardest synthetic live benchmark, `examples\cloud_billing_repair_fixture`
models a SaaS invoice engine. Six defects interact across base-fee proration,
graduated API usage, coupon rounding, service-credit caps, taxable-base ordering,
and tax exemption. The correct reference passed all 13 acceptance tests before
the defects were seeded; the committed challenge passes four and fails nine.
It has no external dependencies and contains no personal repository data.

```powershell
agentforge live-repair `
  --sandbox-backend e2b `
  --workspace .\examples\cloud_billing_repair_fixture `
  --output .\artifacts\cloud-billing-repaired `
  --task "Read TASK.md and repair the cloud billing implementation without modifying tests" `
  --allow-code-transmission `
  --allow-sandbox-execution
```

`examples\fulfillment_repair_fixture` is the strict stress fixture. It contains
23 tests across interacting promotion, FEFO inventory, shipping, tax, credit,
and settlement rules. The seeded baseline passes 5 tests and fails 18; a checked
reference passes all 23. The recorded five-attempt live run reached 20/23 and
then failed closed, so AgentForge did not export an incomplete repair. See
[BENCHMARK_RESULTS.md](BENCHMARK_RESULTS.md) for the measured trajectory and the
separate successful cloud-billing result.

```powershell
agentforge live-repair `
  --sandbox-backend e2b `
  --workspace .\examples\fulfillment_repair_fixture `
  --output .\artifacts\fulfillment-hard-repaired `
  --max-attempts 5 `
  --task "Read TASK.md and repair the multi-warehouse fulfillment implementation without modifying test_fulfillment.py" `
  --allow-code-transmission `
  --allow-sandbox-execution
```

To repeat only that minimal provider test deliberately:

```powershell
$env:AGENTFORGE_RUN_HF_TESTS = "1"
python -m pytest tests\integration\test_huggingface_live.py -q
```

## 100-case SWE-bench run

AgentForge can now generate patches for a balanced 100-case slice of the
official SWE-bench Verified test split. This is a real repository benchmark,
not another synthetic fixture. The run uses five ordered waves of 20: wave two
cannot create a repair microVM until every case in wave one has finished. Each
case has its own E2B `/testbed`, trajectory, prediction file, and append-only
ledger record, so an interruption can resume without discarding finished work.

Start or resume generation with:

```powershell
.\.venv\Scripts\agentforge.exe swebench-run `
  --limit 100 `
  --batch-size 20 `
  --output .\artifacts\swebench-agentforge-100 `
  --allow-code-transmission `
  --allow-sandbox-execution
```

The generation command now defaults to a resilient budgeted hybrid. DeepSeek
V4.1 Flash handles Planner, Repository Explorer, Developer, Debugger, and
Reviewer. Qwen3.6 is the preferred Tester model; a bounded connection or
rate-limit failure transparently retries the identical Tester request through
DeepSeek and opens a circuit so later calls do not keep waiting on Qwen. One
preflight checks both routes before creating an E2B microVM. One shared meter defaults
to 80 requests, 1.5 million tokens, $0.30 estimated inference, and 4,096 output
tokens per response. These are local fail-closed limits, not billing guarantees;
provider prices must be kept current. Override them explicitly for a smaller
smoke test, for example:

```powershell
.\.venv\Scripts\agentforge.exe swebench-run `
  --subset .\artifacts\swebench-agentforge-100\selected-cases.jsonl `
  --instance-id pytest-dev__pytest-10051 `
  --limit 1 --batch-size 1 --max-attempts 1 `
  --max-model-requests 30 --max-model-tokens 400000 `
  --max-model-cost 0.06 `
  --output .\artifacts\swebench-hybrid-smoke `
  --allow-code-transmission --allow-sandbox-execution
```

`resolved_cases` in the generation ledger means the internal AgentForge
workflow obtained passing visible tests and Reviewer acceptance. It is not the
official benchmark score. Grade the resulting patches against the held-out
tests in fresh microVMs before quoting a resume metric:

```powershell
.\.venv\Scripts\agentforge.exe swebench-grade `
  --run .\artifacts\swebench-agentforge-100 `
  --batch-size 20 `
  --allow-code-transmission `
  --allow-sandbox-execution
```

The grading command also runs sequential waves of at most 20. Gold patches are
never saved in AgentForge's selected-case file or supplied to an agent. Both
commands use the project `.venv`; the benchmark dependencies are installed with
`.\.venv\Scripts\python.exe -m pip install -e ".[swebench]"`.

The resilient hybrid was evaluated on the frozen manifest
`benchmarks/swebench-cv-20.txt`: 9/20 SWE-bench Verified cases resolved (45%)
with zero canonical grader errors. The resolved cases passed all 18 target tests
and all 508 regression tests. Generation used 323 provider requests, 4,351,430
provider-reported tokens, and $0.89365 estimated inference under a hard $2.50
cap. Cases ran in four sequential waves of at most five, and seven expensive
cases stopped at their own 500,000-token ceiling. This is a custom frozen subset,
not an official leaderboard submission. Five still-unresolved capped cases were
then retried sequentially with a non-binding token ceiling but retained dollar
guards. One additional Xarray case resolved, producing a retry-assisted best-of
10/20 (50%); cumulative inference was $1.43672. The honest first-pass score
remains 9/20 (45%). Full selection rules, limitations, and measurements are in
[BENCHMARK_RESULTS.md](BENCHMARK_RESULTS.md).

## Core design rules

1. Agents produce structured requests; they do not execute commands directly.
2. Policy decisions happen before sandbox execution.
3. Sandboxes are replaceable and short-lived.
4. Every meaningful decision and result can be recorded as a trajectory event.
5. The orchestration layer depends on interfaces, not on a specific LLM provider.
6. Security failures stop closed: an unknown or forbidden action is rejected.

## Repository layout

```text
src/agentforge/
  agents/          Agent contracts
  context/         Run-scoped memory and model-context budgets
  core/            Tasks, actions, workflow state, and results
  evaluation/      Repair/retrieval benchmarks, SWE-bench metadata, comparisons
  llm/             Provider-neutral models, mock, and hosted API adapters
  tools/           Boundary-checked inspection, symbol index, editing, and Git tools
  orchestration/   Multi-agent workflow runner
  policy/          Pre-execution security decisions
  sandbox/         Sandbox contract, transfer, health, recovery, and backends
  telemetry/       Redacted trajectories, metrics, and SQLite aggregates
tests/              Unit and integration-style tests
PROJECT_GUIDE.md    Plain-language learning guide
DEVELOPMENT.md      Progress tracker and roadmap
```

## Safety

Do not point the local backend at unknown repositories or AI-generated commands. It uses process separation and timeouts for developer convenience, not container or VM isolation. The planned production path is:

```text
agent -> policy engine -> selected microVM backend -> captured result
```
