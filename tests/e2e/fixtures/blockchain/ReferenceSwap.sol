// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

contract ReferenceSwap {
    struct SwapRequest {
        address recipient;
        uint256 amountIn;
        uint256 minAmountOut;
    }

    error MinimumOutput(uint256 quoted, uint256 minimum);

    event Swap(
        address indexed sender,
        address indexed recipient,
        uint256 amountIn,
        uint256 amountOut
    );

    mapping(address => uint256) public outputOf;

    function quote(uint256 amountIn) external pure returns (uint256 amountOut) {
        return amountIn * 2;
    }

    function swap(SwapRequest calldata request) external returns (uint256 amountOut) {
        amountOut = request.amountIn * 2;
        if (amountOut < request.minAmountOut) {
            revert MinimumOutput(amountOut, request.minAmountOut);
        }
        outputOf[request.recipient] += amountOut;
        emit Swap(msg.sender, request.recipient, request.amountIn, amountOut);
    }
}
