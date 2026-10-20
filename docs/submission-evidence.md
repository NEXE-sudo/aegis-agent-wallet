# Reproducible submission evidence

This workflow produces a local hash manifest for the exact evidence files you select. It complements the evaluation reports and the Phase 5 evidence bundle; it does not replace either validator.

## 1. Generate evidence

From the repository root, using a clean, committed source checkout where practical:

```bash
uv run --locked python -m agent.eval --output-dir reports/submission/agent-eval
uv run --locked python -m agent.repeated_eval --runs 5 --output-dir reports/submission/repeated-eval
uv run --locked python -m scripts.build_evidence_bundle --output-dir reports/submission/evidence
```

The final command, without transaction arguments, runs offline evaluation and explicitly reports that no on-chain transfer evidence was supplied. It does not contact Base Sepolia to invent or infer a transfer.

If you have genuine operator-generated Base Sepolia evidence, use the existing [Phase 5 hardening runbook](phase-5-hardening.md) to verify it and build the relevant evidence bundle. Do not include private keys, bearer tokens, RPC credentials, or secret-bearing environment files in the manifest.

## 2. Build the manifest

Select the JSON and Markdown artifacts you intend to submit. For example:

```bash
uv run --locked python -m scripts.submission_manifest build \
  --root . \
  --output reports/submission/submission-manifest.json \
  --artifact reports/submission/agent-eval/agent-eval.json \
  --artifact reports/submission/agent-eval/agent-eval.md \
  --artifact reports/submission/repeated-eval/agent-repeat-eval.json \
  --artifact reports/submission/repeated-eval/agent-repeat-eval.md \
  --artifact reports/submission/evidence/aegis-evidence-bundle.json \
  --artifact reports/submission/evidence/aegis-evidence-bundle.md
```

The script writes JSON and Markdown manifests. Each artifact entry records a root-relative path, byte size, and SHA-256 digest. The manifest also records generation time, Python version, the current Git revision when available, and whether tracked source files differ from that revision. Artifact paths must resolve inside the chosen root.

## 3. Verify before packaging

```bash
uv run --locked python -m scripts.submission_manifest verify \
  reports/submission/submission-manifest.json --root .
uv run --locked python -m scripts.verify_evidence_bundle \
  reports/submission/evidence/aegis-evidence-bundle.json
```

Manifest verification is offline: it checks the manifest digest and each listed artifact's existence, size, and hash. Run the evidence-bundle validator separately for its domain-specific checks. If you modify or regenerate an artifact after building the manifest, rebuild the manifest and verify again.

## Interpretation and limitations

- A passing manifest means the selected files match the hashes recorded in that manifest. It is not a digital signature, trusted timestamp, or proof of authorship.
- A hash does not establish that a report's claims are true. Inspect the report and preserve the exact source revision used to generate it.
- A clean tracked source tree is useful provenance but does not attest to untracked dependencies, machine state, or a trusted build environment.
- Offline evaluation is not live LLM quality evidence or on-chain execution evidence.
- A live transfer claim requires a separately verified Base Sepolia transaction and relevant configuration/audit checks. Simulated results must be labelled as simulated.
- The vault remains unaudited and testnet-only. Never use this process with mainnet funds.
