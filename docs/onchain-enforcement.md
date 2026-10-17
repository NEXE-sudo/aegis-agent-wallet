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
They have **not been run in this environment** because `forge` is unavailable;
run `forge test` after installing Foundry and the pinned dependencies.

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

No deployment script or key-using command is part of Task 1.
