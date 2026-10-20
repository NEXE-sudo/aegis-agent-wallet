# Base Sepolia receipt confirmation checks

The owner-authenticated `GET /transactions/{transaction_id}/receipt` endpoint now reports a point-in-time receipt assessment for a submitted AegisVault transaction. It checks the configured RPC chain ID, retrieves the receipt, reads the reported latest block, and compares the receipt's block hash with the block hash currently reported for that height.

## Status meanings

- `pending`: the RPC has no receipt for the transaction hash.
- `included`: a successful receipt is present in the currently reported canonical block, but has fewer than 12 observed confirmations.
- `confirmed`: a successful receipt is present in the currently reported canonical block and has at least 12 observed confirmations.
- `failed`: the receipt status is reverted and its block hash currently matches the block reported at that height.
- `reorged`: the receipt's block hash differs from the hash currently reported for that block height.

The response includes the receipt block number/hash, observed confirmation count, required threshold, and whether the block hash currently matches. A pending receipt has no block information and zero confirmations.

## Safety and interpretation

This is a read-only, point-in-time observation from one configured RPC provider. Twelve confirmations are a configurable-in-code operational threshold, not a guarantee of consensus finality. Providers may be stale, inconsistent, or dishonest; a later reorganisation can change the assessment. Re-query before making consequential decisions, and use independent trusted infrastructure for production systems.

The endpoint does not mutate transaction workflow status, release governance reservations, reconcile token balances, or prove that the intended token transfer event occurred. A receipt with status `success` only says the EVM transaction did not revert; it is not by itself proof that the intended transfer succeeded. The existing evidence-bundle verifier performs additional transaction/calldata/log/audit cross-checks for operator-generated evidence.

Simulation remains the default. This applies only to the optional, unaudited Base Sepolia vault path (chain ID 84532); never use mainnet or real funds.
