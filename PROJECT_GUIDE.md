# AgentForge: The Easy Project Guide

This guide explains both **what we are building** and **why it is designed this way**. It assumes no knowledge of AI agents, containers, or software architecture.

## 1. The idea in one minute

AgentForge is being built as an individual learning and development project. It is not intended to become a public hosted service. That keeps the design focused on one person's repositories, deliberate runs, understandable safety controls, and the free E2B Hobby plan.

Imagine giving a small software team a bug report. One person plans the work, another searches the code, another writes a fix, another runs tests, and a reviewer checks the result.

AgentForge aims to make that team out of cooperating AI agents:

- the **Planner** turns a vague task into steps;
- the **Explorer** finds the relevant parts of a repository;
- the **Developer** proposes code changes;
- the **Tester** runs checks and reports failures;
- the **Debugger** investigates those failures;
- the **Reviewer** accepts the result or sends it back for another attempt.

AI-generated code can be wrong or dangerous. AgentForge therefore places a security checkpoint and an isolated disposable computer—the **sandbox**—between the agents and real execution.

## 2. What is an AI agent?

A normal chatbot answers once. An agent works in a loop:

```text
understand -> choose an action -> observe the result -> choose the next action
```

For example, an agent might decide to read a file, then search for a function, then run a test. The output of each step becomes information for its next decision.

The language model is the decision-maker, not the executor. If it wants to run `python -m unittest`, it asks AgentForge to do so using a structured request.

## 3. Why multiple agents?

One model can attempt every job, but named roles make the workflow easier to understand, test, and improve. A reviewer can be given different instructions from a developer. A tester can report facts without being tempted to quietly rewrite the implementation.

In the current foundation, AgentForge provides a generic orchestrator that passes shared workflow state through any ordered list of agents. The real specialist prompts and LangGraph cycles arrive in a later section.

## 4. What is a sandbox?

A sandbox is a temporary workspace where code can run without being trusted with the rest of the computer. A good production sandbox limits:

- which files are visible;
- CPU time and memory;
- how many processes can start;
- how long a command may run;
- which network destinations are reachable;
- which credentials, if any, are available.

This repository begins with a `LocalSandbox` so we can test the architecture cheaply. It runs a command as a child process, in a controlled working directory, without shell interpretation, and with a timeout. It is useful scaffolding, but it is not safe enough for hostile code.

The primary backend is now `SbxSandbox`, built on Docker Sandboxes. It creates a disposable microVM with its own Linux filesystem and Docker Engine. AgentForge does not mount the original project: it copies a filtered snapshot into the VM, denies outbound network for that sandbox, and stops the whole VM if a command times out. Docker Desktop and Docker Engine are not required on the host.

There is now a second option, `E2BSandbox`. It rents the same kind of short-lived isolated computer from E2B's cloud instead of starting it on this Windows machine. This avoids the current Docker Sandboxes Windows bug, but it means the filtered source copy is uploaded to an outside service and the E2B account may be charged. It is therefore selected deliberately with `--sandbox-backend e2b`, never silently.

The agent can freely change the inside of this temporary VM. That is intentional. The safety boundary comes from isolating the entire VM from the host and deleting it afterward, rather than trying to make the agent's own temporary filesystem read-only.

## 5. What is the policy engine?

The policy engine is a security guard standing before the sandbox. It receives a structured action such as:

```text
program: python
arguments: -m unittest
working directory: .
```

It then answers **allowed** or **denied**, with human-readable reasons. The first policy version rejects empty commands, shell-control characters, forbidden programs such as `sudo`, and working directories that escape the sandbox workspace.

Why do this outside the sandbox? Isolation reduces damage, while policy reduces exposure. They are two separate safety layers, and either can evolve without replacing the other.

AgentForge now lets the owner write these rules in `agentforge-policy.example.yaml` rather than changing Python code. The file can say which commands and Git operations are allowed, which repository paths may be read or edited, which sensitive paths are always denied, how much output a command may return, and the largest sandbox resources permitted. A spelling mistake or unknown setting stops with an error instead of silently weakening protection.

The policy can also name permitted network hosts. This is currently a planning and validation rule only: AgentForge still keeps sandbox networking off because E2B and the existing local backends cannot guarantee that code contacts only those hosts. A future controlled proxy is required before destination-specific networking can safely be enabled.

## 6. What is a trajectory?

A trajectory is the project's flight recorder. It is a chronological list of events such as:

```text
planner started
planner produced a plan
tester requested a command
policy allowed it
command finished in 0.42 seconds
reviewer rejected the patch
```

AgentForge records these events as JSON Lines: one small JSON object per line. That format is easy for humans to inspect and easy for future dashboards or databases to ingest.

Trajectories will let us answer practical questions: How many attempts did a fix require? Which agent failed? How long did tests take? Which policy rule blocked an action?

## 7. What exists today?

The first working slice contains:

- plain Python data types for tasks, actions, results, and workflow state;
- a role-independent agent interface;
- a sequential multi-agent orchestrator with stop-on-failure behavior;
- a security policy engine;
- a generic sandbox interface;
- a temporary local sandbox for trusted development commands;
- a Docker Sandboxes (`sbx`) microVM adapter with lifecycle and resource controls;
- an E2B hosted-microVM adapter with filtered upload/download and forced cleanup;
- a JSONL trajectory recorder;
- a common language-model interface;
- a deterministic pretend model for tests;
- a Hugging Face adapter supporting text, structured tool calls, retries, and token counts;
- a Planner that produces a validated, size-limited list of steps;
- a Repository Explorer with a bounded list/search/read tool loop;
- a Developer restricted to precise, validated text patches;
- a Tester whose commands must pass through policy and sandbox enforcement;
- a Debugger that turns failed command evidence into a structured diagnosis;
- a Reviewer that sees the final diff and passing evidence before accepting work;
- a filtered, size-bounded way to copy work into and back out of either microVM;
- protection that prevents repository inspection from exposing `.env`, private keys, caches, or files outside the workspace;
- tests demonstrating the important guarantees;
- a command-line policy checker.

The normal test and demo loops deliberately use a pretend model. This proves the real agent logic before API costs and model variability enter the picture.

The `agentforge benchmark-repair` command turns that idea into a repeatable health check. It creates the same tiny broken program each time, lets the deterministic agent team repair it, runs the specification, asks the Reviewer to accept or reject it, and prints comparable metrics. It also adds one line to a local history file under `artifacts/`, so results can be compared later without setting up a database. Because both the bug and model responses are fixed, a future regression is easier to distinguish from ordinary model randomness.

`agentforge benchmark-report` reads that history and compares the newest result with the previous one. A newly failing workflow, test suite, or review—and an increase in repair attempts—is treated as a real regression. Slower execution is shown separately because small timing changes often come from normal computer activity rather than broken agent logic. Invalid or outdated history records stop with their line number instead of producing misleading statistics.

`agentforge benchmark-suite` asks a harder question: can AgentForge recover when its first answer is wrong? It runs the normal direct fix, a scenario where the Developer deliberately makes a bad change, and one where working code receives a rejected review. Failed tests go to the Debugger for diagnosis. A rejected review instead becomes high feedback for the Developer, after which tests and review run again. Only a passing test and accepted review count as success. This is valuable because a useful coding agent must recover from mistakes and criticism, not merely succeed when every scripted choice is correct.

Suite runs go into their own small history file instead of being mixed with the easier one-case measurement. `agentforge benchmark-suite-report` compares cases by name, so it can say “the debug-and-retry case now needs more attempts” or “this case disappeared,” rather than hiding the problem inside an overall average. It also checks that saved totals agree with the case details before trusting them.

## 7.2 What is the local service?

Running `agentforge serve` starts a small control desk on your own computer. Another local program—or the built-in dashboard at `http://127.0.0.1:8765/`—can ask AgentForge to start its offline benchmark, check whether it is waiting, running, finished, failed, or cancelled, and watch lifecycle updates as they happen. The dashboard also shows each benchmark case and how many repair attempts it needed.

The dashboard is now called **AgentForge Studio**. Its top half teaches before it operates: an animated diagram walks through Planner, Explorer, Developer, Tester, Debugger, and Reviewer; selecting a role explains its tools, required evidence, output, and restrictions. Feature cards explain repository intelligence, restricted editing, quality checks, metrics, sandboxes, and evaluation. A layered safety diagram shows how human approval, repository filtering, policy, microVM isolation, and final review surround every query. The Live Lab below those explanations still runs the real deterministic three-case benchmark and displays its lifecycle and results.

The address `127.0.0.1` means “this computer only.” AgentForge does not listen on your Wi-Fi or internet connection. It runs one job at a time and keeps at most 100 recent run records in memory. This avoids pretending that the personal project is a production cloud service. There is no user login yet because remote access is intentionally impossible; authentication would become mandatory before that boundary could ever change.

Cancellation is honest rather than instantaneous. Python cannot safely kill an arbitrary thread midway through file or process cleanup. AgentForge records that cancellation was requested, lets the current bounded operation reach its return point, discards its result, and then marks the run cancelled. A queued job can be cancelled before it starts.

The dashboard is packaged inside AgentForge and contacts only this local service. Requests claiming to come through a non-local web address are rejected. [SECURITY_MODEL.md](SECURITY_MODEL.md) explains why this is adequate for the current one-person mode and lists every identity, isolation, credential, audit, quota, and cleanup control required before remote or multi-user access could be considered.

## 7.3 What do the run metrics mean?

`agentforge trajectory-report` reads the run's event diary and counts concrete facts: how many agent steps finished, how many repairs and retries occurred, how often tests ran or failed, whether review sent the work back, how long sandbox commands took, and how many model tokens were reported. A JSONL file containing broken records or multiple run IDs is rejected instead of producing a misleading total.

Cost is an estimate, not a timeless fact. Model providers can change prices, so AgentForge requires you to supply the current input and output prices when you want a cost calculation. The optional local SQLite file stores one compact metrics record per run. It does not store source files, prompts, tokens, or raw model output.

## 7.1 How can one interface work with different AI models?

AgentForge speaks to models through an `LLMProvider` interface. Think of it as a universal power socket: the orchestrator supplies a conversation and a list of available tools, then receives either an answer or a structured request to use a tool.

The Hugging Face adapter translates that common format into an HTTPS request. The mock adapter returns answers we prepared in advance. Because both expose the same interface, agent tests can be fast, free, private, and predictable; the real hosted model can be used only when we deliberately run an integration test.

The access token lives in the `HF_TOKEN` environment variable and is never stored in source code. The model name lives in `AGENTFORGE_MODEL`, allowing a routing suffix such as `:fastest` or `:cheapest` without modifying Python files. For convenience, the CLI loads these values from a `.env` file in the directory where it is launched. Values already set by the shell win, and `.env` is excluded from version control.

Running `agentforge credentials-check` answers only whether the Hugging Face token, model name, and E2B key are present. It never prints their values and does not contact either service. This is a safe first check before deliberately opting into a live test.

## 7.2 What do the first two agents actually do?

The Planner is forced to call a `submit_plan` tool. AgentForge checks that the plan has a sensible number of non-empty steps and a rationale before allowing it into shared workflow state. A paragraph that merely looks like a plan is not accepted.

The Repository Explorer receives that validated plan and may repeatedly call `list_files`, `search_code`, and `read_file`. Each path is resolved against one assigned repository root. Attempts to use `..`, follow a secret path, read a private key, inspect a binary file, or exceed a size limit are rejected and returned to the model as an observation. The model can correct its request within a fixed iteration budget.

When it has enough evidence, the Explorer must call `finish_exploration`. Every claimed relevant file is checked again, so a hallucinated filename cannot enter the final report unnoticed. On the last normal turn, that is the only tool AgentForge offers. If a hosted model still invents a different tool name, AgentForge rejects it without executing it and gives the model one correction-only turn. That retry again exposes only `finish_exploration`; a second invalid response stops the workflow safely instead of creating an unbounded loop.

## 7.3 How does the Developer change code safely?

The Developer does not receive a general “write anything anywhere” tool. It must identify an exact piece of existing text and provide its replacement. Before writing, AgentForge checks every proposed change:

- the path must remain inside the repository;
- the target must be an existing, permitted text file;
- the old text must occur exactly once, avoiding ambiguous edits;
- the replacement must genuinely change the file;
- the result must remain below the file-size limit;
- all changes in the patch must validate before any file is touched.

Each validated file is written to a temporary sibling and then swapped into place. This prevents readers from seeing a half-written file. The current tool intentionally cannot create, rename, or delete files; those operations need their own future policies.

The Developer can now also submit the familiar “unified diff” format used by Git. AgentForge parses every file header and hunk itself, checks declared line counts, and confirms that context and removed lines exactly match the current file. All files validate before writing begins. The restricted parser still refuses file creation, deletion, renaming, quoted paths, and final-newline changes; supporting those safely requires additional rules.

## 7.4 Why can’t the Tester simply say “it passes”?

The Tester asks for a structured command, such as Python plus `-m unittest`. AgentForge turns that into an `ActionRequest`, sends it through the security policy, and executes it through the configured sandbox. Standard output, errors, exit code, duration, timeout, and truncation status come back as evidence.

The Tester may only finish after a real command completed. If it reports success when the exit code says failure—or failure when execution actually passed—AgentForge rejects the model response. This keeps the recorded result tied to machine evidence rather than model confidence.

## 7.5 What happens when tests fail?

A failed test is not automatically a failed project run. The repair orchestrator follows an explicit route:

```text
Developer -> Tester -> failed -> Debugger -> Developer -> Tester
                                      ^                     |
                                      |______ retry ________|
```

The Debugger receives the exact command outcome and must submit a hypothesis, supporting evidence, and recommended changes. That diagnosis is added to the next Developer prompt. A repair-attempt limit stops the cycle if the team cannot reach passing tests.

Importantly, AgentForge keeps the first version of every edited file. If the Developer makes a wrong first patch and fixes it later, the final unified diff still compares the original file with the final file. The user does not have to mentally combine several intermediate patches.

## 7.6 What does the Reviewer add?

Passing tests are necessary, but they are not proof that a change is sensible or minimal. The Reviewer sees the task, development summary, test command, test summary, and complete diff. It produces a separate accept/reject verdict. A rejection must list at least one concrete issue and causes the workflow to fail closed.

This is still an AI review, not a human security audit. Its purpose is to create separation of roles and an explicit checkpoint. Future versions will also support a human approval state for sensitive repositories.

## 7.7 Why are Git commands restricted separately?

Allowing the `git` program is too broad. `git status` reads state, while `git reset`, `git clean`, or `git checkout` can erase work. The policy engine therefore checks both the executable and its first subcommand. Today only status and diff are allowed through the AgentForge Git tool.

## 7.8 How do I deliberately try a hosted model?

`agentforge live-explore` connects only the Planner and read-only Explorer to Hugging Face. It requires the `HF_TOKEN` and `AGENTFORGE_MODEL` environment variables plus an explicit `--allow-code-transmission` flag. Without that flag, it stops before constructing the provider or making a network request.

A minimal live smoke test has now confirmed that the configured fine-grained token, selected model route, and AgentForge response parser work together. It sent only a fixed test sentence—not repository content—and remains disabled during normal test runs.

This matters because reading a file locally and sending its contents to an external model are different actions. The command filters known secret files and avoids sending the absolute local path, but the person running it should still review the selected repository and understand that ordinary source excerpts may leave the machine.

## 7.9 How does a live repair protect my original project?

AgentForge does not mount your chosen project directly into the microVM. It first builds a filtered snapshot in its own temporary workspace. Source-control internals, virtual environments, caches, common credential filenames, private keys, oversized files, and symbolic links are left out. A manifest records the size and SHA-256 fingerprint of every copied file so the snapshot is auditable.

The snapshot is copied into either a mountless local `sbx` microVM or an E2B cloud microVM. The agents inspect and edit only that copy. Test commands pass through the same policy engine regardless of provider. If testing and review succeed, AgentForge copies the result out, filters it again, and writes it to a brand-new output path. It refuses to overwrite an existing directory. If the workflow fails, nothing is exported, and the original project remains untouched.

The command requires two deliberate flags: one acknowledges that selected source excerpts may be sent to Hugging Face, and the other permits isolated test execution. Having a model token does not silently grant either permission.

AgentForge now represents those choices as a small human-approval checkpoint. Think of it as two separate keys: one opens the door for selected code to leave the computer, while the other allows commands to run in the disposable machine. Possessing either key does not open the other door. The granted capability names are stored in workflow state and the run's audit trail, but credentials are never included.

On the current Windows development machine, installation, virtualization, daemon health, and Docker sign-in all pass. The first real smoke test still stops before VM creation because Docker Sandboxes cannot connect to its own internal Docker socket, a known Windows product issue. This is useful evidence: AgentForge's deterministic tests pass, but the guide does not call the integration “live verified” until the external runtime genuinely completes it.

This was checked again against the latest stable `sbx v0.42.1` on 2026-09-13. AgentForge first preserved an inaccessible daemon-state directory rather than deleting it. A freshly rebuilt daemon then failed on the same internal `docker.sock` self-connection while pulling the sandbox template, and the sandbox list remained empty. Docker's available `v0.43.0-rc3` notes do not claim to fix this Windows problem, so installing a prerelease would add risk without evidence of benefit.

The E2B path has complete offline lifecycle tests and a separate real-cloud smoke test. The real test successfully created a Hobby VM, uploaded a tiny fixture, executed a command, exported the result, and deleted the VM. It remains off by default because each run uses an external service and may spend credits; it runs only when `E2B_API_KEY` is available and `AGENTFORGE_RUN_E2B_TESTS=1` is set. E2B controls CPU, memory, and process sizing through provider templates, so AgentForge can validate requested limits but cannot enforce those three settings independently through the normal creation call.

The complete hosted workflow has also been verified using a deliberately broken two-file calculator rather than a personal repository. The first attempt revealed that the Explorer could spend its final turn rereading files instead of submitting findings, so AgentForge began offering only the required report tool on that final turn. A later real GitHub-clone run exposed a second provider behavior: the model invented the Tester's `run_tests` tool even though only `finish_exploration` was offered. AgentForge now rejects that cross-role request and allows one correction-only finalization retry. On the earlier successful run, the full team planned, inspected, changed `left - right` to `left + right`, passed both tests inside E2B, accepted the minimal diff, exported two filtered files, and destroyed the disposable VM.

The project deliberately stays within E2B Hobby: at most one hour per VM, no more than the plan's included concurrency, and no reliance on paid deployment features. In practice AgentForge uses one sandbox at a time and defaults to a five-minute lifetime, so it remains far below those boundaries.

## 7.10 How does AgentForge find code without reading everything?

A plain text search finds every appearance of a word—including comments and documentation. A symbol index instead understands code structure. For Python files, AgentForge now uses Python's own parser to record where each class, function, and method begins and ends, along with its qualified name and parameters.

If a task mentions `CheckoutService.checkout`, the Explorer can ask `find_symbol` and go directly to that method before reading the surrounding file. This reduces irrelevant text sent to the model and makes findings easier to ground in exact source locations.

The index has strict size and result limits, uses the same secret and ignored-directory protections as the other repository tools, and skips malformed files instead of letting one broken file stop exploration. It currently understands Python; additional language parsers can be added behind the same idea later.

## 7.11 How do agents remember useful facts without overflowing the model?

Every run now gets its own small notebook called `RunContextMemory`. The Planner records its plan, the Explorer records bounded tool observations and final findings, and the orchestrator records what each specialist completed. The notebook disappears with the run; it is not a global memory that can leak information between unrelated projects.

Space in a model prompt is limited. AgentForge therefore gives every notebook entry and tool observation a hard character allowance. When there is too much information, it keeps higher-priority and task-relevant entries first, drops old low-priority notes, and labels truncated tool output clearly. The same rule covers test output and the evidence sent to the Debugger. The Reviewer is never asked to approve a partial patch: if its complete evidence does not fit, the workflow stops instead. AgentForge can also ask a model to compact older notes, but only after code-transmission approval; it deletes the originals only after receiving a valid bounded summary.

## 7.12 How does the flight recorder avoid writing passwords into its log?

An audit log is useful only if keeping it does not create a new secret leak. Before AgentForge's normal in-memory or JSONL recorder stores an event, it walks through nested event data and replaces common credential fields with `[REDACTED]`. It also recognizes token assignments, bearer credentials, and private-key blocks hidden inside longer error messages.

This is a last line of defense, not permission to record everything. Agents still avoid putting prompt bodies and raw credentials into trajectory events in the first place. Future storage backends must apply the same recorder-boundary rule.

## 7.13 What is an embedding, and why try one for code search?

An embedding turns a piece of text into a fixed-length row of numbers. Texts that share useful features can then be compared mathematically. Instead of requiring the exact phrase `calculate shopping total` to appear in a file, a vector search can rank files containing related pieces such as `calculate_total`.

AgentForge defines a small `EmbeddingProvider` interface so a local or hosted model can be swapped in without changing the Explorer. The first implementation is deliberately simple and offline: it splits words and code identifiers, places them into a stable numeric vector, and compares normalized vectors with cosine similarity. An OpenAI-compatible HTTPS adapter is also available for learned embeddings, but it requires code-transmission approval and strictly checks every returned vector.

The offline baseline is useful but not truly intelligent. It cannot reliably understand synonyms, architecture, or program behavior. Its small benchmark improvement only proves that ranked overlapping-word retrieval beats exact whole-phrase matching on those fixture questions. Learned embeddings remain opt-in so the user can compare quality and cost before selecting one.

## 7.14 What makes the finished personal version maintainable?

AgentForge now has small commands for the unglamorous work that keeps an agent trustworthy. `sandbox-health` explains whether a backend is actually ready and never confuses “the CLI exists” with “a VM started.” `sandbox-startup` measures bounded cold starts without leaving a pool running. `clone-snapshot` permits only an explicitly named HTTPS host, makes a shallow clone, removes Git metadata and sensitive files, and writes a new filtered folder.

If Windows or the host process dies after creating an `sbx` microVM, AgentForge keeps that exact resource ID in a local registry. The cleanup command refuses wildcards and unknown names. Provider keys are obtained by a host-only credential broker, and credential-looking environment variables are denied to guest commands even if somebody accidentally adds their names to a policy allowlist.

The local scheduler deliberately runs one job at a time and caps retained records. That is enough for one person on the Hobby plan. A network proxy, multi-user authentication, deployment pipeline, and public hosting are not unfinished personal features; they belong to a different shared-service scope. [ARCHITECTURE.md](ARCHITECTURE.md) shows the complete data flow, while [THREAT_MODEL.md](THREAT_MODEL.md) explains what could go wrong and which layer responds.

We also added a tiny retrieval exam. Each question says which file should be found, and AgentForge measures whether text or symbol search places that file in the first few results. The report includes both the percentage of successful questions and how highly the right file ranked. A deliberately missed query remains visible, preventing an impressive average from hiding failures.

## 8. How the pieces fit together

```text
User task
   |
   v
Orchestrator ---- keeps shared state and chooses the next specialist
   |
   +----> Agent ---- proposes a structured action
                       |
                       v
                 Policy engine ---- denied -> safe error/observation
                       |
                    allowed
                       |
                       v
                    Sandbox ---- stdout, stderr, exit code, duration
                       |
                       v
                 Agent observes and continues

Every important transition --------> Trajectory recorder
```

## 9. Why not begin with LangGraph, FastAPI, and a web interface?

Those are useful delivery tools, but they do not define the project's core. Starting with them would make it harder to tell whether the agent workflow and security boundaries are correct. Small interfaces and deterministic tests give us a dependable base. The integrations can then be adapters around that base.

## 10. The build journey

The planned learning path is:

1. establish core contracts and safety rules;
2. run trusted commands through a local development backend;
3. add interchangeable disposable microVM environments, with bounded workspace import/export (`sbx` and E2B adapters complete; live verification is opt-in);
4. connect a model-independent LLM interface and Hugging Face provider (complete, except for an opt-in live call);
5. implement the Planner/Explorer/Developer/Tester/Debugger/Reviewer graph;
6. support repository cloning, code changes, tests, and Git diffs;
7. add repository search and later semantic retrieval for large codebases;
8. expose the workflow through FastAPI and stream progress;
9. persist telemetry and build evaluation reports;
10. add stronger sandbox providers and a user interface.

For exact status and acceptance criteria, see [DEVELOPMENT.md](DEVELOPMENT.md).

## 11. A tiny glossary

- **API**: a defined way for two pieces of software to communicate.
- **Backend**: the part of an application that performs work rather than drawing the user interface.
- **Docker**: software for running processes inside isolated containers.
- **E2B**: a hosted service that provides short-lived isolated computers for code execution.
- **Ephemeral**: temporary and intended to be destroyed after use.
- **Git diff**: a precise description of which lines changed in a repository.
- **LLM**: the language model that interprets the task and chooses actions.
- **Orchestrator**: the coordinator that decides which agent works next.
- **Policy**: explicit rules defining which actions are permitted.
- **RAG**: retrieving relevant information before asking a model to reason about it.
- **Sandbox**: an isolated environment for executing untrusted work.
- **Tool call**: a structured request from a model to perform an action.
- **Trajectory**: the recorded history of an agent run.
