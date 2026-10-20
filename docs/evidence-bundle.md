# Reproducible Aegis Evidence Bundle

The evidence bundle command combines the deterministic offline agent evaluation with an optional, read-only Base Sepolia transfer verification into a portable JSON report and Markdown summary.

## Offline evaluation bundle

Run from the repository root:

```bash
uv run --locked python -m scripts.build_evidence_bundle
```

The command writes:

- `reports/evidence-bundle/aegis-evidence-bundle.json`
- `reports/evidence-bundle/aegis-evidence-bundle.md`

This runs the existing deterministic scenarios with simulated execution and isolated temporary SQLite databases. Without a transaction hash, the report explicitly marks testnet evidence as not supplied; it must not be presented as proof of an on-chain transfer.

## Add a verified Base Sepolia transfer

Only when a real AegisVault transaction has already been submitted on Base Sepolia, run:

```bash
uv run --locked python -m scripts.build_evidence_bundle \
  --transaction-hash 0xYOUR_TRANSACTION_HASH \
  --expected-sender 0xYOUR_PUBLIC_AGENT_ADDRESS \
  --vault 0xYOUR_DEPLOYED_AEGIS_VAULT \
  --audit-db ./aegis.sqlite3 \
  --min-confirmations 12
```

The verifier uses read-only JSON-RPC calls, checks the chain and vault/token transfer events, compares the expected public sender, and cross-checks the transaction and audit trail against the SQLite database opened read-only. It never signs or submits a transaction. The configured RPC must be Base Sepolia (chain ID 84532). The verifier also fetches the RPC-reported block at the receipt height and requires its hash to match the receipt's `blockHash`; the JSON and Markdown reports record both hashes. A mismatch fails verification, which helps detect a receipt whose block is no longer canonical according to that RPC. The optional `--min-confirmations` setting (default `1`) requires a minimum observed depth calculated as `latest block - receipt block + 1`; the report records both block heights and the observed depth. These are point-in-time observations from one RPC, not a finality guarantee or independent consensus proof.

## Provenance and integrity

The JSON and Markdown reports record the UTC generation time, source revision when available, Python version, and Python implementation. The source revision is read from a valid `GITHUB_SHA` environment variable first and otherwise from the local Git checkout. If neither yields a valid commit hash, the report records `null`. This identifies the reported generation context; it does not prove that the checkout was clean or that the report was produced by a trusted party.

The bundle includes the deterministic evaluation results, optional transaction evidence, explicit per-check outcomes, provenance, and a SHA-256 digest of the canonical JSON payload (excluding the digest field itself). Recompute the digest after removing `bundle_sha256` to check that the saved JSON has not changed. The digest covers the provenance fields, detects accidental or subsequent modification, and is not a digital signature or proof of authorship.

A bundle fails if an included transfer cannot be verified, the expected sender differs, the audit cross-check fails, the receipt reports failure, the observed confirmation depth is below the requested minimum, or any offline scenario fails. A failed run still writes a report describing the outcome. The evidence schema is `aegis-evidence-bundle/v4`, which records receipt and canonical block hashes for supplied testnet evidence. No live LLM evaluation is run by this command.

## Evidence limits

- A matching receipt/canonical block hash only establishes agreement with the configured RPC at verification time; it is not a guarantee of finality or independent consensus.
- Offline evaluation is policy/workflow evidence, not proof of LLM safety or real-world invoice verification.
- An RPC response is provider-reported evidence, not an independent consensus proof.
- The AegisVault is unaudited and testnet-only. Never use mainnet keys, RPCs, tokens, or funds.
