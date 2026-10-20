# AegisVault On-chain Enforcement

> **Unaudited, testnet-only prototype.** This contract is limited to Base Sepolia
> (chain ID `84532`) and is not suitable for real funds. It is not an audit,
> certification, or guarantee of security.

## Design

`AegisVault` holds ERC-20 tokens and allows one configured agent address to call
`transferToken`. The owner is initialized at deployment and uses OpenZeppelin
`Ownable2Step` for ownership transfers. The owner can rotate or revoke the one
agent, configure per-token limits, manage recipient allowlisting and exact
transfer approvals, pause the agent path, and make an emergency withdrawal even
while paused.

Each enabled token has a per-transaction cap, a daily cap, and an optional
approval threshold, all measured in token base units. A transfer requires an
owner approval if the recipient is not allowlisted or if a nonzero threshold is
met or exceeded. That approval is bound to the exact token, recipient, amount,
and `bytes32` reference, expires within at most seven days, and is consumed on
use. An approval never bypasses either spending cap.

Spend accounting uses `block.timestamp / 1 days`, an epoch-aligned UTC day
bucket. This is not a rolling 24-hour limit: a transfer near UTC midnight and
another shortly after midnight can use separate daily caps. Agent transfers use
OpenZeppelin `SafeERC20` and check that both the vault's balance decreases and
the recipient's balance increases by exactly the requested amount. Tokens with
transfer fees, rebasing during transfer, or other balance behavior that violates
those checks are rejected.

## Invariants enforced by the contract

- Only the current nonzero agent can call `transferToken`; owner functions are
  restricted to the owner, and ownership transfer is two-step.
- Transfers are disabled while paused. Emergency withdrawal remains owner-only
  and available while paused.
- A token must be explicitly allowed; zero amounts, per-transaction overages,
  and daily-cap overages always revert, regardless of approval.
- A destination must be allowlisted or have a valid approval for the exact
  `(token, recipient, amount, ref)` tuple, unless a configured threshold also
  requires approval. An approval is removed on successful use.
- A successful agent transfer must debit the vault and credit the recipient by
  exactly the requested amount. Emergency withdrawals intentionally remain
  available for owner recovery and use `SafeERC20` without that policy check.
- The vault makes no arbitrary external calls and uses no upgradeability,
  `delegatecall`, or `selfdestruct`.

Foundry unit, fuzz, and invariant tests are in `contracts/test/AegisVault.t.sol`.
The repository now defines a dedicated GitHub Actions workflow at
`.github/workflows/foundry-checks.yml` that installs Foundry, installs the pinned
OpenZeppelin and `forge-std` dependencies, and runs `forge test -vvv` for contract
changes. The workflow's actual result must be checked on each pull request; Python
checks do not establish Solidity test results. Locally, run `forge test` from
`contracts/` after installing Foundry and the pinned dependencies.

## Read-only vault configuration inspection

The owner-authenticated endpoint
`GET /chain/base-sepolia/vault/{address}/status?token_address={token_address}`
reads the RPC-reported chain, bytecode, owner, agent, pause state, and token policy
for a supplied vault/token pair. It checks basic structural conditions and, when
`AEGIS_VAULT_ADDRESS` is set, reports whether the supplied vault matches that
local setting. When `AEGIS_VAULT_OWNER_ADDRESS` is configured, it compares the
observed owner; when `AEGIS_TESTNET_PRIVATE_KEY` is supplied, it derives the
public address and compares it with the on-chain agent without returning the key.
Missing expected values are reported as unperformed comparisons, not successful
matches. A wrong-chain RPC,
malformed ABI response, or RPC failure fails closed.

This is a read-only snapshot from one configured RPC, not proof that the address
is the intended deployment, that the contract is audited, or that the observed
state will remain unchanged. Verify addresses against trusted deployment records
before any testnet execution. The endpoint never signs or submits transactions. The response's structural-validity flag checks only the observed fields; it cannot confirm the intended owner or agent unless compared with trusted deployment records.

The portable evidence-bundle generator now records a related deployment snapshot for successful-transfer and reverted-call evidence: the current bytecode SHA-256 fingerprint, owner/agent addresses, pause state, and selected token policy. Re-verification re-reads the current values and fails on drift. This snapshot is captured at verification time, not at the transaction block, and a matching fingerprint does not establish who deployed the code or whether the deployment is trusted.

## Trust model and known risks

The vault limits what a compromised Aegis server or agent session key can
transfer: the agent cannot edit policy, add recipients, issue approvals, unpause,
change the agent, or withdraw funds. Even with an exact owner approval, the
per-transaction and daily caps remain enforced. The owner key is a higher-trust
boundary: a compromised owner can change policy and recipients, approve
transfers, pause/unpause, rotate the agent, transfer ownership, and withdraw
funds.

The contract does not establish that a token is genuine or safe. A malicious or
upgradeable token can change its behavior; a token such as USDC may independently
blacklist the vault or a recipient, pause its own transfers, or otherwise refuse
transfers. Exact balance checks reject fee-on-transfer behavior detectable in
the immediate transfer, but do not make an arbitrary token trustworthy.

Other known risks:

- This contract has not been audited. Solidity compiler correctness, dependency
  behavior, deployment configuration, and operational procedures remain risks.
- The daily cap resets at a UTC bucket boundary rather than over a rolling
  24-hour period.
- There is a single agent address. A compromised or misconfigured agent can
  choose any transfer that fits the on-chain limits and approval rules.
- A compromised owner key can remove or raise limits and move all vault funds.
- `block.timestamp` is a chain timestamp, with the usual block-producer
  constraints; it is not a trusted wall clock.
- The token balance checks are dependent on the token's `balanceOf` semantics.
  Rebasing, reflection, callback, or upgrade behavior can cause transfers to
  revert or make accounting assumptions invalid.
- Emergency withdrawal intentionally bypasses the agent pause and spending
  policy; it remains owner-only.
- Approval references are opaque bytes32 values. The contract does not verify
  that a reference is an Aegis fingerprint or that an off-chain proposal is
  authentic.
- No mainnet deployment target, production key custody, monitoring, recovery, or
  formal verification is included.

## Pinned toolchain and dependency setup

The Foundry project pins Solidity `0.8.28`. OpenZeppelin Contracts `v5.7.0` was
verified as the latest stable v5 tag available when this change was prepared;
Foundry test helpers are pinned to `forge-std` `v1.17.0`. From the repository
root, after installing Foundry, run:

```bash
cd contracts
forge install OpenZeppelin/openzeppelin-contracts@v5.7.0
forge install foundry-rs/forge-std@v1.17.0
forge test
```

## Owner deployment and operation runbook

This is an operational guide for **Base Sepolia only** (chain ID `84532`).
The contract and procedure are unaudited and testnet-only. Never use a mainnet
RPC, account, token address, or deployment target.

Install Foundry if it is not already available. From the repository root, fetch
the exact pinned libraries and run the local test suite:

```bash
cd contracts
forge install OpenZeppelin/openzeppelin-contracts@v5.7.0
forge install foundry-rs/forge-std@v1.17.0
forge test
```

Use two separate throwaway Base Sepolia accounts and keep their keys only in
Foundry keystores. The **owner account** is the only account used for deployment
and owner operations. The **agent account** is configured below and is the only
key the Aegis server may later hold. Fund both accounts with Base Sepolia ETH for
their respective transactions. Never paste keys into commands, chat, source,
or logs.

From `contracts/`, set local shell values. Replace the placeholders with values
you obtained yourself; do not commit this shell state:

```bash
export BASE_SEPOLIA_RPC_URL="<BASE_SEPOLIA_RPC_URL>"
export OWNER_ADDRESS="<OWNER_ADDRESS>"
export OWNER_KEYSTORE_ACCOUNT="<FOUNDRY_OWNER_ACCOUNT_NAME>"
export AGENT_ADDRESS="<AGENT_ADDRESS>"
export VAULT="<DEPLOYED_VAULT_ADDRESS>"
export USDC="0x036cbd53842c5426634e7929541ec2318f3dcf7e"
```

The owner address must belong to `OWNER_KEYSTORE_ACCOUNT`, which holds the
owner's signing key in Foundry's keystore. The deploy script accepts only the
public owner address in `AEGIS_VAULT_OWNER_ADDRESS`; the key is selected by
Foundry at invocation time and is never read or logged by the script. The script
refuses any chain other than Base Sepolia.

### Deploy

```bash
AEGIS_VAULT_OWNER_ADDRESS="$OWNER_ADDRESS" forge script script/Deploy.s.sol:Deploy \
  --rpc-url "$BASE_SEPOLIA_RPC_URL" \
  --account "$OWNER_KEYSTORE_ACCOUNT" \
  --broadcast
```

Take the deployed vault address and deployment transaction hash from Foundry's
real output and set `VAULT` to that address. Do not copy example or invented
deployment results into evidence.

### Configure the vault

Confirm the RPC is connected to Base Sepolia before sending transactions:

```bash
cast chain-id --rpc-url "$BASE_SEPOLIA_RPC_URL"
```

Set the Aegis demo USDC policy. Values are in USDC base units (six decimals):
50 USDC per transfer, 150 USDC per UTC day, and approval required at or above
50 USDC:

```bash
cast send "$VAULT" \
  "setAgent(address)" "$AGENT_ADDRESS" \
  --rpc-url "$BASE_SEPOLIA_RPC_URL" --chain 84532 \
  --account "$OWNER_KEYSTORE_ACCOUNT"

cast send "$VAULT" \
  "setTokenPolicy(address,bool,uint256,uint256,uint256)" \
  "$USDC" true 50000000 150000000 50000000 \
  --rpc-url "$BASE_SEPOLIA_RPC_URL" --chain 84532 \
  --account "$OWNER_KEYSTORE_ACCOUNT"

cast send "$VAULT" \
  "setRecipient(address,bool)" "0x2222222222222222222222222222222222222222" true \
  --rpc-url "$BASE_SEPOLIA_RPC_URL" --chain 84532 \
  --account "$OWNER_KEYSTORE_ACCOUNT"

cast send "$VAULT" \
  "setRecipient(address,bool)" "0x3333333333333333333333333333333333333333" true \
  --rpc-url "$BASE_SEPOLIA_RPC_URL" --chain 84532 \
  --account "$OWNER_KEYSTORE_ACCOUNT"
```

The two addresses above are the checked-in demo policy recipients, not verified
real-world identities. Only allow recipients you intentionally control or have
independently checked.

### Deposit test USDC and read state back

The owner account must already hold test USDC. This transfers tokens from the
keystore's selected owner account into the vault; it does not use the agent key:

```bash
cast send "$USDC" \
  "transfer(address,uint256)" "$VAULT" <AMOUNT_BASE_UNITS> \
  --rpc-url "$BASE_SEPOLIA_RPC_URL" --chain 84532 \
  --account "$OWNER_KEYSTORE_ACCOUNT"
```

Read the deployment and configuration back without signing:

```bash
cast call "$VAULT" "owner()(address)" --rpc-url "$BASE_SEPOLIA_RPC_URL"
cast call "$VAULT" "agent()(address)" --rpc-url "$BASE_SEPOLIA_RPC_URL"
cast call "$VAULT" \
  "tokenPolicy(address)(bool,uint256,uint256,uint256)" "$USDC" \
  --rpc-url "$BASE_SEPOLIA_RPC_URL"
cast call "$VAULT" \
  "recipients(address)(bool)" "0x2222222222222222222222222222222222222222" \
  --rpc-url "$BASE_SEPOLIA_RPC_URL"
cast call "$VAULT" \
  "todaySpend(address)(uint256)" "$USDC" \
  --rpc-url "$BASE_SEPOLIA_RPC_URL"
cast call "$USDC" "balanceOf(address)(uint256)" "$VAULT" \
  --rpc-url "$BASE_SEPOLIA_RPC_URL"
```

### Approve one exact transfer

Replace the three placeholders with the transaction proposal's actual token,
recipient, and amount. `REF` must be the exact 32-byte proposal fingerprint
encoded as hex. The expiry below is one hour after the local Unix time; ensure
the chain timestamp has not passed it when the transfer is submitted:

```bash
export PROPOSAL_TOKEN="<TOKEN_ADDRESS>"
export PROPOSAL_RECIPIENT="<RECIPIENT_ADDRESS>"
export PROPOSAL_AMOUNT="<AMOUNT_BASE_UNITS>"
export PROPOSAL_REF="<0X_PLUS_64_HEX_DIGITS>"
EXPIRY=$(($(date +%s) + 3600))

cast send "$VAULT" \
  "approveTransfer(address,address,uint256,bytes32,uint256)" \
  "$PROPOSAL_TOKEN" "$PROPOSAL_RECIPIENT" "$PROPOSAL_AMOUNT" "$PROPOSAL_REF" "$EXPIRY" \
  --rpc-url "$BASE_SEPOLIA_RPC_URL" --chain 84532 \
  --account "$OWNER_KEYSTORE_ACCOUNT"

cast call "$VAULT" \
  "approvalExpiry(address,address,uint256,bytes32)(uint256)" \
  "$PROPOSAL_TOKEN" "$PROPOSAL_RECIPIENT" "$PROPOSAL_AMOUNT" "$PROPOSAL_REF" \
  --rpc-url "$BASE_SEPOLIA_RPC_URL"
```

### Pause, resume, revoke, or recover

Every state-changing command below requires the owner keystore account:

```bash
cast send "$VAULT" "pause()" \
  --rpc-url "$BASE_SEPOLIA_RPC_URL" --chain 84532 \
  --account "$OWNER_KEYSTORE_ACCOUNT"

cast call "$VAULT" "paused()(bool)" --rpc-url "$BASE_SEPOLIA_RPC_URL"

cast send "$VAULT" "unpause()" \
  --rpc-url "$BASE_SEPOLIA_RPC_URL" --chain 84532 \
  --account "$OWNER_KEYSTORE_ACCOUNT"

cast send "$VAULT" "revokeAgent()" \
  --rpc-url "$BASE_SEPOLIA_RPC_URL" --chain 84532 \
  --account "$OWNER_KEYSTORE_ACCOUNT"

cast send "$VAULT" \
  "emergencyWithdraw(address,address,uint256)" "$USDC" "<RECOVERY_ADDRESS>" "<AMOUNT_BASE_UNITS>" \
  --rpc-url "$BASE_SEPOLIA_RPC_URL" --chain 84532 \
  --account "$OWNER_KEYSTORE_ACCOUNT"
```

These commands submit transactions to Base Sepolia and consume testnet ETH.
Inspect each real transaction result before continuing. Do not treat an
unconfirmed or failed transaction as completed configuration.
## Submission evidence

No deployment or transfer evidence is claimed yet. Keep each value as `TODO`
until it is supplied by the operator from a real Base Sepolia run. Do not fill
these fields with test fixtures or invented values.

- **Vault address:** `TODO: provide the deployed AegisVault address`
  - Verify deployment receipt and compare its `contractAddress` with this value:
    `cast receipt "<DEPLOYMENT_TX_HASH>" --rpc-url "$BASE_SEPOLIA_RPC_URL"`
- **Deployment transaction hash:** `TODO: provide the real Base Sepolia deployment transaction hash`
  - Verify receipt:
    `cast receipt "<DEPLOYMENT_TX_HASH>" --rpc-url "$BASE_SEPOLIA_RPC_URL"`
- **Executed `transferToken` transaction hash:** `TODO: provide the real successful transfer hash`
  - Verify vault destination, calldata, vault/token events, agent sender, and
    (when available) the stored proposal fingerprint:
    `uv run python scripts/verify_testnet_evidence.py "<TRANSFER_TX_HASH>" --vault "<VAULT_ADDRESS>" --expected-sender "<AGENT_ADDRESS>" --audit-db "<AEGIS_DB_PATH>"`
- **Blocked bypass transaction hash:** `TODO: provide a real reverted bypass hash`
  - Inspect the transaction and its receipt; the receipt status must be `0x0`,
    and the transaction's `to` and calldata must identify the intended vault
    bypass attempt:
    `cast tx "<BLOCKED_BYPASS_TX_HASH>" --rpc-url "$BASE_SEPOLIA_RPC_URL" && cast receipt "<BLOCKED_BYPASS_TX_HASH>" --rpc-url "$BASE_SEPOLIA_RPC_URL"`

For a mined reverted vault call, the read-only verifier can validate the Base
Sepolia chain, transaction destination, sender, `transferToken` calldata, receipt
failure status, receipt/canonical block hash agreement, and observed confirmation
depth without requiring success-only transfer events:

```bash
uv run --locked python scripts/verify_testnet_evidence.py "<REVERTED_TX_HASH>" \
  --vault "<VAULT_ADDRESS>" \
  --expected-sender "<AGENT_ADDRESS>" \
  --expect-revert \
  --min-confirmations 12
```

This mode confirms only that the submitted vault call was mined with a failed
receipt and that the configured RPC reports the stated transaction/block data.
It does **not** recover or infer the Solidity revert reason, and it does not
cross-check a successful Aegis transaction record in SQLite. A pending or
successful transaction is not evidence of a blocked bypass. These observations
come from one RPC and are not proof of finality. The contract is unaudited and
testnet-only.
