# Phase 5 — Contract and Integration Hardening

This runbook ties together the automated contract tests, read-only deployment verification, transaction evidence, portable bundle integrity checks, and optional live re-verification. It is a verification workflow for the testnet prototype, not a security audit.

## 1. Automated checks

From the repository root:

```bash
uv run --locked ruff check .
uv run --locked python -m pytest -q
```

Run the Solidity suite separately from `contracts/` with Foundry installed:

```bash
cd contracts
forge test -vvv
```

GitHub Actions runs the Python checks and the dedicated Foundry unit, fuzz, and invariant suite. A passing Python workflow does not substitute for the Foundry workflow.

## 2. Capture actual Base Sepolia evidence

Use only your own throwaway Base Sepolia wallets and a vault you deployed yourself. Set `BASE_SEPOLIA_RPC_URL` to your chosen Base Sepolia HTTPS RPC endpoint. Never paste private keys, bearer tokens, or RPC credentials into evidence files or PR comments.

### Verify an already-mined vault deployment

```bash
uv run --locked python -m scripts.verify_vault_deployment \
  "<DEPLOYMENT_TX_HASH>" \
  --expected-vault "<DEPLOYED_VAULT_ADDRESS>" \
  --expected-owner "<EXPECTED_OWNER_ADDRESS>" \
  --min-confirmations 12 \
  --output reports/deployment-verification.json
```

Use real values from your own deployment output. The verifier is read-only and does not deploy, sign, or broadcast. When you have a trusted runtime bytecode fingerprint, pass it with `--expected-bytecode-sha256`; a hash obtained from the same untrusted RPC is not an independent trust anchor.

### Build a successful-transfer evidence bundle

After an actual successful vault transfer, use its transaction hash, the expected public sender, the vault address, and the local Aegis SQLite audit database:

```bash
uv run --locked python -m scripts.build_evidence_bundle \
  --transaction-hash "<SUCCESSFUL_TRANSFER_TX_HASH>" \
  --expected-sender "<AGENT_PUBLIC_ADDRESS>" \
  --vault "<DEPLOYED_VAULT_ADDRESS>" \
  --audit-db "<PATH_TO_AEGIS_SQLITE_DATABASE>" \
  --min-confirmations 12 \
  --output-dir reports/phase5-success
```

For a deliberately failed, already-mined vault call, use `--expect-revert` and omit `--audit-db`:

```bash
uv run --locked python -m scripts.build_evidence_bundle \
  --transaction-hash "<MINED_REVERTED_CALL_TX_HASH>" \
  --expected-sender "<AGENT_PUBLIC_ADDRESS>" \
  --vault "<DEPLOYED_VAULT_ADDRESS>" \
  --expect-revert \
  --min-confirmations 12 \
  --output-dir reports/phase5-revert
```

A reverted receipt proves that the transaction failed according to the RPC's reported receipt status; it does not prove the exact revert reason. Do not submit a transaction merely to populate evidence, and do not describe a reverted call as a successful transfer.

## 3. Validate, re-query, and link the evidence

Validate a saved bundle's digest and internal consistency without network access:

```bash
uv run --locked python -m scripts.verify_evidence_bundle \
  reports/phase5-success/aegis-evidence-bundle.json
```

Re-query Base Sepolia for a passing bundle and compare the current transaction, receipt, confirmation depth, vault configuration, and (for successful transfers) SQLite audit record:

```bash
uv run --locked python -m scripts.reverify_testnet_bundle \
  reports/phase5-success/aegis-evidence-bundle.json \
  --expected-sender "<AGENT_PUBLIC_ADDRESS>" \
  --audit-db "<PATH_TO_AEGIS_SQLITE_DATABASE>" \
  --min-confirmations 12
```

For a reverted-call bundle, omit `--audit-db`. Then link the deployment record to the transaction bundle:

```bash
uv run --locked python -m scripts.verify_phase5_evidence \
  reports/deployment-verification.json \
  reports/phase5-success/aegis-evidence-bundle.json \
  --output reports/phase5-success/cross-check.json
```

Use the reverted-call bundle path instead when validating a reverted-call scenario. The cross-check requires a schema-v6 bundle and verifies both digests, the successful deployment receipt and confirmation threshold, matching Base Sepolia chain and vault address, owner identity, runtime bytecode fingerprint, and distinct deployment/transfer transaction hashes. It rejects mismatches rather than attempting to reconcile them.

## 4. Evidence interpretation and residual risks

- JSON SHA-256 digests detect accidental or later payload changes; they are not signatures and do not prove who generated an artefact.
- Chain information is observed through the configured RPC provider. Receipt-block hash comparisons are point-in-time consistency checks, not independent consensus proofs.
- Observed confirmations do not guarantee finality; chain reorganisations and provider errors remain possible.
- A runtime bytecode hash is a fingerprint, not proof of source-code reproducibility, compiler provenance, or contract safety.
- The deployment verifier checks the expected constructor owner and current `owner()`; it does not establish that the deployer was authorised or that the owner key is secure.
- Testnet transaction evidence is meaningful only for the actual hashes, addresses, RPC observations, and audit database supplied by the operator. Never invent sample hashes or report fixture tests as live-chain evidence.
- The vault remains unaudited and testnet-only. Keep simulated execution as the default, use throwaway Base Sepolia wallets, and do not use this workflow to authorise mainnet execution or real-fund custody.
- Offline evaluation validates deterministic workflow scenarios; it does not prove general LLM safety, invoice truth, or real-world business correctness.

## Phase 5 completion boundary

The repository's Phase 5 implementation includes automated Solidity unit/fuzz/invariant checks, read-only deployment and transaction verification, successful-transfer and reverted-call evidence modes, portable schema-v6 bundles, offline integrity checks, live re-verification, and this cross-artifact deployment-to-transfer consistency check. The workflow is implemented, but a live evidence report must be generated from a real Base Sepolia deployment and transaction; CI and fixtures are not substitutes for that operational evidence.
