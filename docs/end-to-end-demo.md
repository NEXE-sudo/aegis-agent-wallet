# Reproducible Aegis Demo

This walkthrough demonstrates the owner-governed payment workflow without sending
transactions to a blockchain. It is designed for a clean local checkout and produces
machine-readable evaluation evidence.

## 1. Prepare the environment

Use Python 3.11 or newer and install the locked development dependencies:

```bash
uv sync --locked --extra dev
```

Create distinct local credentials and choose a dedicated SQLite file. Do not commit
these values or reuse production credentials:

```bash
export AEGIS_AGENT_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export AEGIS_APPROVAL_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export AEGIS_DB_PATH="./aegis-demo.sqlite3"
export AEGIS_EXECUTION_MODE="simulated"
```

Start the local API in terminal one:

```bash
uv run --locked uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open [http://127.0.0.1:8000/](http://127.0.0.1:8000/) for the local control centre.
Use the agent token for agent operations and the owner token for governance, review,
and audit operations. The frontend holds tokens in browser memory only; reloading or
disconnecting clears them.

## 2. Run deterministic evaluation evidence

In terminal two, from the repository root:

```bash
uv run --locked python -m agent.eval
uv run --locked python -m agent.repeated_eval --runs 5
```

Both commands use simulated execution and isolated temporary SQLite databases. They
do not need a live RPC endpoint, market-data provider, or LLM credentials. The repeated
run writes:

- `reports/agent-repeat-eval.json` — run-by-run status, scenario pass counts, and rates.
- `reports/agent-repeat-eval.md` — human-readable summary suitable for review.

A successful run exits with code zero. Preserve the generated files with the submission
evidence; do not edit the counts or describe a failed run as a pass. The timestamped
single-run report is generated separately by `agent.eval`.

## 3. Demonstrate the owner-governed queue

Use the Governance view in the frontend to complete these steps in order:

1. Save a mandate with a single token budget, a monthly budget, per-payment limit,
   approval threshold, treasury buffer, and owner-maintained available-balance value.
2. Register a Base Sepolia payee using the known USDC token metadata and an address
   verified out of band. Mark it verified only after the owner has checked it.
3. Queue a structured task using the registered payee ID, invoice ID, token symbol,
   integer amount in base units, and a short description.
4. Process pending tasks. The agent may recommend a proposal only from the saved task,
   mandate, and payee records; free text is not an authority to alter payment fields.
5. Inspect the persisted decision. If a proposal is produced, review its status using
   the owner view; a `ready` or `awaiting_approval` status is not evidence of payment.
6. Open the audit view and verify the governance and decision chain. Export JSON or CSV
   if a portable review artefact is needed.

The agent cannot approve, reject, execute, or modify the owner-managed mandate or payee.
A payee marked verified is owner-attested, not independently identity-verified. The
available balance is planning data, not a live blockchain balance.

## 4. Capture honest evidence

For a submission, retain:

- The exact Git commit SHA and the local Ruff/pytest results.
- The unmodified JSON and Markdown evaluation reports.
- A screenshot of the governance queue and decision result with secrets hidden.
- The audit verification result and a JSON/CSV export if relevant.
- A clear statement that the walkthrough used simulated execution.

Do not claim a live transfer, deployed-contract verification, or testnet evidence unless
an owner has separately performed and verified that operation. The on-chain path is
restricted to the AegisVault on Base Sepolia (chain ID 84532), requires explicit setup,
and is not needed for this demo.
