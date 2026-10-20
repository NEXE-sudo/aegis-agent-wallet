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

## Add a verified reverted vault call

For a mined AegisVault call that failed on Base Sepolia, use the same read-only verification path with `--expect-revert`. Do not supply `--audit-db`, because a reverted call does not create a successful submitted-transfer record:

```bash
uv run --locked python -m scripts.build_evidence_bundle \
  --transaction-hash 0xYOUR_REVERTED_TRANSACTION_HASH \
  --expected-sender 0xYOUR_PUBLIC_AGENT_ADDRESS \
  --vault 0xYOUR_DEPLOYED_AEGIS_VAULT \
  --expect-revert \
  --min-confirmations 12
```

This verifies the mined failed receipt, call shape, sender, observed confirmation depth, and receipt/canonical block hash agreement. It does not decode or infer the Solidity revert reason, and it never signs or broadcasts a transaction.

## Provenance and integrity

The JSON and Markdown reports record the UTC generation time, source revision when available, Python version, and Python implementation. The source revision is read from a valid `GITHUB_SHA` environment variable first and otherwise from the local Git checkout. If neither yields a valid commit hash, the report records `null`. This identifies the reported generation context; it does not prove that the checkout was clean or that the report was produced by a trusted party.

The bundle includes the deterministic evaluation results, optional transaction evidence, explicit per-check outcomes, provenance, and a SHA-256 digest of the canonical JSON payload (excluding the digest field itself). Recompute the digest after removing `bundle_sha256` to check that the saved JSON has not changed. The digest covers the provenance fields, detects accidental or subsequent modification, and is not a digital signature or proof of authorship.

A bundle fails if the included transaction cannot be verified, the expected sender differs, a required audit cross-check fails, the receipt status does not match the selected success/revert mode, the observed confirmation depth is below the requested minimum, or any offline scenario fails. A failed run still writes a report describing the outcome. The generator emits schema `aegis-evidence-bundle/v5`, including separate status-aware successful-transfer and reverted-call modes. The verifier also accepts schema v4 successful-transfer bundles. No live LLM evaluation is run by the generator.

## Verify a saved bundle

Validate the saved JSON without regenerating the evaluation or contacting an RPC:

```bash
uv run --locked python -m scripts.verify_evidence_bundle \
  reports/evidence-bundle/aegis-evidence-bundle.json
```

Exit code `0` means the digest and internal consistency checks pass and the bundle's overall result is PASS. Exit code `1` means the bundle is internally consistent but its recorded overall result is FAIL. Exit code `2` means the file, digest, schema, or cross-field consistency is invalid. This checks the saved payload against its own SHA-256 digest and recorded results; it does not authenticate the author, prove the source revision was trusted, or independently re-query the chain.

## Evidence limits

- A matching receipt/canonical block hash only establishes agreement with the configured RPC at verification time; it is not a guarantee of finality or independent consensus.
- Offline evaluation is policy/workflow evidence, not proof of LLM safety or real-world invoice verification.
- An RPC response is provider-reported evidence, not an independent consensus proof.
- The AegisVault is unaudited and testnet-only. Never use mainnet keys, RPCs, tokens, or funds.
