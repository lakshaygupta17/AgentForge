# AgentForge Local Access and Future Authentication Design

This document defines the access boundary for the personal AgentForge service. It is a design contract, not a claim that AgentForge is ready for public deployment.

## Current mode: one person, one computer

The current service has no login screen because it can only bind to `127.0.0.1`. That address is the computer's private loopback interface: another device cannot connect to it over Wi-Fi, Ethernet, or the internet.

The HTTP layer also rejects requests with a non-local `Host` or `Origin`, sends a restrictive Content Security Policy, loads no remote dashboard assets, executes one run at a time, and accepts only the deterministic offline benchmark. The API cannot currently request a hosted model call, provide a repository path, launch a cloud sandbox, or supply a shell command.

These restrictions are the current authentication boundary. They are suitable for this project's explicitly personal, non-deployed scope, but they are not sufficient for a shared or remotely accessible service.

## Data ownership today

- Run status and lifecycle events exist only in process memory and disappear when the service stops.
- The dashboard stores no browser cookie, token, repository content, or credential.
- Benchmark results contain fixed fixture metrics rather than user source code.
- The process may load the project `.env` for other CLI commands, but the service response model never exposes those values.
- A maximum of 100 completed run records is retained, and only one worker executes at once.

## Mandatory design for any future shared mode

Remote binding must remain disabled until every control below is implemented and tested.

### Identity and sessions

1. Put the service behind HTTPS and a maintained reverse proxy.
2. Authenticate with a standards-based OpenID Connect provider rather than storing passwords in AgentForge.
3. Exchange the identity response for a short-lived, opaque server session stored in an `HttpOnly`, `Secure`, `SameSite=Strict` cookie.
4. Rotate the session identifier after login and privilege changes; expire it after bounded inactivity and absolute lifetime.
5. Require anti-CSRF tokens for state-changing browser requests and reject untrusted origins.

### Authorization and isolation

1. Give every task an immutable owner identity at creation.
2. Check ownership on status, event, cancellation, artifact, and deletion operations—not only on the dashboard.
3. Allocate a separate sandbox, workspace directory, trajectory namespace, and artifact namespace per run.
4. Never reuse writable workspaces or model conversation state between users.
5. Use unguessable run identifiers, while still treating identifier knowledge as insufficient authorization.
6. Apply per-user concurrency, storage, inference, and sandbox-time quotas before scheduling work.

### Credentials

1. Replace direct host environment access with a credential broker.
2. Give a sandbox only a short-lived capability for the exact provider and operation it needs.
3. Never write raw provider tokens into a workspace, prompt, trajectory, event, result, or browser response.
4. Revoke delegated credentials when a run finishes, fails, times out, or is cancelled.

### Audit and lifecycle

1. Record login, run creation, approvals, policy decisions, cancellation, artifact access, and administrative actions in an append-only audit log.
2. Redact before persistence and test redaction against nested payloads and provider errors.
3. Define automatic retention and deletion for workspaces, logs, artifacts, and account data.
4. Add an orphaned-sandbox reaper and verify cleanup after process crashes.
5. Rate-limit authentication and task endpoints, and make denial responses reveal no account or run existence across owners.

## Gate for changing the bind address

Changing `agentforge serve` from loopback to `0.0.0.0`, a LAN address, or a public interface is a security-sensitive feature—not a configuration tweak. It requires the identity, authorization, isolation, credential, audit, cleanup, and abuse tests above, followed by an explicit documentation and security review. Until then, the CLI intentionally provides no host option.
