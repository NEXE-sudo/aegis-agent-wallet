// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {Test} from "forge-std/Test.sol";
import {StdInvariant} from "forge-std/StdInvariant.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {AegisVault} from "../src/AegisVault.sol";

contract MockToken is IERC20 {
    uint256 public override totalSupply;
    mapping(address account => uint256) public override balanceOf;
    mapping(address account => mapping(address spender => uint256)) public override allowance;

    function mint(address account, uint256 amount) external {
        totalSupply += amount;
        balanceOf[account] += amount;
        emit Transfer(address(0), account, amount);
    }

    function transfer(address to, uint256 amount) public virtual override returns (bool) {
        require(to != address(0) && balanceOf[msg.sender] >= amount, "transfer invalid");
        balanceOf[msg.sender] -= amount;
        balanceOf[to] += amount;
        emit Transfer(msg.sender, to, amount);
        return true;
    }

    function approve(address spender, uint256 amount) external override returns (bool) {
        allowance[msg.sender][spender] = amount;
        emit Approval(msg.sender, spender, amount);
        return true;
    }

    function transferFrom(address from, address to, uint256 amount)
        external
        override
        returns (bool)
    {
        uint256 permitted = allowance[from][msg.sender];
        require(permitted >= amount && balanceOf[from] >= amount, "transferFrom invalid");
        allowance[from][msg.sender] = permitted - amount;
        balanceOf[from] -= amount;
        balanceOf[to] += amount;
        emit Transfer(from, to, amount);
        return true;
    }
}

contract FeeOnTransferToken is MockToken {
    uint256 private constant FEE = 1;

    function transfer(address to, uint256 amount) public override returns (bool) {
        require(to != address(0) && balanceOf[msg.sender] >= amount, "transfer invalid");
        balanceOf[msg.sender] -= amount;
        balanceOf[to] += amount - FEE;
        totalSupply -= FEE;
        emit Transfer(msg.sender, to, amount - FEE);
        emit Transfer(msg.sender, address(0), FEE);
        return true;
    }
}

contract ReentrantToken is MockToken {
    AegisVault private _vault;

    function setVault(AegisVault vault) external {
        _vault = vault;
    }

    function transfer(address to, uint256 amount) public override returns (bool) {
        (bool success, bytes memory result) = address(_vault).call(
            abi.encodeCall(
                AegisVault.transferToken,
                (address(this), to, 1, bytes32("nested"))
            )
        );
        if (!success) {
            assembly {
                revert(add(result, 32), mload(result))
            }
        }
        return super.transfer(to, amount);
    }
}

contract AegisVaultTest is Test {
    address internal constant AGENT = address(0xA11CE);
    address internal constant RECIPIENT = address(0xB0B);
    address internal constant UNLISTED = address(0xCAFE);
    bytes32 internal constant REF = keccak256("proposal-reference");

    AegisVault internal vault;
    MockToken internal token;

    function setUp() public {
        vault = new AegisVault(address(this));
        token = new MockToken();
        vault.setAgent(AGENT);
        vault.setTokenPolicy(address(token), true, 100, 200, 0);
        vault.setRecipient(RECIPIENT, true);
        token.mint(address(vault), 10_000);
    }

    function _transfer(address to, uint256 amount, bytes32 ref) internal {
        vm.prank(AGENT);
        vault.transferToken(address(token), to, amount, ref);
    }

    function _approve(address to, uint256 amount, bytes32 ref, uint256 expiry) internal {
        vault.approveTransfer(address(token), to, amount, ref, expiry);
    }

    function testOnlyAgentCanTransferAndRevokedAgentCannotTransfer() public {
        vm.expectRevert(AegisVault.UnauthorizedAgent.selector);
        vault.transferToken(address(token), RECIPIENT, 1, REF);

        vault.revokeAgent();
        vm.expectRevert(AegisVault.UnauthorizedAgent.selector);
        vm.prank(AGENT);
        vault.transferToken(address(token), RECIPIENT, 1, REF);
    }

    function testPauseBlocksAgentAndEmergencyWithdrawWorksWhilePaused() public {
        vault.pause();
        vm.expectRevert(bytes4(keccak256("EnforcedPause()")));
        vm.prank(AGENT);
        vault.transferToken(address(token), RECIPIENT, 1, REF);

        vault.emergencyWithdraw(address(token), RECIPIENT, 7);
        assertEq(token.balanceOf(RECIPIENT), 7);
    }

    function testEmergencyWithdrawIsOwnerOnly() public {
        vm.expectRevert(abi.encodeWithSignature("OwnableUnauthorizedAccount(address)", AGENT));
        vm.prank(AGENT);
        vault.emergencyWithdraw(address(token), RECIPIENT, 1);
    }

    function testOwnerControlsAndTwoStepOwnership() public {
        vm.expectRevert(abi.encodeWithSignature("OwnableUnauthorizedAccount(address)", AGENT));
        vm.prank(AGENT);
        vault.pause();

        address nextOwner = address(0xD00D);
        vault.transferOwnership(nextOwner);
        assertEq(vault.owner(), address(this));
        assertEq(vault.pendingOwner(), nextOwner);

        vm.prank(nextOwner);
        vault.acceptOwnership();
        assertEq(vault.owner(), nextOwner);

        vm.expectRevert(
            abi.encodeWithSignature("OwnableUnauthorizedAccount(address)", address(this))
        );
        vault.setTokenPolicy(address(token), false, 0, 0, 0);
    }

    function testOwnerAndAgentMustRemainDistinct() public {
        vm.expectRevert(AegisVault.AgentCannotBeOwner.selector);
        vault.setAgent(address(this));

        vault.transferOwnership(AGENT);
        vm.expectRevert(AegisVault.AgentCannotBeOwner.selector);
        vm.prank(AGENT);
        vault.acceptOwnership();
        assertEq(vault.owner(), address(this));
    }

    function testZeroAmountReverts() public {
        vm.expectRevert(AegisVault.ZeroAmount.selector);
        vm.prank(AGENT);
        vault.transferToken(address(token), RECIPIENT, 0, REF);
    }

    function testPerTransactionCapAndExactBoundary() public {
        _transfer(RECIPIENT, 100, REF);
        assertEq(vault.todaySpend(address(token)), 100);

        vm.expectRevert(
            abi.encodeWithSelector(AegisVault.PerTransactionCapExceeded.selector, 101, 100)
        );
        vm.prank(AGENT);
        vault.transferToken(address(token), RECIPIENT, 101, REF);
    }

    function testDailyCapBoundaryAndExceededAmount() public {
        _transfer(RECIPIENT, 100, REF);
        _transfer(RECIPIENT, 100, bytes32("second"));
        assertEq(vault.todaySpend(address(token)), 200);

        vm.expectRevert(
            abi.encodeWithSelector(AegisVault.DailyCapExceeded.selector, 200, 1, 200)
        );
        vm.prank(AGENT);
        vault.transferToken(address(token), RECIPIENT, 1, bytes32("third"));
    }

    function testDailySpendResetsAtUtcDayBucketBoundary() public {
        _transfer(RECIPIENT, 100, REF);
        assertEq(vault.todaySpend(address(token)), 100);
        vm.warp((block.timestamp / 1 days + 1) * 1 days);
        assertEq(vault.todaySpend(address(token)), 0);
        _transfer(RECIPIENT, 100, bytes32("next-day"));
        assertEq(vault.todaySpend(address(token)), 100);
    }

    function testDisallowedTokenReverts() public {
        MockToken other = new MockToken();
        vm.expectRevert(abi.encodeWithSelector(AegisVault.TokenNotAllowed.selector, address(other)));
        vm.prank(AGENT);
        vault.transferToken(address(other), RECIPIENT, 1, REF);
    }

    function testUnlistedRecipientRequiresApproval() public {
        vm.expectRevert(
            abi.encodeWithSelector(
                AegisVault.TransferApprovalRequired.selector,
                address(token),
                UNLISTED,
                10,
                REF
            )
        );
        vm.prank(AGENT);
        vault.transferToken(address(token), UNLISTED, 10, REF);
    }

    function testThresholdApprovalIsConsumedExactlyOnce() public {
        vault.setTokenPolicy(address(token), true, 100, 200, 80);
        _approve(RECIPIENT, 80, REF, block.timestamp + 1 days);
        _transfer(RECIPIENT, 80, REF);
        assertEq(vault.approvalExpiry(address(token), RECIPIENT, 80, REF), 0);

        vm.expectRevert(
            abi.encodeWithSelector(
                AegisVault.TransferApprovalRequired.selector,
                address(token),
                RECIPIENT,
                80,
                REF
            )
        );
        vm.prank(AGENT);
        vault.transferToken(address(token), RECIPIENT, 80, REF);
    }

    function testExpiredApprovalReverts() public {
        uint256 expiry = block.timestamp + 1;
        _approve(UNLISTED, 10, REF, expiry);
        vm.warp(expiry + 1);

        vm.expectRevert(
            abi.encodeWithSelector(AegisVault.TransferApprovalExpired.selector, expiry)
        );
        vm.prank(AGENT);
        vault.transferToken(address(token), UNLISTED, 10, REF);
    }

    function testApprovalBindsReferenceAmountAndRecipient() public {
        uint256 expiry = block.timestamp + 1 days;
        _approve(UNLISTED, 10, REF, expiry);

        vm.expectPartialRevert(AegisVault.TransferApprovalRequired.selector);
        vm.prank(AGENT);
        vault.transferToken(address(token), UNLISTED, 10, bytes32("wrong-ref"));

        vm.expectPartialRevert(AegisVault.TransferApprovalRequired.selector);
        vm.prank(AGENT);
        vault.transferToken(address(token), UNLISTED, 11, REF);

        vm.expectPartialRevert(AegisVault.TransferApprovalRequired.selector);
        vm.prank(AGENT);
        vault.transferToken(address(token), address(0xD00D), 10, REF);

        MockToken other = new MockToken();
        vault.setTokenPolicy(address(other), true, 100, 200, 0);
        other.mint(address(vault), 100);
        vm.expectPartialRevert(AegisVault.TransferApprovalRequired.selector);
        vm.prank(AGENT);
        vault.transferToken(address(other), UNLISTED, 10, REF);
    }

    function testRevokedApprovalCannotBeUsed() public {
        _approve(UNLISTED, 10, REF, block.timestamp + 1 days);
        vault.revokeApproval(address(token), UNLISTED, 10, REF);

        vm.expectPartialRevert(AegisVault.TransferApprovalRequired.selector);
        vm.prank(AGENT);
        vault.transferToken(address(token), UNLISTED, 10, REF);
    }

    function testApprovalDoesNotBypassPerTransactionOrDailyCap() public {
        _approve(RECIPIENT, 101, REF, block.timestamp + 1 days);
        vm.expectRevert(
            abi.encodeWithSelector(AegisVault.PerTransactionCapExceeded.selector, 101, 100)
        );
        vm.prank(AGENT);
        vault.transferToken(address(token), RECIPIENT, 101, REF);

        vault.setTokenPolicy(address(token), true, 100, 100, 0);
        _transfer(RECIPIENT, 100, bytes32("daily-cap-full"));
        _approve(UNLISTED, 1, bytes32("approved-over-cap"), block.timestamp + 1 days);
        vm.expectRevert(
            abi.encodeWithSelector(AegisVault.DailyCapExceeded.selector, 100, 1, 100)
        );
        vm.prank(AGENT);
        vault.transferToken(address(token), UNLISTED, 1, bytes32("approved-over-cap"));
    }

    function testApprovalExpiryMustBeFutureAndAtMostSevenDaysAway() public {
        vm.expectRevert(AegisVault.InvalidApprovalExpiry.selector);
        _approve(UNLISTED, 1, REF, block.timestamp);

        vm.expectRevert(AegisVault.InvalidApprovalExpiry.selector);
        _approve(UNLISTED, 1, REF, block.timestamp + 7 days + 1);
    }

    function testFeeOnTransferTokenReverts() public {
        FeeOnTransferToken feeToken = new FeeOnTransferToken();
        vault.setTokenPolicy(address(feeToken), true, 100, 200, 0);
        feeToken.mint(address(vault), 100);

        vm.expectRevert(AegisVault.UnexpectedTokenBalanceChange.selector);
        vm.prank(AGENT);
        vault.transferToken(address(feeToken), RECIPIENT, 10, REF);
        assertEq(vault.todaySpend(address(feeToken)), 0);
    }

    function testReentrantTokenCallReverts() public {
        ReentrantToken malicious = new ReentrantToken();
        AegisVault secondVault = new AegisVault(address(this));
        secondVault.setAgent(AGENT);
        secondVault.setTokenPolicy(address(malicious), true, 100, 200, 0);
        secondVault.setRecipient(RECIPIENT, true);
        malicious.mint(address(secondVault), 100);
        malicious.setVault(secondVault);

        vm.expectRevert(bytes4(keccak256("ReentrancyGuardReentrantCall()")));
        vm.prank(AGENT);
        secondVault.transferToken(address(malicious), RECIPIENT, 10, REF);
    }

    function testFuzz_TransfersCannotExceedConfiguredCaps(
        uint96 rawAmount,
        uint96 rawPriorSpend
    ) public {
        uint256 amount = uint256(rawAmount) % 101 + 1;
        uint256 priorSpend = uint256(rawPriorSpend) % 201;
        if (priorSpend >= 100) {
            _transfer(RECIPIENT, 100, bytes32("prior-first"));
            priorSpend -= 100;
        }
        if (priorSpend > 0) _transfer(RECIPIENT, priorSpend, bytes32("prior-second"));
        uint256 actualPriorSpend = vault.todaySpend(address(token));

        if (amount > 100) {
            vm.expectPartialRevert(AegisVault.PerTransactionCapExceeded.selector);
            vm.prank(AGENT);
            vault.transferToken(address(token), RECIPIENT, amount, REF);
        } else if (actualPriorSpend + amount > 200) {
            vm.expectPartialRevert(AegisVault.DailyCapExceeded.selector);
            vm.prank(AGENT);
            vault.transferToken(address(token), RECIPIENT, amount, REF);
        } else {
            _transfer(RECIPIENT, amount, REF);
            assertEq(vault.todaySpend(address(token)), actualPriorSpend + amount);
        }
    }
}

contract AgentActor {
    function callTarget(address target, bytes calldata data) external returns (bool success) {
        (success,) = target.call(data);
    }
}

contract VaultHandler {
    AegisVault public immutable vault;
    MockToken public immutable token;
    AgentActor public immutable actor;
    address public constant ALLOWED_RECIPIENT = address(0xB0B);
    uint256 public unauthorizedSuccessfulTransfers;
    uint256 private _sequence;

    constructor() {
        vault = new AegisVault(address(this));
        token = new MockToken();
        actor = new AgentActor();
        vault.setAgent(address(actor));
        vault.setTokenPolicy(address(token), true, 100, 200, 80);
        vault.setRecipient(ALLOWED_RECIPIENT, true);
        token.mint(address(vault), 1_000_000);
    }

    function exercise(uint96 rawAmount, address proposedRecipient, bool grantApproval) external {
        uint256 amount = uint256(rawAmount) % 250 + 1;
        address to = proposedRecipient;
        if (to == address(0) || to == address(vault)) to = address(0xCAFE);
        bytes32 ref = bytes32(++_sequence);
        AegisVault.TokenPolicy memory policy = vault.tokenPolicy(address(token));
        bool needsApproval = !vault.recipients(to)
            || (policy.approvalThreshold != 0 && amount >= policy.approvalThreshold);
        if (grantApproval && needsApproval) {
            vault.approveTransfer(address(token), to, amount, ref, block.timestamp + 1 days);
        }

        bool success = actor.callTarget(
            address(vault),
            abi.encodeCall(AegisVault.transferToken, (address(token), to, amount, ref))
        );
        if (success && needsApproval && !grantApproval) {
            unauthorizedSuccessfulTransfers += 1;
        }
    }
}

contract AegisVaultInvariantTest is StdInvariant, Test {
    VaultHandler internal handler;

    function setUp() public {
        handler = new VaultHandler();
        targetContract(address(handler));
    }

    function invariant_dailySpendNeverExceedsDailyCap() public view {
        AegisVault vault = handler.vault();
        assertLe(vault.todaySpend(address(handler.token())), 200);
    }

    function invariant_successfulTransfersRequireAllowlistOrApproval() public view {
        assertEq(handler.unauthorizedSuccessfulTransfers(), 0);
    }
}
