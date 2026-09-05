from __future__ import annotations

from importlib import metadata
import json

import pytest
from pydantic import ValidationError
from web3 import Web3

import openminion.tools.blockchain as blockchain_package
from openminion.modules.tool.contracts.model_ids import (
    ALL_MODEL_TOOL_IDS_SET,
    DEFAULT_VISIBLE_MODEL_TOOL_IDS_SET,
    MODEL_BLOCKCHAIN_DEBUG,
    MODEL_BLOCKCHAIN_INSPECT,
    MODEL_BLOCKCHAIN_PREPARE_TRANSACTION,
    MODEL_BLOCKCHAIN_SEND_TRANSACTION,
)
from openminion.modules.tool.contracts.runtime_ids import (
    ALL_RUNTIME_BINDING_IDS_SET,
    RUNTIME_BLOCKCHAIN_DEBUG,
    RUNTIME_BLOCKCHAIN_INSPECT,
    RUNTIME_BLOCKCHAIN_PREPARE_TRANSACTION,
    RUNTIME_BLOCKCHAIN_SEND_TRANSACTION,
)
from openminion.modules.tool.runtime.policy_defaults import DEFAULT_POLICY
from openminion.modules.tool.contracts.dependencies import ToolDependencyProbeContext
from openminion.base.config.env import EnvironmentConfig
from openminion.tools.blockchain.plugin import (
    BLOCKCHAIN_DEBUG_DESCRIPTION,
    BLOCKCHAIN_INSPECT_DESCRIPTION,
    WEB3_DEPENDENCY,
)
from openminion.tools.blockchain.schemas import (
    DEBUG_REQUEST_ADAPTER,
    DebugArgs,
    INSPECT_REQUEST_ADAPTER,
    InspectArgs,
    PREPARE_REQUEST_ADAPTER,
    PrepareArgs,
    AbiParameter,
    ErrorAbi,
    EventAbi,
    FunctionAbi,
    SendTransactionArgs,
)
from openminion.tools.blockchain.abi import (
    abi_selector,
    abi_signature,
    canonical_abi_type,
    decode_abi_values,
    encode_function_call,
    event_topic,
)

MODEL_IDS = {
    MODEL_BLOCKCHAIN_DEBUG,
    MODEL_BLOCKCHAIN_INSPECT,
    MODEL_BLOCKCHAIN_PREPARE_TRANSACTION,
    MODEL_BLOCKCHAIN_SEND_TRANSACTION,
}
RUNTIME_IDS = {
    RUNTIME_BLOCKCHAIN_DEBUG,
    RUNTIME_BLOCKCHAIN_INSPECT,
    RUNTIME_BLOCKCHAIN_PREPARE_TRANSACTION,
    RUNTIME_BLOCKCHAIN_SEND_TRANSACTION,
}


def test_blockchain_package_exports_final_registrar() -> None:
    manifest = blockchain_package.REGISTRAR.get_manifest(None)

    assert manifest.module_id == "blockchain"
    assert {item.model_tool_id for item in manifest.model_tools} == MODEL_IDS
    assert {
        item.runtime_binding_id for item in manifest.runtime_bindings
    } == RUNTIME_IDS
    assert all(not item.aliases for item in manifest.model_tools)


def test_manifest_distinguishes_inspect_from_debug() -> None:
    manifest = blockchain_package.REGISTRAR.get_manifest(None)
    inspect = next(
        item
        for item in manifest.model_tools
        if item.model_tool_id == MODEL_BLOCKCHAIN_INSPECT
    )
    debug = next(
        item
        for item in manifest.model_tools
        if item.model_tool_id == MODEL_BLOCKCHAIN_DEBUG
    )

    assert inspect.description == BLOCKCHAIN_INSPECT_DESCRIPTION
    assert inspect.description == (
        "Use for read-only contract functions supplied as a function ABI and "
        "arguments, including quotes and state. Also reads balance, bytecode, "
        "transaction, or receipt, one fact per call. When asked to verify a receipt, "
        "use action receipt even if a prior send returned receipt status. Use "
        "blockchain.debug only for raw calldata, reverts, or event decoding. Never "
        "signs or sends."
    )
    assert debug.description == BLOCKCHAIN_DEBUG_DESCRIPTION
    assert debug.description == (
        "Simulate EVM calls and decode calldata, revert data, or events from one "
        "transaction receipt on the configured blockchain. Read-only; never signs "
        "or sends. Not for receipt status; use blockchain.inspect with action receipt."
    )


def test_complex_blockchain_schema_examples_are_valid() -> None:
    for model, adapter in (
        (InspectArgs, INSPECT_REQUEST_ADAPTER),
        (PrepareArgs, PREPARE_REQUEST_ADAPTER),
        (DebugArgs, DEBUG_REQUEST_ADAPTER),
    ):
        examples = model.model_json_schema()["examples"]
        assert examples
        for example in examples:
            adapter.validate_python(example)


def test_discriminated_tool_schemas_inline_action_branches() -> None:
    for model in (InspectArgs, PrepareArgs, DebugArgs):
        schema = model.model_json_schema()
        assert schema["discriminator"] == {
            "propertyName": "kind" if model is PrepareArgs else "action"
        }
        assert all("$ref" not in branch for branch in schema["oneOf"])
        assert all("title" not in branch for branch in schema["oneOf"])
        assert not any(name.endswith("Args") for name in schema["$defs"])


def test_blockchain_manifest_maps_each_model_id_to_its_runtime_candidate() -> None:
    manifest = blockchain_package.REGISTRAR.get_manifest(None)

    assert {
        (
            model.model_tool_id,
            binding.runtime_binding_id,
            binding.runtime_candidates,
        )
        for model, binding in zip(
            manifest.model_tools, manifest.runtime_bindings, strict=True
        )
    } == {
        (
            MODEL_BLOCKCHAIN_DEBUG,
            RUNTIME_BLOCKCHAIN_DEBUG,
            (MODEL_BLOCKCHAIN_DEBUG,),
        ),
        (
            MODEL_BLOCKCHAIN_INSPECT,
            RUNTIME_BLOCKCHAIN_INSPECT,
            (MODEL_BLOCKCHAIN_INSPECT,),
        ),
        (
            MODEL_BLOCKCHAIN_PREPARE_TRANSACTION,
            RUNTIME_BLOCKCHAIN_PREPARE_TRANSACTION,
            (MODEL_BLOCKCHAIN_PREPARE_TRANSACTION,),
        ),
        (
            MODEL_BLOCKCHAIN_SEND_TRANSACTION,
            RUNTIME_BLOCKCHAIN_SEND_TRANSACTION,
            (MODEL_BLOCKCHAIN_SEND_TRANSACTION,),
        ),
    }


def test_blockchain_ids_are_canonically_admitted() -> None:
    assert MODEL_IDS <= ALL_MODEL_TOOL_IDS_SET
    assert MODEL_IDS <= DEFAULT_VISIBLE_MODEL_TOOL_IDS_SET
    assert RUNTIME_IDS <= ALL_RUNTIME_BINDING_IDS_SET


def test_default_policy_allows_blockchain_family() -> None:
    assert "blockchain." in DEFAULT_POLICY["tools"]["allow_prefix"]


def test_inspect_schema_is_closed_and_discriminated() -> None:
    parsed = INSPECT_REQUEST_ADAPTER.validate_python(
        {"action": "native_balance", "address": "0x" + "11" * 20}
    )
    assert parsed.action == "native_balance"

    with pytest.raises(ValidationError):
        INSPECT_REQUEST_ADAPTER.validate_python(
            {"action": "native_balance", "address": "0x" + "11" * 20, "chain": 1}
        )
    with pytest.raises(ValidationError):
        INSPECT_REQUEST_ADAPTER.validate_python({"action": "arbitrary_rpc"})


@pytest.mark.parametrize(
    "payload",
    [
        {
            "action": "simulate_call",
            "from_address": "0x" + "11" * 20,
            "to_address": "0x" + "22" * 20,
            "data": "0x",
        },
        {
            "action": "decode_calldata",
            "function_abi": {
                "type": "function",
                "name": "balanceOf",
                "inputs": [{"name": "owner", "type": "address"}],
                "outputs": [{"name": "", "type": "uint256"}],
                "stateMutability": "view",
            },
            "data": ("0x70a08231000000000000000000000000" + "11" * 20),
        },
        {"action": "decode_revert", "data": "0x08c379a0"},
        {
            "action": "transaction_events",
            "transaction_hash": "0x" + "aa" * 32,
            "event_abi": {
                "type": "event",
                "name": "Swap",
                "inputs": [{"name": "sender", "type": "address", "indexed": True}],
                "anonymous": False,
            },
        },
    ],
)
def test_debug_schema_accepts_exact_four_actions(payload: dict) -> None:
    assert DEBUG_REQUEST_ADAPTER.validate_python(payload).action == payload["action"]


def test_debug_schema_rejects_unknown_action_and_fields() -> None:
    with pytest.raises(ValidationError):
        DEBUG_REQUEST_ADAPTER.validate_python({"action": "arbitrary_rpc"})
    with pytest.raises(ValidationError):
        DEBUG_REQUEST_ADAPTER.validate_python(
            {
                "action": "decode_revert",
                "data": "0x",
                "rpc_url": "http://example.invalid",
            }
        )


@pytest.mark.parametrize(
    "abi_type",
    [
        "address",
        "bool",
        "string",
        "bytes",
        "bytes32",
        "uint",
        "int256",
        "address[]",
        "uint8[2][]",
    ],
)
def test_non_tuple_abi_parameter_accepts_supported_types(abi_type: str) -> None:
    assert AbiParameter(name="value", type=abi_type).type == abi_type


@pytest.mark.parametrize(
    "abi_type",
    ["tuple", "fixed128x18", "function", "bytes33", "uint7", "address[01]"],
)
def test_non_tuple_abi_parameter_rejects_unsupported_types(abi_type: str) -> None:
    with pytest.raises(ValidationError):
        AbiParameter(name="value", type=abi_type)


def test_recursive_tuple_signatures_and_values_are_ordered() -> None:
    function = FunctionAbi.model_validate(
        {
            "type": "function",
            "name": "swap",
            "inputs": [
                {
                    "name": "requests",
                    "type": "tuple[]",
                    "components": [
                        {"name": "recipient", "type": "address"},
                        {"name": "amountIn", "type": "uint256"},
                        {
                            "name": "minimum",
                            "type": "tuple",
                            "components": [{"name": "minAmountOut", "type": "uint256"}],
                        },
                    ],
                }
            ],
            "outputs": [],
            "stateMutability": "nonpayable",
        }
    )
    web3 = Web3()
    values = [
        [
            [
                "0x" + "11" * 20,
                7,
                [9],
            ]
        ]
    ]
    encoded = encode_function_call(web3, function, values)

    assert canonical_abi_type(function.inputs[0]) == ("(address,uint256,(uint256))[]")
    assert abi_signature(function) == "swap((address,uint256,(uint256))[])"
    assert decode_abi_values(web3, function.inputs, bytes.fromhex(encoded[10:])) == [
        [["0x" + "11" * 20, "7", ["9"]]]
    ]
    with pytest.raises(ValueError):
        encode_function_call(
            web3,
            function,
            [{"recipient": "0x" + "11" * 20, "amountIn": 7}],
        )


def test_function_error_and_event_use_one_canonical_signature_owner() -> None:
    parameter = {
        "name": "request",
        "type": "tuple",
        "components": [
            {"name": "recipient", "type": "address"},
            {"name": "amountIn", "type": "uint256"},
        ],
    }
    function = FunctionAbi.model_validate(
        {
            "type": "function",
            "name": "swap",
            "inputs": [parameter],
            "outputs": [],
            "stateMutability": "nonpayable",
        }
    )
    error = ErrorAbi.model_validate(
        {"type": "error", "name": "SwapFailed", "inputs": [parameter]}
    )
    event = EventAbi.model_validate(
        {
            "type": "event",
            "name": "Swap",
            "inputs": [{**parameter, "indexed": False}],
            "anonymous": False,
        }
    )
    web3 = Web3()

    assert abi_signature(function) == "swap((address,uint256))"
    assert abi_signature(error) == "SwapFailed((address,uint256))"
    assert abi_signature(event) == "Swap((address,uint256))"
    assert (
        abi_selector(function, web3).removeprefix("0x")
        == web3.keccak(text="swap((address,uint256))")[:4].hex()
    )
    assert event_topic(event, web3).removeprefix("0x") == web3.keccak(
        text="Swap((address,uint256))"
    ).hex().removeprefix("0x")


def test_tuple_components_are_required_only_for_tuple_types() -> None:
    with pytest.raises(ValidationError):
        AbiParameter(name="request", type="tuple[]")
    with pytest.raises(ValidationError):
        AbiParameter(
            name="owner",
            type="address",
            components=[{"name": "value", "type": "uint256"}],
        )


def test_prepare_and_send_schemas_reject_noncanonical_fields() -> None:
    prepared = PREPARE_REQUEST_ADAPTER.validate_python(
        {
            "kind": "native_transfer",
            "to_address": "0x" + "22" * 20,
            "value_wei": "1",
        }
    )
    assert prepared.kind == "native_transfer"

    with pytest.raises(ValidationError):
        PREPARE_REQUEST_ADAPTER.validate_python(
            {"kind": "native_transfer", "to": "0x" + "22" * 20, "value_wei": "1"}
        )


def test_nested_abi_containers_accept_strict_json_strings() -> None:
    function_abi = {
        "type": "function",
        "name": "quote",
        "inputs": [{"name": "amountIn", "type": "uint256"}],
        "outputs": [{"name": "amountOut", "type": "uint256"}],
        "stateMutability": "pure",
    }
    prepared = PREPARE_REQUEST_ADAPTER.validate_python(
        {
            "kind": "contract_call",
            "contract_address": "0x" + "22" * 20,
            "function_abi": json.dumps(function_abi),
            "function_args": "[7]",
        }
    )
    inspected = INSPECT_REQUEST_ADAPTER.validate_python(
        {
            "action": "contract_read",
            "contract_address": "0x" + "22" * 20,
            "function_abi": json.dumps(function_abi),
            "function_args": "[7]",
        }
    )
    debugged = DEBUG_REQUEST_ADAPTER.validate_python(
        {"action": "decode_revert", "data": "0x", "error_abis": "[]"}
    )

    assert prepared.function_args == [7]
    assert inspected.function_args == [7]
    assert debugged.error_abis == []


def test_nested_abi_container_decoding_does_not_repair_wrong_shapes() -> None:
    with pytest.raises(ValidationError):
        PREPARE_REQUEST_ADAPTER.validate_python(
            {
                "kind": "contract_call",
                "contract_address": "0x" + "22" * 20,
                "function_abi": "[]",
                "function_args": '{"amountIn":7}',
            }
        )


def test_nested_abi_container_decoding_rejects_non_json_native_sequences() -> None:
    function_abi = {
        "type": "function",
        "name": "quote",
        "inputs": [{"name": "amountIn", "type": "uint256"}],
        "outputs": [{"name": "amountOut", "type": "uint256"}],
        "stateMutability": "pure",
    }
    with pytest.raises(ValidationError):
        PREPARE_REQUEST_ADAPTER.validate_python(
            {
                "kind": "contract_call",
                "contract_address": "0x" + "22" * 20,
                "function_abi": function_abi,
                "function_args": (7,),
            }
        )
    with pytest.raises(ValidationError):
        INSPECT_REQUEST_ADAPTER.validate_python(
            {
                "action": "contract_read",
                "contract_address": "0x" + "22" * 20,
                "function_abi": function_abi,
                "function_args": (7,),
            }
        )
    with pytest.raises(ValidationError):
        DEBUG_REQUEST_ADAPTER.validate_python(
            {"action": "decode_revert", "data": "0x", "error_abis": ()}
        )


@pytest.mark.parametrize(
    "value",
    [(7,), {1, 2}, {1: "value"}, float("nan"), float("inf")],
    ids=["tuple", "set", "non-string-key", "nan", "infinity"],
)
def test_nested_abi_container_decoding_rejects_non_json_nested_values(
    value: object,
) -> None:
    with pytest.raises(ValidationError):
        PREPARE_REQUEST_ADAPTER.validate_python(
            {
                "kind": "contract_call",
                "contract_address": "0x" + "22" * 20,
                "function_abi": {
                    "type": "function",
                    "name": "quote",
                    "inputs": [{"name": "amountIn", "type": "uint256"}],
                    "outputs": [{"name": "amountOut", "type": "uint256"}],
                    "stateMutability": "pure",
                },
                "function_args": [value],
            }
        )


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_nested_abi_container_decoding_rejects_nonstandard_json_constants(
    constant: str,
) -> None:
    with pytest.raises(ValidationError):
        PREPARE_REQUEST_ADAPTER.validate_python(
            {
                "kind": "contract_call",
                "contract_address": "0x" + "22" * 20,
                "function_abi": {
                    "type": "function",
                    "name": "quote",
                    "inputs": [{"name": "amountIn", "type": "uint256"}],
                    "outputs": [{"name": "amountOut", "type": "uint256"}],
                    "stateMutability": "pure",
                },
                "function_args": f"[{constant}]",
            }
        )


def test_send_schema_accepts_structured_and_strict_json_objects() -> None:
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

    encoded = SendTransactionArgs.model_validate(
        {
            "transaction": json.dumps(transaction),
            "call_context": None,
            "preparation_digest": "sha256:" + "0" * 64,
        }
    )
    parsed = SendTransactionArgs.model_validate(
        {
            "transaction": transaction,
            "call_context": None,
            "preparation_digest": "sha256:" + "0" * 64,
        }
    )

    assert parsed.transaction.model_dump(mode="json") == transaction
    assert encoded.transaction.model_dump(mode="json") == transaction


def test_send_schema_rejects_non_object_json_nested_value() -> None:
    with pytest.raises(ValidationError):
        SendTransactionArgs.model_validate(
            {
                "transaction": "[]",
                "call_context": None,
                "preparation_digest": "sha256:" + "0" * 64,
            }
        )
    with pytest.raises(ValidationError):
        SendTransactionArgs.model_validate(
            {
                "transaction": {},
                "call_context": None,
                "preparation_digest": "sha256:" + "0" * 64,
                "private_key": "secret",
            }
        )


def _probe_context(tmp_path) -> ToolDependencyProbeContext:
    return ToolDependencyProbeContext(
        workspace=tmp_path,
        env=EnvironmentConfig(values={}),
        policy={},
    )


def test_web3_dependency_ready_payload(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(metadata, "version", lambda _name: "7.12.0")

    status = WEB3_DEPENDENCY.probe(_probe_context(tmp_path))

    assert status.as_dict() == {
        "dependency_id": "python:web3",
        "state": "ready",
        "resolved_path": "",
        "version": "7.12.0",
        "reason_code": "",
        "message": "python:web3 is ready",
        "setup_hints": [],
    }


def test_web3_dependency_missing_payload(monkeypatch, tmp_path) -> None:
    def _missing(_name: str) -> str:
        raise metadata.PackageNotFoundError

    monkeypatch.setattr(metadata, "version", _missing)

    status = WEB3_DEPENDENCY.probe(_probe_context(tmp_path))

    assert status.as_dict() == {
        "dependency_id": "python:web3",
        "state": "missing",
        "resolved_path": "",
        "version": "",
        "reason_code": "python_package_not_found",
        "message": "python:web3 is not available",
        "setup_hints": [
            {
                "platform": "any",
                "label": "Install OpenMinion blockchain support",
                "command": [
                    "python",
                    "-m",
                    "pip",
                    "install",
                    "openminion[blockchain]",
                ],
                "official_url": "https://web3py.readthedocs.io/en/stable/quickstart.html",
                "note": "Installs the optional Web3.py dependency.",
            }
        ],
    }
