// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {Ownable2Step} from "@openzeppelin/contracts/access/Ownable2Step.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";

/// @title AegisVault
/// @notice Unaudited, testnet-only token vault with one rotatable agent and UTC-day spending caps.
/// @dev This contract is a prototype enforcement boundary, not production custody software.
contract AegisVault is Ownable2Step, Pausable, ReentrancyGuard {
    using SafeERC20 for IERC20;

    /// @notice Maximum lifetime for an owner-approved transfer.
    uint256 public constant MAX_APPROVAL_LIFETIME = 7 days;

    struct TokenPolicy {
        bool allowed;
        uint256 perTxCap;
        uint256 dailyCap;
        uint256 approvalThreshold;
    }

    error UnauthorizedAgent();
    error AgentAddressZero();
    error AgentCannotBeOwner();
    error TokenAddressZero();
    error RecipientAddressInvalid();
    error TokenNotAllowed(address token);
    error InvalidTokenPolicy();
    error ZeroAmount();
    error PerTransactionCapExceeded(uint256 amount, uint256 cap);
    error DailyCapExceeded(uint256 spent, uint256 amount, uint256 cap);
    error TransferApprovalRequired(address token, address recipient, uint256 amount, bytes32 ref);
    error TransferApprovalExpired(uint256 expiry);
    error ApprovalDoesNotExist();
    error InvalidApprovalExpiry();
    error VaultAlreadyPaused();
    error VaultNotPaused();
    error UnexpectedTokenBalanceChange();

    event AgentChanged(address indexed previousAgent, address indexed newAgent);
    event TokenPolicyChanged(
        address indexed token,
        bool allowed,
        uint256 perTxCap,
        uint256 dailyCap,
        uint256 approvalThreshold
    );
    event RecipientChanged(address indexed recipient, bool allowed);
    event TransferApprovalSet(
        bytes32 indexed approvalId,
        address indexed token,
        address indexed recipient,
        uint256 amount,
        bytes32 ref,
        uint256 expiry
    );
    event TransferApprovalRevoked(bytes32 indexed approvalId);
    event TransferApprovalConsumed(
        bytes32 indexed approvalId,
        address indexed token,
        address indexed recipient,
        uint256 amount,
        bytes32 ref,
        address agent
    );
    event TokenTransferred(
        address indexed token,
        address indexed to,
        uint256 amount,
        bytes32 ref,
        address agent
    );
    event EmergencyWithdrawal(address indexed token, address indexed to, uint256 amount);

    /// @notice The single address authorized to call transferToken, or zero when revoked.
    address public agent;
    mapping(address token => TokenPolicy policy) private _tokenPolicies;
    /// @notice Whether each destination is allowlisted by the owner.
    mapping(address recipient => bool allowed) public recipients;
    mapping(address token => mapping(uint256 day => uint256 amount)) private _dailySpend;
    mapping(bytes32 approvalId => uint256 expiry) private _approvalExpiries;

    /// @notice Create a vault whose initial owner is the supplied address.
    /// @param initialOwner Address that receives two-step ownership and policy powers.
    constructor(address initialOwner) Ownable(initialOwner) {}

    /// @notice Set or rotate the single agent address.
    /// @dev The agent can only call transferToken; it receives no owner permissions.
    /// @param newAgent Nonzero address of the agent session key.
    function setAgent(address newAgent) external onlyOwner {
        if (newAgent == address(0)) revert AgentAddressZero();
        if (newAgent == owner()) revert AgentCannotBeOwner();
        address previousAgent = agent;
        agent = newAgent;
        emit AgentChanged(previousAgent, newAgent);
    }

    /// @notice Accept a two-step ownership transfer unless it would merge owner and agent powers.
    function acceptOwnership() public override {
        address newOwner = pendingOwner();
        if (newOwner != address(0) && newOwner == agent) revert AgentCannotBeOwner();
        super.acceptOwnership();
    }

    /// @notice Revoke the current agent.
    function revokeAgent() external onlyOwner {
        address previousAgent = agent;
        if (previousAgent == address(0)) revert AgentAddressZero();
        agent = address(0);
        emit AgentChanged(previousAgent, address(0));
    }

    /// @notice Configure whether a token may be transferred and its on-chain limits.
    /// @param token ERC-20 token contract.
    /// @param allowed Whether the token is currently permitted.
    /// @param perTxCap Maximum amount in one transfer, in token base units.
    /// @param dailyCap Maximum aggregate amount per UTC day bucket, in token base units.
    /// @param approvalThreshold Amount at or above which owner approval is required; zero disables this threshold.
    function setTokenPolicy(
        address token,
        bool allowed,
        uint256 perTxCap,
        uint256 dailyCap,
        uint256 approvalThreshold
    ) external onlyOwner {
        if (token == address(0)) revert TokenAddressZero();
        if (allowed && (perTxCap == 0 || dailyCap == 0)) revert InvalidTokenPolicy();
        _tokenPolicies[token] = TokenPolicy({
            allowed: allowed,
            perTxCap: perTxCap,
            dailyCap: dailyCap,
            approvalThreshold: approvalThreshold
        });
        emit TokenPolicyChanged(token, allowed, perTxCap, dailyCap, approvalThreshold);
    }

    /// @notice Allow or disallow a recipient for otherwise unapproved transfers.
    /// @param recipient Destination address.
    /// @param allowed Whether this recipient is allowlisted.
    function setRecipient(address recipient, bool allowed) external onlyOwner {
        if (recipient == address(0) || recipient == address(this)) {
            revert RecipientAddressInvalid();
        }
        recipients[recipient] = allowed;
        emit RecipientChanged(recipient, allowed);
    }

    /// @notice Approve one exact token, recipient, amount, and reference tuple.
    /// @param token ERC-20 token contract.
    /// @param to Destination address.
    /// @param amount Exact transfer amount in token base units.
    /// @param ref Exact application proposal reference.
    /// @param expiry Unix timestamp after which the approval is invalid, at most seven days away.
    function approveTransfer(
        address token,
        address to,
        uint256 amount,
        bytes32 ref,
        uint256 expiry
    ) external onlyOwner {
        if (token == address(0)) revert TokenAddressZero();
        if (to == address(0) || to == address(this)) revert RecipientAddressInvalid();
        if (amount == 0) revert ZeroAmount();
        if (
            expiry <= block.timestamp
                || expiry > block.timestamp + MAX_APPROVAL_LIFETIME
        ) revert InvalidApprovalExpiry();

        bytes32 id = _approvalId(token, to, amount, ref);
        _approvalExpiries[id] = expiry;
        emit TransferApprovalSet(id, token, to, amount, ref, expiry);
    }

    /// @notice Revoke the approval for one exact transfer tuple.
    /// @param token ERC-20 token contract.
    /// @param to Destination address.
    /// @param amount Exact transfer amount in token base units.
    /// @param ref Exact application proposal reference.
    function revokeApproval(address token, address to, uint256 amount, bytes32 ref)
        external
        onlyOwner
    {
        bytes32 id = _approvalId(token, to, amount, ref);
        if (_approvalExpiries[id] == 0) revert ApprovalDoesNotExist();
        delete _approvalExpiries[id];
        emit TransferApprovalRevoked(id);
    }

    /// @notice Pause agent transfers.
    function pause() external onlyOwner {
        if (paused()) revert VaultAlreadyPaused();
        _pause();
    }

    /// @notice Resume agent transfers.
    function unpause() external onlyOwner {
        if (!paused()) revert VaultNotPaused();
        _unpause();
    }

    /// @notice Execute one policy-limited ERC-20 transfer as the configured agent.
    /// @dev The daily bucket is UTC-aligned by `block.timestamp / 1 days`, not a rolling 24-hour window.
    /// @param token Allowed ERC-20 token contract.
    /// @param to Destination address.
    /// @param amount Transfer amount in token base units.
    /// @param ref Proposal fingerprint or other exact owner-approved reference.
    function transferToken(address token, address to, uint256 amount, bytes32 ref)
        external
        nonReentrant
    {
        if (agent == address(0) || msg.sender != agent) revert UnauthorizedAgent();
        if (paused()) revert EnforcedPause();
        if (token == address(0)) revert TokenAddressZero();
        if (to == address(0) || to == address(this)) revert RecipientAddressInvalid();

        TokenPolicy memory policy = _tokenPolicies[token];
        if (!policy.allowed) revert TokenNotAllowed(token);
        if (amount == 0) revert ZeroAmount();
        if (amount > policy.perTxCap) {
            revert PerTransactionCapExceeded(amount, policy.perTxCap);
        }

        uint256 day = block.timestamp / 1 days;
        uint256 spent = _dailySpend[token][day];
        if (spent > policy.dailyCap || amount > policy.dailyCap - spent) {
            revert DailyCapExceeded(spent, amount, policy.dailyCap);
        }

        bool approvalRequired =
            !recipients[to] || (policy.approvalThreshold != 0 && amount >= policy.approvalThreshold);
        if (approvalRequired) {
            bytes32 id = _approvalId(token, to, amount, ref);
            uint256 expiry = _approvalExpiries[id];
            if (expiry == 0) {
                revert TransferApprovalRequired(token, to, amount, ref);
            }
            if (expiry <= block.timestamp) revert TransferApprovalExpired(expiry);
            delete _approvalExpiries[id];
            emit TransferApprovalConsumed(id, token, to, amount, ref, msg.sender);
        }

        _dailySpend[token][day] = spent + amount;
        IERC20 asset = IERC20(token);
        uint256 vaultBalanceBefore = asset.balanceOf(address(this));
        uint256 recipientBalanceBefore = asset.balanceOf(to);
        asset.safeTransfer(to, amount);
        uint256 vaultBalanceAfter = asset.balanceOf(address(this));
        uint256 recipientBalanceAfter = asset.balanceOf(to);
        if (
            vaultBalanceAfter > vaultBalanceBefore
                || vaultBalanceBefore - vaultBalanceAfter != amount
                || recipientBalanceAfter < recipientBalanceBefore
                || recipientBalanceAfter - recipientBalanceBefore != amount
        ) revert UnexpectedTokenBalanceChange();

        emit TokenTransferred(token, to, amount, ref, msg.sender);
    }

    /// @notice Withdraw tokens as owner, including while the vault is paused.
    /// @param token ERC-20 token contract.
    /// @param to Destination address.
    /// @param amount Transfer amount in token base units.
    function emergencyWithdraw(address token, address to, uint256 amount)
        external
        onlyOwner
        nonReentrant
    {
        if (token == address(0)) revert TokenAddressZero();
        if (to == address(0) || to == address(this)) revert RecipientAddressInvalid();
        if (amount == 0) revert ZeroAmount();
        IERC20(token).safeTransfer(to, amount);
        emit EmergencyWithdrawal(token, to, amount);
    }

    /// @notice Read the current policy for a token.
    /// @param token ERC-20 token contract.
    /// @return policy The token's active on-chain limits.
    function tokenPolicy(address token) external view returns (TokenPolicy memory policy) {
        return _tokenPolicies[token];
    }

    /// @notice Read the amount spent for a token in the current UTC day bucket.
    /// @param token ERC-20 token contract.
    /// @return amount Total successful spend so far in today's bucket.
    function todaySpend(address token) external view returns (uint256 amount) {
        return _dailySpend[token][block.timestamp / 1 days];
    }

    /// @notice Read the expiry of an approval for one exact transfer tuple.
    /// @param token ERC-20 token contract.
    /// @param to Destination address.
    /// @param amount Exact transfer amount in token base units.
    /// @param ref Exact application proposal reference.
    /// @return expiry Unix timestamp, whether future or expired, or zero if no approval is stored.
    function approvalExpiry(address token, address to, uint256 amount, bytes32 ref)
        external
        view
        returns (uint256 expiry)
    {
        return _approvalExpiries[_approvalId(token, to, amount, ref)];
    }

    function _approvalId(address token, address to, uint256 amount, bytes32 ref)
        private
        pure
        returns (bytes32)
    {
        return keccak256(abi.encode(token, to, amount, ref));
    }
}
