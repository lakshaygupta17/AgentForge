# AgentForge Architecture

AgentForge is a personal software-repair workshop. A task moves through six specialist roles, but every risky action passes through shared policy and isolation boundaries.

```text
Task
  |
  v
Planner -> Explorer -> Developer -> Tester -> Reviewer -> Accepted snapshot
                         ^          |          |
                         |          v          |
                         +------ Debugger <----+
                              bounded retries

Host-only services: model provider, approvals, credential broker, trajectories
                              |
                              v
Policy gate -> filtered snapshot -> disposable sbx or E2B microVM
                                      |
                                      v
                              filtered new output folder
```

## Why the pieces are separate

- The agents decide *what to try*. They do not receive a general shell or direct access to the source project.
- Repository tools decide *which files may be inspected or edited*. They reject path escapes, secrets, binaries, links, and oversized content.
- The policy engine decides *which structured commands and resources are allowed*.
- The sandbox decides *where commands run*. The source repository is copied through a bounded filter into a disposable microVM; an accepted result is copied into a different output directory.
- The credential broker keeps Hugging Face and E2B credentials on the host control plane. Credential-shaped environment variables are denied even if a policy accidentally allowlists their names.
- The approval checkpoint separates permission to transmit selected code from permission to execute tests.
- Telemetry records sanitized events and aggregate metrics without intentionally storing provider credentials.

## Runtime choices

`sbx` is the preferred local microVM backend. Its current Windows release is installed but fails before VM creation because of an upstream internal-socket defect. E2B is the verified fallback and stays within the Hobby constraints: one active worker, bounded resources, networking off by default, and sessions capped below one hour.

`LocalSandbox` is only for trusted deterministic development tests. `DockerSandbox` remains a legacy adapter and is not the personal live-repair default.

## Data lifecycle

1. The original workspace is read, filtered, and copied; it is never edited in place by `live-repair`.
2. Agents operate on the copied workspace.
3. Commands run only after policy and approval checks.
4. A successful reviewed result is exported to a new path through the same filters.
5. Normal shutdown destroys the sandbox. Interrupted `sbx` resources remain in an exact-ID registry for deliberate cleanup.
6. Local service records disappear when its process stops; trajectory and benchmark files persist only where the user requested them.

## Deliberate personal-scope limits

There is no remote bind address, multi-user authentication, public deployment, paid queue, or shared workspace. The local dashboard accepts only the offline benchmark. Adding shared access would be a different product boundary and is gated by `SECURITY_MODEL.md`.
