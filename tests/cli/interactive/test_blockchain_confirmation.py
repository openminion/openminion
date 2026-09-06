from web3 import Web3

from openminion.modules.brain.loop.tools.confirmation import (
    confirmation_required_user_message,
)
from openminion.modules.brain.schemas import ToolCommand
from openminion.tools.blockchain.confirmation import (
    build_blockchain_send_confirmation_preview,
)
from openminion.tools.blockchain.abi import abi_signature, encode_function_call
from openminion.tools.blockchain.schema_types import FunctionAbi
from openminion.tools.blockchain.runtime import preparation_digest


def test_focus_and_terminal_shared_renderer_has_no_blockchain_session_choice() -> None:
    transaction = {
        "schema_version": "evm-transaction-v1",
        "transaction_type": "eip1559",
        "chain_id": 31337,
        "from_address": "0x" + "11" * 20,
        "to_address": "0x" + "22" * 20,
        "value_wei": "1",
        "nonce": "0",
        "gas_limit": "21000",
        "data": "0x",
        "max_fee_per_gas_wei": "2",
        "max_priority_fee_per_gas_wei": "1",
        "max_total_fee_wei": "42000",
    }
    command = ToolCommand(
        kind="tool",
        title="Send transaction",
        tool_name="blockchain.send_transaction",
        args={
            "transaction": transaction,
            "call_context": None,
            "preparation_digest": preparation_digest(transaction, None),
        },
        inputs={},
    )

    preview = build_blockchain_send_confirmation_preview(command.args)
    rendered = confirmation_required_user_message(command, preview)

    assert f"To: {preview.to_address}" in rendered
    assert f"Preparation digest: {preview.preparation_digest}" in rendered
    assert rendered.splitlines()[-1] == (
        "Reply exactly yes to allow once, or no to cancel."
    )


def test_shared_renderer_shows_ordered_tuple_call_facts() -> None:
    function = FunctionAbi.model_validate(
        {
            "type": "function",
            "name": "swap",
            "inputs": [
                {
                    "name": "request",
                    "type": "tuple",
                    "components": [
                        {"name": "recipient", "type": "address"},
                        {"name": "amountIn", "type": "uint256"},
                    ],
                }
            ],
            "outputs": [],
            "stateMutability": "nonpayable",
        }
    )
    function_args = [["0x" + "33" * 20, 7]]
    call_context = {
        "function_abi": function.model_dump(mode="json"),
        "function_args": function_args,
        "function_signature": abi_signature(function),
    }
    transaction = {
        "schema_version": "evm-transaction-v1",
        "transaction_type": "eip1559",
        "chain_id": 31337,
        "from_address": "0x" + "11" * 20,
        "to_address": "0x" + "22" * 20,
        "value_wei": "0",
        "nonce": "0",
        "gas_limit": "50000",
        "data": encode_function_call(Web3(), function, function_args),
        "max_fee_per_gas_wei": "2",
        "max_priority_fee_per_gas_wei": "1",
        "max_total_fee_wei": "100000",
    }
    command = ToolCommand(
        kind="tool",
        title="Send tuple transaction",
        tool_name="blockchain.send_transaction",
        args={
            "transaction": transaction,
            "call_context": call_context,
            "preparation_digest": preparation_digest(transaction, call_context),
        },
        inputs={},
    )

    preview = build_blockchain_send_confirmation_preview(command.args)
    rendered = confirmation_required_user_message(command, preview)

    assert preview.call is not None
    assert preview.call.function_signature == "swap((address,uint256))"
    assert preview.call.function_args == [["0x" + "33" * 20, "7"]]
    assert "Function: swap((address,uint256))" in rendered
    assert 'Arguments: [["0x3333333333333333333333333333333333333333","7"]]' in rendered
