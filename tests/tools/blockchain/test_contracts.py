from __future__ import annotations

from importlib import metadata
import json

import pytest
from pydantic import ValidationError

import openminion.tools.blockchain as blockchain_package
from openminion.modules.tool.contracts.model_ids import (
    ALL_MODEL_TOOL_IDS_SET,
    DEFAULT_VISIBLE_MODEL_TOOL_IDS_SET,
    MODEL_BLOCKCHAIN_INSPECT,
    MODEL_BLOCKCHAIN_PREPARE_TRANSACTION,
    MODEL_BLOCKCHAIN_SEND_TRANSACTION,
)
from openminion.modules.tool.contracts.runtime_ids import (
    ALL_RUNTIME_BINDING_IDS_SET,
    RUNTIME_BLOCKCHAIN_INSPECT,
    RUNTIME_BLOCKCHAIN_PREPARE_TRANSACTION,
    RUNTIME_BLOCKCHAIN_SEND_TRANSACTION,
)
from openminion.modules.tool.runtime.policy_defaults import DEFAULT_POLICY
from openminion.modules.tool.contracts.dependencies import ToolDependencyProbeContext
from openminion.base.config.env import EnvironmentConfig
from openminion.tools.blockchain.plugin import WEB3_DEPENDENCY
from openminion.tools.blockchain.schemas import (
    INSPECT_REQUEST_ADAPTER,
    PREPARE_REQUEST_ADAPTER,
    AbiParameter,
    SendTransactionArgs,
)

MODEL_IDS = {
    MODEL_BLOCKCHAIN_INSPECT,
    MODEL_BLOCKCHAIN_PREPARE_TRANSACTION,
    MODEL_BLOCKCHAIN_SEND_TRANSACTION,
}
RUNTIME_IDS = {
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


def test_send_schema_accepts_json_encoded_nested_objects() -> None:
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

    parsed = SendTransactionArgs.model_validate(
        {
            "transaction": json.dumps(transaction),
            "call_context": None,
            "preparation_digest": "sha256:" + "0" * 64,
        }
    )

    assert parsed.transaction.model_dump(mode="json") == transaction


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
