"""Native value caps: config surface, prepare refusal and send refusal."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from eth_account import Account
from web3 import Web3

from openminion.base.config.runtime.tool_family import (
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
    message = str(raised.value)
    assert "max_value_wei" in message and "decimal string" in message


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


class _StubEth:
    """Enough chain surface for the send-state revalidation, and no broadcast."""

    chain_id = 31337

    def __init__(self) -> None:
        self.broadcasts = 0
        self.account = SimpleNamespace(
            from_key=lambda key: SimpleNamespace(address=Account.from_key(key).address)
        )

    def get_transaction_count(self, address: str, block: str) -> int:
        return 9

    def call(self, transaction: dict[str, Any], block: str) -> str:
        return "0x"

    def send_raw_transaction(self, raw: bytes) -> str:  # pragma: no cover
        self.broadcasts += 1
        raise AssertionError("a refused send must not broadcast")


class _StubWeb3:
    def __init__(self) -> None:
        self.eth = _StubEth()

    @staticmethod
    def to_checksum_address(value: str) -> str:
        return Web3.to_checksum_address(value)


def _transaction(value_wei: str) -> dict[str, Any]:
    return {
        "schema_version": "evm-transaction-v1",
        "transaction_type": "eip1559",
        "chain_id": 31337,
        "from_address": Account.from_key(PRIVATE_KEY).address,
        "to_address": RECIPIENT,
        "value_wei": value_wei,
        "nonce": "9",
        "gas_limit": "21000",
        "data": "0x",
        "max_fee_per_gas_wei": "23",
        "max_priority_fee_per_gas_wei": "3",
        "max_total_fee_wei": "483000",
    }


def test_send_refuses_value_over_the_cap_before_broadcast() -> None:
    """The configured path reports the cap like it reports the fee cap: a stale field."""
    client = _StubWeb3()
    transaction = _transaction("1001")

    result = send_transaction(
        {
            "transaction": transaction,
            "call_context": None,
            "preparation_digest": preparation_digest(transaction, None),
        },
        _context(secret_service=_SecretService(), max_value_wei="1000"),
        web3=client,
    )

    assert result["ok"] is False
    assert result["error"]["details"]["fields"] == ["value"]
    assert client.eth.broadcasts == 0


def test_the_send_boundary_is_inclusive() -> None:
    """Equal value is not stale; one wei more is."""
    from openminion.tools.blockchain.cap_checks import stale_cap_fields

    config = coerce_blockchain_tool_runtime_config(
        {"enabled": True, "max_value_wei": "1000"}
    )
    assert config is not None
    assert stale_cap_fields(config, _transaction("1000")) == []
    assert stale_cap_fields(config, _transaction("1001")) == ["value"]


def test_the_fee_field_is_still_reported_alongside_the_value_field() -> None:
    """Both caps are evaluated, so a transaction can report both stale fields."""
    from openminion.tools.blockchain.cap_checks import stale_cap_fields

    config = coerce_blockchain_tool_runtime_config(
        {"enabled": True, "max_value_wei": "1000", "max_total_fee_wei": "100"}
    )
    assert config is not None
    assert stale_cap_fields(config, _transaction("1001")) == ["fees", "value"]


def test_the_resolved_send_refuses_over_cap_before_any_broadcast() -> None:
    """The mainnet send path refuses at its own gate, beside the fee cap."""
    from openminion.tools.blockchain.resolved_operations import (
        _load_validated_send_signer,
    )

    from openminion.tools.blockchain.runtime import resolve_blockchain_config

    context = _context(secret_service=_SecretService(), max_value_wei="1000")
    config = resolve_blockchain_config(context)
    transaction = _transaction("1001")
    request = SimpleNamespace(
        transaction=SimpleNamespace(model_dump=lambda mode="json": transaction),
        preparation_digest="sha256:" + "1" * 64,
        signer_address=Account.from_key(PRIVATE_KEY).address,
    )

    _, _, _, error = _load_validated_send_signer(request, context, config)

    assert error is not None
    assert error["error"]["code"] == "VALUE_CAP_EXCEEDED"
    assert error["error"]["details"] == {
        "value_wei": "1001",
        "configured_max_value_wei": "1000",
    }
