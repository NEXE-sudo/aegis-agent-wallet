# Aegis Agent Wallet — Security Self-Assessment (Draft)

**Assessment status:** Draft, implementation-based self-assessment; not an
independent audit, penetration test, certification, or claim of conformance.

**Reference:** [OWASP Top 10 for Agentic Applications 2026](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/).
The risk names and identifiers below follow the OWASP 2026 framework. This
assessment is scoped to the current prototype code and documentation, reviewed on
2026-10-16. It is not a complete control-by-control audit of the OWASP guidance.

## 1. System scope and assumptions

Aegis is a Python/FastAPI local prototype with a constrained agent package, SQLite
workflow store, deterministic policy engine, heuristic risk engine, dependency-free
frontend, external market-data and JSON-RPC providers, and an optional
OpenAI-compatible LLM endpoint. Simulated execution is the default. Actual signing
and submission is restricted to explicitly enabled `base_sepolia_vault`
execution on Base Sepolia (chain ID `84532`). Direct EOA token transfers are
disabled. The unaudited, testnet-only AegisVault contract provides an independent
policy boundary for vault-held tokens.

The assessment assumes the operator controls the local host, environment variables,
and SQLite file. It does not assume that an RPC provider, market-data provider, or
LLM provider is trusted. The API is intended to remain bound to loopback for local
use. The unauthenticated health and standalone policy/risk evaluation endpoints
should not be exposed to untrusted callers.

### Assessment labels

- **Partial:** Relevant safeguards exist, but they do not fully address the risk or
  have not been independently tested.
- **Limited / out of scope:** The capability is absent or the relevant attack
  surface is intentionally small, but this is not proof that the category is
  eliminated.
- **Not independently verified:** The implementation claim has not been confirmed
  by an external security assessment.

No category below should be read as a pass or certification.

## 2. OWASP 2026 risk mapping

| ID and risk | Current implementation evidence | Residual risk and assessment |
| --- | --- | --- |
| **ASI01 — Agent Goal Hijack** | The original tool loop treats free-form invoice content as untrusted and checks proposal calls against structured `authorized_payment`. A separate deterministic governance-queue path reads owner-created structured tasks, mandate and payee records from SQLite; it does not accept caller-supplied payment fields, and it constructs proposals from the registered payee plus the task's integer amount. The governance decision includes rationale, rule references, mandate hash and hash-linked audit evidence. | **Partial.** The persisted queue is deterministic and is not yet an LLM reasoner over the queue. Owner-supplied task fields can still be incorrect, `verified` is an owner attestation rather than independent identity verification, the available-balance value is a planning snapshot, and proposed amounts are conservatively retained without cancellation reconciliation. Existing wallet policy and vault checks remain authoritative. The existing LLM evaluation is still narrow and does not prove general prompt-injection resistance. |
| **ASI02 — Tool Misuse & Exploitation** | The agent exposes only proposal, market-quote, and limited-status tools. Tool names, argument fields, and payment fields are validated; unsupported calls are rejected. API-side policy and execution revalidation add independent checks. | **Partial.** A valid proposal can reserve budget and create workflow records. The system has no general-purpose tool sandbox or comprehensive abuse/rate-limit controls. Unauthenticated evaluation endpoints provide a policy oracle. |
| **ASI03 — Identity & Privilege Abuse** | Separate static agent and owner bearer tokens gate workflow actions. Owner-only governance endpoints create/update mandates and payees and queue structured tasks; the agent token can only process pending tasks. The agent package does not load or send the owner token. Missing/blank or identical workflow tokens fail closed; comparisons are constant-time. | **Partial.** Tokens remain development credentials without user identity, scoped credential issuance, rotation/revocation, MFA, session management, or production secret storage. A stolen owner token has owner authority, including marking a payee as verified. |
| **ASI04 — Agentic Supply Chain Vulnerabilities** | Python dependencies are declared and locked; CI installs with `uv --locked`, runs tests and Ruff, and performs a dependency audit. The optional model endpoint and model name are configured explicitly by environment variables. | **Partial / not independently verified.** A lockfile and CI audit do not establish that every dependency, build action, model, provider, or transitive service is trustworthy. Model provenance, provider security, deployment artifact signing, and a full supply-chain review are not documented as implemented. |
| **ASI05 — Unexpected Code Execution (RCE)** | The agent has a fixed API-tool allowlist and does not expose a code-execution or shell tool. The API accepts typed, bounded request fields; the frontend is served as a fixed same-origin document with a nonce-scoped script policy. | **Limited.** There is no general-purpose sandbox or independent code-execution boundary for compromised Python dependencies, the host, or the configured LLM provider. The local application process and dependencies remain trusted components. |
| **ASI06 — Memory & Context Poisoning** | The agent has no persistent conversational memory feature. Structured payment fields are separated from invoice text, and proposal tool arguments are checked against those structured fields. Workflow persistence stores transaction state, not a reusable LLM memory store. | **Partial.** A task's structured payment may itself be wrong or poisoned upstream. Current-turn context can still influence LLM output and user-facing explanations; no general context provenance or external memory integrity system is implemented. |
| **ASI07 — Insecure Inter-Agent Communication** | The prototype provides a single agent package and does not implement agent-to-agent delegation or messaging. Agent-to-API authentication uses the agent token; non-loopback API and LLM URLs require HTTPS in their respective clients/configuration. | **Limited / out of scope.** There is no inter-agent protocol to assess. This does not assess the security of an external LLM provider, RPC provider, or any future multi-agent extension. |
| **ASI08 — Cascading Failures** | Policy limits, SQLite-serialized spend reservations, idempotency handling, explicit errors, sanitized provider failures, and retry of the same persisted signed transaction reduce selected duplicate-spend and uncertain-broadcast cases. Tests exercise workflow and retry behavior with mocked providers. | **Partial.** There is no distributed transaction coordinator, circuit breaker, RPC quorum, automatic receipt/finality or reorganization reconciliation, or production incident recovery. Local reservations are not chain balances. Provider failure and stale state remain operational risks. |
| **ASI09 — Human-Agent Trust Exploitation** | The agent cannot approve, reject, or change vault policy, but it can request execution of eligible proposals through the vault-only endpoint. Owner actions remain separate and fingerprint-bound approvals expire. Hard policy blocks cannot be overridden. The UI/API distinguish a proposal from `executed_simulated` and `submitted_onchain`; the latter is not described as finality. | **Partial.** A bearer token is not proof of a human's identity or informed review. Operators can over-trust a risk score, model explanation, quote, token metadata, or RPC response. No multi-person review, comprehension check, or formal approval evidence is provided. |
| **ASI10 — Rogue Agents** | The agent's capability set is small; its API client uses only the agent token and lacks owner routes. Server-side owner authentication and workflow policy remain required even if the agent misbehaves. The offline mode is deterministic and the optional LLM tool loop rejects non-allowlisted calls. | **Partial.** There is no independent runtime monitor, anomaly detector, per-agent credential revocation service, or fleet-management control. Compromise of the application, agent token, host, or supplied structured payment remains in scope. |
| **Vault-mode permission boundary** | The server holds only the agent session key. The contract independently enforces its agent role, pause state, per-token caps, recipient allowlist or exact expiring approval, and one UTC-day bucket. Owner-only status and approval-helper routes never sign or read a key. | **Unaudited, testnet-only.** A compromised agent/server can spend within configured caps, send to allowed recipients, or use an already existing exact approval. It cannot change vault policy or withdraw funds without the separately held owner key. A compromised owner key can change policy and roles, approve transfers, pause/unpause, or withdraw. Malicious/upgradeable tokens, token blacklists, RPC/provider failures, chain timestamp boundaries, and a single-agent compromise remain risks. |

## 3. Cross-cutting controls and limitations

### Transaction and key boundary

- `AEGIS_EXECUTION_MODE` defaults to `simulated`; the only real-transfer mode is
  `base_sepolia_vault`, which requires a deployed vault, an agent session key, and
  HTTPS `BASE_SEPOLIA_RPC_URL`. The retired direct EOA mode is rejected rather
  than selecting simulation.
- Vault execution revalidates proposal integrity, policy, spending, risk, and approval,
  checks chain ID `84532`, and signs locally. Eligible agent requests use the same
  vault-enforced path; owner approval remains mandatory when required.
- The private key is not accepted in an API body, returned in API responses, or
  intentionally logged. The process and SQLite database may contain sensitive
  material, including persisted signed transaction bytes for retry.
- No mainnet support, production key custody, or real-fund transfer capability is
  implemented. Direct EOA transfer code is no longer part of the execution path.
- In `base_sepolia_vault`, `AEGIS_TESTNET_PRIVATE_KEY` is the agent session key
  only. The Aegis server must never hold the vault owner key. Vault policy is a
  second enforcement layer, not a replacement for Aegis workflow review or a
  guarantee that the configured token behaves safely.

### Permission tiers in vault mode

| Tier | Authority | Explicit limits |
| --- | --- | --- |
| **Agent session key** | Submit `transferToken` calls as the configured single agent. | Cannot set policy, allow recipients, approve, pause/unpause, rotate the agent, transfer ownership, or withdraw. Transfers remain subject to on-chain per-transaction and UTC-day caps and the recipient/approval rules. |
| **Owner key** | Configure token policies and recipient flags; approve exact transfers; pause/unpause; rotate/revoke the agent; transfer ownership; emergency-withdraw. | This key is not held by the Aegis Python server. If compromised, the attacker can change or bypass the effective limits and move vault assets. |
| **Vault contract** | Enforce the rules in deployed bytecode for vault-held token transfers. | Unaudited and testnet-only. It cannot prove token authenticity, prevent a token's own blacklist/upgrade behavior, provide rolling-24-hour caps, or protect against owner-key compromise. |

### Contract known-risk list

These limits apply to the contract and operational model described in
[on-chain enforcement](onchain-enforcement.md):

- The Solidity contract and its dependencies have not been audited, formally
  verified, or independently tested. Compiler correctness, dependency behavior,
  deployment configuration, and operational procedures remain risks. It is for
  Base Sepolia testnet only.
- There is one rotatable agent. A compromised agent can spend within the
  configured caps, send to allowlisted recipients, or use an already-existing
  exact owner approval. Approval does not raise either cap.
- A compromised owner key can change/remove limits, change recipient flags,
  approve transfers, pause or unpause, rotate the agent, transfer ownership, and
  emergency-withdraw assets.
- The daily cap is keyed to `block.timestamp / 1 days`, a UTC-aligned day bucket
  rather than a rolling 24-hour window. Chain timestamps are not a trusted
  wall-clock source.
- The token remains an independent trust boundary. A malicious or upgradeable
  token can change behavior; an issuer blacklist or pause can prevent transfers.
  Fee-on-transfer and other balance deltas are rejected by the agent-transfer
  checks, but token `balanceOf` behavior is still trusted.
- Emergency withdrawal is intentionally owner-only and bypasses agent pause and
  spending policy. Approval references are opaque `bytes32` values: the contract
  binds the exact reference but does not prove it is an authentic Aegis
  fingerprint or that an off-chain proposal is authentic.
- There is no mainnet target, production key custody, monitoring, automated
  recovery, or on-chain finality/reorganization handling.

### Additional residual findings from the 2026-10-16 review

- The review marked approval-expiry logic as partially resolved; it noted that validation remained duplicated between `WorkflowStore.approve` and `_execute_with_validation`.
- The review marked release hygiene as partially resolved, citing changelog/release-note inconsistencies and formatting concerns in its 2026-10-16 snapshot.

### Data, logs, and audit

- Request logs use route metadata and status rather than raw URLs, headers, or
  exception text. Selected provider errors are sanitized.
- Workflow transitions append audit events in the same SQLite transaction as the
  state change. Database triggers prevent ordinary row updates/deletes; an actor
  who controls the database file can bypass this application-level protection.
- Logs, environment variables, database files, backups, and any provider payloads
  still require operator protection. This draft makes no claim of cryptographic
  audit integrity or comprehensive personal-data minimization.

### Testing evidence

The project has deterministic unit/API/workflow tests, mocked RPC/provider tests,
frontend browser tests when their prerequisites are present, and an offline agent
evaluation with scripted scenarios. These tests are useful regression evidence;
they do not prove resistance to real-world prompt injection, provider compromise,
malicious dependencies, or attacks against a deployed system. The evaluation does
not make a real transfer and does not assess LLM quality.

**TODO:** Attach the exact commit and CI run link used for final submission review.
Foundry tests have not been run in the current environment; report
`TODO: run forge test` until a real `forge test` run is performed. Do not infer
contract test results from Python test runs.
**TODO:** Record any independent penetration-test or external review findings;
do not describe an unperformed review as completed.

## 4. Prioritized follow-up work

These are recommendations, not implemented capabilities:

1. Keep deployment loopback-only until a separately reviewed deployment design
   defines TLS, network controls, identity, authorization, rate limiting, secret
   management, and operational monitoring.
2. Define independent provenance and approval for `authorized_payment`; do not
   treat task input or invoice text as a trusted payment source.
3. Add adversarial testing for prompt injection, tool argument boundary cases,
   provider errors, credential separation, and workflow race/retry behavior.
4. Specify secret rotation and incident-response procedures for owner and agent
   tokens, testnet keys, RPC credentials, and SQLite backups.
5. Before any broader chain or custody scope, design balance reconciliation,
   transaction finality/reorganization handling, key custody, and stronger audit
   integrity as separate reviewed changes.
6. Track dependency, CI action, model, and external-service provenance as part of a
   maintained supply-chain process.

## 5. Review items requiring owner input

- **TODO:** Confirm the intended assessment audience, business owner, and final
  approval date.
- **TODO:** Confirm whether any external security review or regulatory mapping is
  required by the FinTechathon submission; do not imply formal compliance.
- **Evidence (pending owner-run test; not yet verified):**
  Transaction hash: TODO (supplied by the owner after a real transfer)
  Verify with: `uv run python scripts/verify_testnet_evidence.py <TRANSACTION_HASH> --expected-sender "<TODO: throwaway wallet address>" --audit-db "<path>"`
  This check ties the on-chain transfer to the sending wallet and to Aegis's audit record; it does not prove the wallet key was held only by Aegis.
- Vault address, deployment transaction hash, successful vault transfer hash,
  and reverted bypass transaction hash remain `TODO`. Their read-only verification
  commands are listed in [on-chain enforcement evidence](onchain-enforcement.md#submission-evidence);
  no live chain values are claimed here.
- Foundry contract tests: **TODO: run forge test**. Do not report a Solidity
  test result until `forge test` has actually been run and its output reviewed.
- **TODO:** Review each residual-risk statement against the exact submission
  commit and update this assessment if code changes before submission.

## 6. References

- [OWASP Top 10 for Agentic Applications 2026](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/)
- [Aegis threat model](threat-model.md)
- [Aegis runtime configuration](runtime-configuration.md)
- [AegisVault design, runbook, and evidence placeholders](onchain-enforcement.md)
- [Aegis agent guide](agent.md)
- [Aegis workflow state transitions](workflow-state-transitions.md)
