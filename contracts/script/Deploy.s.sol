// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {Script, console2} from "forge-std/Script.sol";
import {AegisVault} from "../src/AegisVault.sol";

contract Deploy is Script {
    uint256 private constant BASE_SEPOLIA_CHAIN_ID = 84532;

    error UnsupportedChainId(uint256 actualChainId);
    error InvalidOwnerAddress();

    function run() external returns (AegisVault vault) {
        if (block.chainid != BASE_SEPOLIA_CHAIN_ID) {
            revert UnsupportedChainId(block.chainid);
        }

        address ownerAddress = vm.envAddress("AEGIS_VAULT_OWNER_ADDRESS");
        if (ownerAddress == address(0)) revert InvalidOwnerAddress();

        vm.startBroadcast(ownerAddress);
        vault = new AegisVault(ownerAddress);
        vm.stopBroadcast();

        console2.log("AegisVault deployed at", address(vault));
        console2.log("Owner", ownerAddress);
        console2.log("Chain ID", block.chainid);
    }
}
