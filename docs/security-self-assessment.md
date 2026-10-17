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
and submission is restricted to explicitly enabled Base Sepolia testnet mode
(chain ID `84532`).

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
| **ASI01 — Agent Goal Hijack** | The agent prompt treats free-form invoice content as untrusted. Every proposal tool call is checked against the task's structured `authorized_payment` before the API request. The offline prompt-injection evaluation checks that an injected invoice cannot replace the structured recipient. The agent cannot approve or execute. | **Partial.** This check only compares a tool call with structured task data; it does not prove that data's origin or correctness, prevent all prompt injection, or validate the LLM's explanations. The scenarios are narrow deterministic tests, not a general adversarial evaluation. |
| **ASI02 — Tool Misuse & Exploitation** | The agent exposes only proposal, market-quote, and limited-status tools. Tool names, argument fields, and payment fields are validated; unsupported calls are rejected. API-side policy and execution revalidation add independent checks. | **Partial.** A valid proposal can reserve budget and create workflow records. The system has no general-purpose tool sandbox or comprehensive abuse/rate-limit controls. Unauthenticated evaluation endpoints provide a policy oracle. |
| **ASI03 — Identity & Privilege Abuse** | Separate static agent and owner bearer tokens gate workflow actions. Missing/blank or identical workflow tokens fail closed; comparisons are constant-time. Owner-only routes cover approval, rejection, cancellation, execution, full reads, and audit access. The agent package does not load or send the owner token. | **Partial.** Tokens are development credentials without user identity, scoped credential issuance, rotation/revocation, MFA, session management, or production secret storage. A stolen owner token has owner authority. |
| **ASI04 — Agentic Supply Chain Vulnerabilities** | Python dependencies are declared and locked; CI installs with `uv --locked`, runs tests and Ruff, and performs a dependency audit. The optional model endpoint and model name are configured explicitly by environment variables. | **Partial / not independently verified.** A lockfile and CI audit do not establish that every dependency, build action, model, provider, or transitive service is trustworthy. Model provenance, provider security, deployment artifact signing, and a full supply-chain review are not documented as implemented. |
| **ASI05 — Unexpected Code Execution (RCE)** | The agent has a fixed API-tool allowlist and does not expose a code-execution or shell tool. The API accepts typed, bounded request fields; the frontend is served as a fixed same-origin document with a nonce-scoped script policy. | **Limited.** There is no general-purpose sandbox or independent code-execution boundary for compromised Python dependencies, the host, or the configured LLM provider. The local application process and dependencies remain trusted components. |
| **ASI06 — Memory & Context Poisoning** | The agent has no persistent conversational memory feature. Structured payment fields are separated from invoice text, and proposal tool arguments are checked against those structured fields. Workflow persistence stores transaction state, not a reusable LLM memory store. | **Partial.** A task's structured payment may itself be wrong or poisoned upstream. Current-turn context can still influence LLM output and user-facing explanations; no general context provenance or external memory integrity system is implemented. |
| **ASI07 — Insecure Inter-Agent Communication** | The prototype provides a single agent package and does not implement agent-to-agent delegation or messaging. Agent-to-API authentication uses the agent token; non-loopback API and LLM URLs require HTTPS in their respective clients/configuration. | **Limited / out of scope.** There is no inter-agent protocol to assess. This does not assess the security of an external LLM provider, RPC provider, or any future multi-agent extension. |
| **ASI08 — Cascading Failures** | Policy limits, SQLite-serialized spend reservations, idempotency handling, explicit errors, sanitized provider failures, and retry of the same persisted signed transaction reduce selected duplicate-spend and uncertain-broadcast cases. Tests exercise workflow and retry behavior with mocked providers. | **Partial.** There is no distributed transaction coordinator, circuit breaker, RPC quorum, automatic receipt/finality or reorganization reconciliation, or production incident recovery. Local reservations are not chain balances. Provider failure and stale state remain operational risks. |
| **ASI09 — Human-Agent Trust Exploitation** | The agent cannot approve or execute. Owner actions are separate and fingerprint-bound approvals expire. Hard policy blocks cannot be overridden. The UI/API distinguish a proposal from `executed_simulated` and `submitted_onchain`; the latter is not described as finality. | **Partial.** A bearer token is not proof of a human's identity or informed review. Operators can over-trust a risk score, model explanation, quote, token metadata, or RPC response. No multi-person review, comprehension check, or formal approval evidence is provided. |
| **ASI10 — Rogue Agents** | The agent's capability set is small; its API client uses only the agent token and lacks owner routes. Server-side owner authentication and workflow policy remain required even if the agent misbehaves. The offline mode is deterministic and the optional LLM tool loop rejects non-allowlisted calls. | **Partial.** There is no independent runtime monitor, anomaly detector, per-agent credential revocation service, or fleet-management control. Compromise of the application, agent token, host, or supplied structured payment remains in scope. |

## 3. Cross-cutting controls and limitations

### Transaction and key boundary

- `AEGIS_EXECUTION_MODE` defaults to `simulated`. `base_sepolia` requires a valid
  `AEGIS_TESTNET_PRIVATE_KEY` and HTTPS `BASE_SEPOLIA_RPC_URL`; invalid settings
  fail rather than selecting simulation.
- Testnet execution is owner-only, revalidates proposal integrity, policy, spending,
  risk, and approval, checks chain ID `84532`, and signs locally.
- The private key is not accepted in an API body, returned in API responses, or
  intentionally logged. The process and SQLite database may contain sensitive
  material, including persisted signed transaction bytes for retry.
- No mainnet support, production key custody, or real-fund transfer capability is
  implemented.

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
**TODO:** Record any independent penetration-test or external review findings; do
not describe an unperformed review as completed.

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
  Verify with: `uv run python scripts/verify_testnet_evidence.py <TRANSACTION_HASH>`
- **TODO:** Review each residual-risk statement against the exact submission
  commit and update this assessment if code changes before submission.

## 6. References

- [OWASP Top 10 for Agentic Applications 2026](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/)
- [Aegis threat model](threat-model.md)
- [Aegis runtime configuration](runtime-configuration.md)
- [Aegis agent guide](agent.md)
- [Aegis workflow state transitions](workflow-state-transitions.md)
