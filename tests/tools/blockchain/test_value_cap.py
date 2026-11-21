"""Native value caps: config surface, prepare refusal and send refusal."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from eth_account import Account
from web3 import Web3

from openminion.base.config.runtime.tool_family import (
    BlockchainToolRuntimeConfig,
    coerce_blockchain_tool_runtime_config,
)
from openminion.modules.tool.plugin_api import PolicyAuthorization
from openminion.tools.blockchain.runtime import (
    preparation_digest,
    prepare_transaction,
    send_transaction,
)

PRIVATE_KEY = "0x" + "11" * 32
RECIPIENT = Web3.to_checksum_address("0x" + "22" * 20)


class _SecretService:
    def __init__(self, value: str = PRIVATE_KEY) -> None:
        self.value = value
        self.reads: list[tuple[str, str]] = []

    def get_secret_sync(self, key: str, *, namespace: str) -> str:
        self.reads.append((key, namespace))
        return self.value


class _NoRpcWeb3:
    """Any RPC use is a test failure: a cap refusal must precede the network."""

    def HTTPProvider(self, *_args: Any, **_kwargs: Any) -> Any:  # pragma: no cover
        raise AssertionError("cap refusal must not reach the RPC transport")


def _context(*, secret_service: Any | None = None, **blockchain: Any) -> SimpleNamespace:
    config = {
        "enabled": True,
        "rpc_url": "http://127.0.0.1:8545",
        "chain_id": 31337,
        "signer_secret_key": "signer-reference-sentinel",
        "signer_secret_namespace": "chain-reference-sentinel",
        "writes_enabled": True,
        "max_total_fee_wei": "10000000000000000",
        "receipt_timeout_seconds": 60,
    }
    config.update(blockchain)
    return SimpleNamespace(
        secret_service=secret_service,
        policy=SimpleNamespace(
            raw={
                "context_metadata": {
                    "runtime_tools": {"blockchain": config},
                }
            }
        ),
        policy_authorization=PolicyAuthorization(
            tool="blockchain",
            method="send_transaction",
            invocation_hash="hash",
            approval_id="approval",
            grant_id="grant",
            duration_type="once",
        ),
        write_audit_event=lambda event: None,
        events=[],
    )


def test_the_cap_is_absent_by_default_and_coerced_when_present() -> None:
    default = coerce_blockchain_tool_runtime_config({"enabled": True})
    assert default is not None and default.max_value_wei == ""

    configured = coerce_blockchain_tool_runtime_config(
        {"enabled": True, "max_value_wei": " 2500000000000000000 "}
    )
    assert configured is not None
    assert configured.max_value_wei == "2500000000000000000"

    zero = coerce_blockchain_tool_runtime_config({"enabled": True, "max_value_wei": "0"})
    assert zero is not None and zero.max_value_wei == "0"


@pytest.mark.parametrize("bad", ["-1", "1.5", "0x10", "1e18", " 1 2"])
def test_the_cap_rejects_non_canonical_decimals(bad: str) -> None:
    with pytest.raises(Exception) as raised:
        coerce_blockchain_tool_runtime_config({"enabled": True, "max_value_wei": bad})
    assert "max_value_wei" in str(raised.value)


def test_prepare_refuses_value_over_the_cap_before_any_rpc() -> None:
    secret_service = _SecretService()
    context = _context(secret_service=secret_service, max_value_wei="1000")

    result = prepare_transaction(
        {"kind": "native_transfer", "to_address": RECIPIENT, "value_wei": "1001"},
        context,
        web3=_NoRpcWeb3(),
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "VALUE_CAP_EXCEEDED"
    assert result["error"]["details"]["value_wei"] == "1001"
    assert result["error"]["details"]["configured_max_value_wei"] == "1000"
    assert secret_service.reads == []


def test_send_refuses_value_over_the_cap_before_signing() -> None:
    secret_service = _SecretService()
    context = _context(secret_service=secret_service, max_value_wei="1000")
    transaction = {
        "schema_version": "evm-transaction-v1",
        "transaction_type": "eip1559",
        "chain_id": 31337,
        "from_address": Account.from_key(PRIVATE_KEY).address,
        "to_address": RECIPIENT,
        "value_wei": "1001",
        "nonce": "0",
        "gas_limit": "21000",
        "data": "0x",
        "max_fee_per_gas_wei": "1000000000",
        "max_priority_fee_per_gas_wei": "1000000000",
        "max_total_fee_wei": "21000000000000",
    }

    result = send_transaction(
        {
            "transaction": transaction,
            "call_context": None,
            "preparation_digest": preparation_digest(transaction, None),
        },
        context,
        web3=_NoRpcWeb3(),
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "VALUE_CAP_EXCEEDED"
    assert secret_service.reads == []


def _signer_unavailable(code: str) -> bool:
    return code == "SIGNER_UNAVAILABLE"


def test_the_boundary_is_inclusive_and_zero_value_still_passes() -> None:
    """value == cap and value == 0 are allowed; the guard only refuses strictly above."""
    at_cap = prepare_transaction(
        {"kind": "native_transfer", "to_address": RECIPIENT, "value_wei": "1000"},
        _context(max_value_wei="1000"),
    )
    zero_value = prepare_transaction(
        {"kind": "native_transfer", "to_address": RECIPIENT, "value_wei": "0"},
        _context(max_value_wei="0"),
    )
    for result in (at_cap, zero_value):
        assert result["error"]["code"] != "VALUE_CAP_EXCEEDED"
        assert _signer_unavailable(result["error"]["code"])


def test_a_zero_cap_refuses_any_non_zero_value() -> None:
    result = prepare_transaction(
        {"kind": "native_transfer", "to_address": RECIPIENT, "value_wei": "1"},
        _context(max_value_wei="0"),
    )
    assert result["error"]["code"] == "VALUE_CAP_EXCEEDED"
    assert result["error"]["details"]["configured_max_value_wei"] == "0"


def test_the_cap_covers_the_resolved_prepare_entry() -> None:
    """The resolved kind is refused before any stored resolution is consulted."""
    result = prepare_transaction(
        {
            "kind": "resolved_contract_call",
            "resolution_digest": "sha256:" + "1" * 64,
            "function_signature": "deposit()",
            "arguments": [],
            "value_wei": "5000",
        },
        _context(max_value_wei="1000"),
    )
    assert result["error"]["code"] == "VALUE_CAP_EXCEEDED"
    assert result["error"]["details"]["value_wei"] == "5000"


def test_the_serialized_config_keeps_the_cap() -> None:
    from openminion.base.config.runtime.tool_family import (
        blockchain_tool_runtime_config_to_dict,
    )

    config = BlockchainToolRuntimeConfig(enabled=True, max_value_wei="42")
    assert blockchain_tool_runtime_config_to_dict(config)["max_value_wei"] == "42"
