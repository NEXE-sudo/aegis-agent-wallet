# Changelog

## 0.7.0

- Bind allow-listed token addresses to trusted chain ID, symbol, and decimals in policy configuration.
- Block proposals whose caller-supplied token metadata does not match trusted configuration.
- Add policy and API regression tests for metadata tampering.

## 0.6.0

- Configure the demo policy with Circle's published Base Sepolia USDC contract and six decimals.
- Reuse one canonical address constant across policy configuration and tests.
- Keep recipient addresses as placeholders and execution simulation-only.

## 0.5.0

- Add authenticated, read-only Base Sepolia chain ID and ERC-20 metadata inspection.
- Verify the RPC chain ID before reading token metadata; reject wrong-chain endpoints.
- Add tests for RPC failures, invalid addresses, missing contract code, and token metadata reads.
- Keep wallet signing and transaction submission disabled.


## 0.4.0

- Calculate daily spending from persisted transactions instead of trusting the proposal caller.
- Reserve pending transaction amounts and serialize budget checks with SQLite write locks.
- Re-evaluate policy and spending atomically immediately before simulated execution.
- Require separate bearer tokens for agent proposal creation and owner retrieval/approval/execution; protected routes fail closed when secrets are missing.
- Add idempotency keys for proposal retries and reject reuse with a different payload.
- Add additive SQLite migration for new accounting and idempotency columns.
- Isolate API tests in temporary databases and cover auth, daily limits, idempotency, and execution-time revalidation.
- Keep execution simulation-only; no wallet keys, signing, or blockchain RPC calls are added.
