# Guarded Base Sepolia live test runbook

This runner is a convenience wrapper around the repository's existing deployment,
vault, API, and evidence-verification paths. It is **not** an audit or a guarantee of
finality. AegisVault is unaudited and testnet-only; the default execution mode remains
simulated. Never use mainnet or real funds.

## Safety model

- Without `--broadcast`, the runner validates local configuration and prints a plan.
  It does not query RPC, start the API, sign, or broadcast.
- With `--broadcast`, it first queries `eth_chainId` from `BASE_SEPOLIA_RPC_URL`
  and refuses unless the result is exactly `84532`; it then checks the owner and
  agent wallet balances before the first transaction.
- RPC URLs and credentials are passed to subprocesses through environment variables,
  not command-line arguments, and are redacted from saved logs. Private keys and
  bearer tokens are never printed or written to reports.
- Owner signing uses the existing Foundry keystore alias `OWNER_KEYSTORE_ACCOUNT`.
  The agent key is read from `AEGIS_TESTNET_PRIVATE_KEY`; use a separate throwaway
  agent account that is not the owner.
- Each broadcast step is marked in the state file before it starts. If a broadcast
  step fails or its outcome is uncertain, the runner stops and will not retry that
  step automatically. Inspect the transaction and the partial report manually before
  deciding whether to use a fresh output directory.
- The runner only supports the application's current Base Sepolia demo USDC policy.
  It configures the two checked-in demo allowlist addresses; those are placeholders,
  not verified identities. The transfer recipient must be a different, owner-chosen
  public address so the proposal requires owner approval.
- Minimum confirmation depth defaults to 12. Observed confirmations are provider
  observations and do not guarantee finality.

## Prerequisites

1. Use two separate throwaway Base Sepolia accounts: one owner account in Foundry's
   keystore and one agent account. Fund **both** with Base Sepolia test ETH. The
   runner requires at least 0.01 test ETH on each before deployment.
2. The owner account must have enough Base Sepolia demo USDC if you pass
   `--fund-amount-base-units`; otherwise the vault must already hold enough USDC.
3. Install the project's locked Python dependencies and Foundry. From the repository
   root, run `uv sync --locked --extra dev`; from `contracts/`, run
   `forge install OpenZeppelin/openzeppelin-contracts@v5.7.0` and
   `forge install foundry-rs/forge-std@v1.17.0`.
4. Read [on-chain enforcement](onchain-enforcement.md) and
   [Phase 5 hardening](phase-5-hardening.md) before broadcasting. Get test ETH from
   a public Base Sepolia faucet and demo USDC from the token's published testnet
   faucet/documentation or the project's documented test-token source. Availability
   and eligibility vary; use the public faucet information linked by the repository
   or the token issuer, and do not trust unsolicited faucet links.

## Configure secrets locally

Do not put values in shell history, source files, command-line flags, reports, or chat.
Use an interactive secret prompt in your local terminal. For example:

```bash
read -r -s -p "Base Sepolia RPC URL: " BASE_SEPOLIA_RPC_URL; printf '\n'; export BASE_SEPOLIA_RPC_URL
read -r -s -p "Agent private key (throwaway testnet key): " AEGIS_TESTNET_PRIVATE_KEY; printf '\n'; export AEGIS_TESTNET_PRIVATE_KEY
read -r -s -p "Agent API token: " AEGIS_AGENT_TOKEN; printf '\n'; export AEGIS_AGENT_TOKEN
read -r -s -p "Owner API token: " AEGIS_APPROVAL_TOKEN; printf '\n'; export AEGIS_APPROVAL_TOKEN
```

Then set public/non-secret values. Use the actual owner address and the account alias
already present in your local Foundry keystore:

```bash
export AEGIS_VAULT_OWNER_ADDRESS="<YOUR_THROWAWAY_OWNER_ADDRESS>"
export OWNER_KEYSTORE_ACCOUNT="<YOUR_FOUNDRY_KEYSTORE_ALIAS>"
export AEGIS_API_URL="http://127.0.0.1:8000"
```

The angle-bracket strings above are placeholders; replace them locally. Do not run
the command with the placeholders unchanged. The owner address must match the account
alias. Do not pass any private key or RPC URL as a CLI argument. Unset secret
environment variables when finished.

## Run a dry run first

Choose a recipient address you control and which is not one of the two demo allowlist
placeholders in `scripts/live_testnet_run.py`. The amount is in USDC base units
(six decimals): `1000000` means 1 USDC. Maximum is 50 USDC (50000000 base units).

```bash
uv run --locked python -m scripts.live_testnet_run \
  --recipient "<YOUR_UNLISTED_TESTNET_RECIPIENT>" \
  --amount-base-units 1000000
```

Expected: `Dry run: PASS (no RPC calls or broadcasts)`, followed by the numbered
plan and a saved `reports/live/partial-report.json`. If configuration validation
fails, correct the named environment variable or public argument. No chain action
should have occurred.

## Explicitly opt into the live run

First ensure the API port in `AEGIS_API_URL` is unused. The runner starts a dedicated
localhost API in `base_sepolia_vault` mode using `reports/live/workflow.sqlite3`.
It refuses to reuse an already-running API process because that process may be using
a different database or execution mode.

```bash
uv run --locked python -m scripts.live_testnet_run \
  --broadcast \
  --recipient "<YOUR_UNLISTED_TESTNET_RECIPIENT>" \
  --amount-base-units 1000000 \
  --fund-amount-base-units 1000000
```

Omit `--fund-amount-base-units` only if the vault already has sufficient demo USDC.
This command sends real Base Sepolia transactions, consumes test ETH, configures the
vault, creates a proposal, records owner approval, submits the exact on-chain
approval, executes one transfer, and runs the existing bypass demo. Review each
prompt from Foundry if it requests the keystore password; that password must remain
local and must not be supplied as a CLI flag.

Expected success begins with `Live Base Sepolia run: PASS` and reports the actual
vault address plus deployment, successful transfer, and reverted bypass transaction
hashes. Verify every hash independently using the output files and public Base
Sepolia explorer. Never replace a missing hash with an example.

If a step fails, the runner stops, writes `reports/live/partial-report.json`, and
keeps logs/state in `reports/live/`. A failed broadcast step is marked uncertain
and is **not automatically retried**. Check the actual transaction receipt and saved
state first. If the RPC chain ID is not 84532, the runner refuses before broadcasting.
If either wallet has less than 0.01 test ETH, use a public Base Sepolia faucet and
rerun only after checking that no broadcast step is marked uncertain.

## Evidence validation and documentation

On a completed run, the runner writes:

- `reports/live/deployment-verification.json`
- `reports/live/success/aegis-evidence-bundle.json` and Markdown summary
- `reports/live/revert/aegis-evidence-bundle.json` and Markdown summary
- `reports/live/phase5-success.json` and `phase5-revert.json`
- Reverification logs, submission evaluation reports, manifest, and preflight output

The runner uses the repository's existing deployment verifier, schema-v6 evidence
bundle builder/verifier, Phase 5 cross-check, live reverifier, offline evaluation,
manifest, and submission preflight. Keep the generated reports together; hashes
prove file consistency, not authorship or transaction finality.

Only after the saved deployment and both bundles pass verification, fill the
documentation evidence blocks:

```bash
uv run --locked python -m scripts.fill_evidence_docs
```

To include observed CI references for the exact final source revision, pass the
actual run URLs after checking the run's tested SHA:

```bash
uv run --locked python -m scripts.fill_evidence_docs \
  --python-ci-url "<OBSERVED_PYTHON_ACTIONS_RUN_URL>" \
  --foundry-ci-url "<OBSERVED_FOUNDRY_ACTIONS_RUN_URL>"
```

This documentation update modifies tracked files. Commit the reviewed documentation
change, regenerate the submission manifest for the new source revision, and rerun
the manifest verification and `scripts.submission_preflight`. Do not claim the
preflight passed if it reports a source-revision mismatch or dirty tracked tree.
The evidence-document filler is offline and refuses to use absent, failing, fixture,
or cross-artifact-mismatched bundles.

## Submission limits

The run proves only what the saved provider-observed evidence supports. It does not
prove independent consensus, finality, authorship, contract safety, key exclusivity,
production readiness, or LLM safety. The vault remains unaudited and Base Sepolia
only. If no real transfer and reverted call are verified before the deadline, submit
the offline evaluation and clearly label live execution evidence as not supplied.
