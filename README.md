# Aegis Agent Wallet

A testnet-only AI-agent wallet prototype. Current milestones include deterministic policy checks, explainable risk scoring, fingerprint-bound approval, persistent transaction state, and a simulated execution controller.

## macOS setup

```bash
python3 --version
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"
python -m pytest -q
uvicorn app.main:app --reload
```

API docs: http://127.0.0.1:8000/docs

## Workflow endpoints

- `POST /transactions/propose` — evaluates policy and risk, persists a transaction, and assigns its initial status.
- `GET /transactions/{transaction_id}` — retrieves the persisted transaction state.
- `POST /transactions/{transaction_id}/approve` — approves only when the submitted fingerprint exactly matches the stored transaction fingerprint and the body explicitly says `APPROVE`.
- `POST /transactions/{transaction_id}/execute` — runs the simulated executor once. Retries return a conflict instead of repeating execution.
- `POST /policy/evaluate` and `POST /risk/assess` — standalone policy and risk evaluations.

A proposal is fingerprinted using SHA-256 over its canonical JSON representation. Approval is bound to that fingerprint. SQLite state is persisted in `aegis-workflow.sqlite3` by default (override with `AEGIS_DB_PATH`).

## Important limitations

- **Execution is simulated. No private key is loaded, no transaction is signed, and no RPC request is sent.** The simulated execution reference is not a real transaction hash.
- The sample token and recipient addresses are placeholders and must not be treated as verified Base Sepolia contracts or accounts.
- The API currently has no authentication or authorization. Run it only on a trusted local development machine; do not expose it to the public internet.
- The SQLite transitions prevent repeat execution in this prototype, but a production multi-worker system needs authenticated approval, robust key management, chain nonce/reconciliation logic, and a durable execution queue.
- Risk scores are simple heuristics, not a validated fraud detector.
- Hard policy blocks cannot be overridden by approval.

## Demo request

Use the interactive API documentation to submit a transaction. A small payment to an approved recipient should become `ready`. A transaction to an unknown recipient, or one with high risk, should become `awaiting_approval`. A policy violation becomes `blocked`.

For an approval-required transaction, copy its `fingerprint` from the response and pass it to the approval endpoint. Only then can the simulated executor run.
