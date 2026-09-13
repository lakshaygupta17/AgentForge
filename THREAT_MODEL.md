# AgentForge Threat Model

## Scope and assets

This model covers one trusted user running AgentForge locally with untrusted repository text, model output, and test code. The important assets are the original source tree, `.env` credentials, host files and processes, inference credits, sandbox quota, repaired output, and audit data.

## Trust boundaries

1. **User to CLI:** approvals must be explicit for code transmission, sandbox execution, network cloning, and orphan cleanup.
2. **Repository to agents:** repository text is data, not an instruction. Reads are bounded and sensitive paths are excluded.
3. **Model to tools:** model tool calls are untrusted structured input. Schemas, iteration limits, policy, and repository boundaries validate them.
4. **Host to sandbox:** only a filtered snapshot and policy-approved action environment cross this boundary. Host credentials do not.
5. **Host to providers:** Hugging Face and E2B credentials remain on the host control plane; selected source leaves only after approval.
6. **Service to browser:** the service binds to loopback, rejects non-local Host/Origin values, and serves no external dashboard assets.

## Abuse cases and controls

| Threat | Example | Main controls | Residual risk |
|---|---|---|---|
| Prompt injection | README tells the agent to leak `.env` | repository content treated as data; sensitive-file filters; bounded tools; transmission approval | a secret pasted into an ordinary source file may evade filename filters, so review content before live transmission |
| Path escape | `../../private.txt` | resolved-path containment in repository, transfer, edit, and command working-directory layers | filesystem or platform defects outside Python checks |
| Shell injection | arguments contain `;`, pipes, or newlines | argument arrays, no implicit host shell, shell-control rejection, forbidden shells | an allowed executable may itself interpret a rich configuration file |
| Credential theft | model asks tests to receive `HF_TOKEN` | host credential broker; credential-shaped action variables always denied; host environment filtered; `.env` excluded | third-party provider SDKs still hold credentials in the AgentForge host process |
| Network exfiltration | test uploads code | deny-all sandbox networking by default | enabling network access broadens risk; hostname policy alone is not a proxy and is not exposed as a completed live workflow |
| Resource exhaustion | fork bomb or huge output | CPU, memory, process, time, output, step, retry, file-count, and session caps | provider-side outages and quota exhaustion remain possible |
| Malicious patch | model alters unrelated files | existing-text-only restricted patches, write policy, complete diff, independent review, export to a new path | reviewer quality depends on model behavior; user should inspect important output |
| Sandbox escape | hostile test exploits guest runtime | disposable microVM backends for live repair; source is copied rather than mounted | sandbox/provider vulnerabilities are outside AgentForge's direct control |
| Orphaned resources | host crashes after VM creation | exact owned-resource registry and opt-in reaper; normal forced cleanup | power loss before registry persistence can leave a provider resource requiring provider-side inspection |
| Log leakage | provider error includes a token | recursive event redaction, text redaction, sanitized CLI/service errors | redaction is defense in depth, not proof arbitrary prose contains no secret |
| Cross-user access | another person guesses a run ID | remote access unavailable; loopback-only, one-person service | local malware or another OS account with equivalent access is outside the application boundary |
| Supply-chain attack | cloned repository abuses Git metadata | HTTPS exact-host allowlist, shallow no-tags clone, file protocol disabled, `.git` stripped | remote host compromise can still supply malicious source, which remains untrusted |

## Security invariants

- Live repair never modifies the selected source directory.
- No approval capability implies another capability.
- A credential-shaped environment name is never delegated to a sandbox command.
- Cleanup targets are exact IDs previously recorded as AgentForge-owned.
- The personal HTTP service cannot bind remotely or start arbitrary live repair.
- Default network access is denied.

## Out-of-scope assumptions

The operating-system account, Python environment, installed `sbx`/E2B SDK, and provider control planes are trusted dependencies. AgentForge is not hardened against a compromised host administrator, malicious kernel, provider breach, or physical attacker. It is not a public or multi-tenant service.

## Review triggers

Review this document before adding remote binding, sandbox networking, package installation inside repairs, repository credential access, multi-user state, persistent public APIs, new patch capabilities, or a new sandbox/provider backend.
