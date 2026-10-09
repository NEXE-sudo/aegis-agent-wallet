# Changelog

## 0.4.0

- Calculate daily spending from persisted transactions instead of trusting the proposal caller.
- Reserve pending transaction amounts and serialize budget checks with SQLite write locks.
- Re-evaluate policy and spending atomically immediately before simulated execution.
- Require separate bearer tokens for agent proposal creation and owner retrieval/approval/execution; protected routes fail closed when secrets are missing.
- Add idempotency keys for proposal retries and reject reuse with a different payload.
- Add additive SQLite migration for new accounting and idempotency columns.
- Isolate API tests in temporary databases and cover auth, daily limits, idempotency, and execution-time revalidation.
- Keep execution simulation-only; no wallet keys, signing, or blockchain RPC calls are added.
