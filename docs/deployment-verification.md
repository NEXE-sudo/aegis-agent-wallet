# AegisVault Deployment Verification

This runbook verifies an **already-submitted** AegisVault deployment using read-only JSON-RPC calls to Base Sepolia (chain ID `84532`). It does not deploy a contract, sign a transaction, or broadcast anything.

The verifier checks that:

- the configured RPC reports Base Sepolia;
- the transaction exists and is a contract-creation transaction;
- the encoded constructor owner matches the expected owner;
- the transaction receipt succeeded and reports the expected vault address;
- the receipt block hash matches the RPC's block hash at that height;
- the observed confirmation depth meets the chosen threshold;
- runtime bytecode exists at the vault address, and optionally matches an expected SHA-256 fingerprint;
- the current on-chain `owner()` matches the expected owner.

These checks strengthen deployment traceability but do not prove that the source code produced the deployed bytecode, authenticate the deployer, or replace an independent audit.

## Verify a real deployment

Run from the repository root after setting `BASE_SEPOLIA_RPC_URL` to an HTTPS Base Sepolia endpoint. Use the actual deployment transaction hash, contract address and owner from your own Foundry output; do not use invented values.

```bash
uv run --locked python -m scripts.verify_vault_deployment \
  "<DEPLOYMENT_TX_HASH>" \
  --expected-vault "<DEPLOYED_VAULT_ADDRESS>" \
  --expected-owner "<EXPECTED_OWNER_ADDRESS>" \
  --min-confirmations 12 \
  --output reports/deployment-verification.json
```

The command exits with code `0` only when all requested checks pass. A missing receipt, reverted deployment, wrong chain, constructor/owner mismatch, wrong created address, non-canonical receipt block hash, insufficient confirmations, missing bytecode or expected-fingerprint mismatch fails verification.

To compare the deployed runtime bytecode with a fingerprint already recorded in a verified schema-v6 evidence bundle, pass its `vault_configuration.bytecode_sha256` value:

```bash
uv run --locked python -m scripts.verify_vault_deployment \
  "<DEPLOYMENT_TX_HASH>" \
  --expected-vault "<DEPLOYED_VAULT_ADDRESS>" \
  --expected-owner "<EXPECTED_OWNER_ADDRESS>" \
  --expected-bytecode-sha256 "<EXPECTED_BYTECODE_SHA256>" \
  --min-confirmations 12 \
  --output reports/deployment-verification.json
```

The verifier reports the deployer address, deployment transaction hash, receipt and canonical block hashes, observed confirmation depth, current runtime bytecode SHA-256, constructor owner, and current owner. The JSON includes a SHA-256 digest of the evidence payload (excluding the digest field). This digest detects accidental or subsequent changes only; it is not a signature or proof of authorship.

## Interpretation and limits

- All chain data is reported by the configured RPC. Matching block hashes are a point-in-time consistency check, not independent consensus or a guarantee of finality.
- The constructor owner is decoded from the final ABI word of the contract-creation input and checked against the expected owner. The current owner is checked independently using `eth_call`; a later ownership transfer will therefore fail the expected-owner check.
- Runtime bytecode is hashed as returned by the RPC. Matching a known hash establishes byte-for-byte agreement with that reference, not source-code reproducibility or contract safety.
- The evidence does not establish that the deployer address was authorised, that the deployment used trusted compiler settings, or that the deployed contract was audited.
- The AegisVault remains unaudited and testnet-only. Never use this workflow to authorise mainnet execution or real-fund custody.
