from __future__ import annotations

import pytest

from openminion.base.config.base import ConfigError
from openminion.base.config.runtime.capability_resolution import (
    merge_tool_runtime_overrides,
)
from openminion.base.config.runtime.tools import (
    BlockchainToolRuntimeConfig,
    ToolRuntimeConfig,
    coerce_tool_runtime_config,
    tool_runtime_config_to_dict,
)


def _enabled_config(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "enabled": True,
        "rpc_url": "http://127.0.0.1:8545",
        "chain_id": 31337,
    }
    payload.update(overrides)
    return payload


def test_blockchain_config_defaults_round_trip_when_present() -> None:
    config = coerce_tool_runtime_config({"blockchain": _enabled_config()})

    assert config.blockchain == BlockchainToolRuntimeConfig(
        enabled=True,
        rpc_url="http://127.0.0.1:8545",
        chain_id=31337,
    )
    assert tool_runtime_config_to_dict(config) == {
        "blockchain": {
            "enabled": True,
            "rpc_url": "http://127.0.0.1:8545",
            "chain_id": 31337,
            "signer_secret_key": "",
            "signer_secret_namespace": "blockchain",
            "writes_enabled": False,
            "max_total_fee_wei": "10000000000000000",
            "receipt_timeout_seconds": 60,
        }
    }


def test_omitted_blockchain_config_stays_omitted() -> None:
    assert tool_runtime_config_to_dict(ToolRuntimeConfig()) == {}


def test_agent_blockchain_config_replaces_system_object_whole() -> None:
    system = ToolRuntimeConfig(
        blockchain=BlockchainToolRuntimeConfig(
            enabled=True,
            rpc_url="http://system:8545",
            chain_id=1,
        )
    )
    agent = ToolRuntimeConfig(
        blockchain=BlockchainToolRuntimeConfig(
            enabled=True,
            rpc_url="http://agent:8545",
            chain_id=31337,
            receipt_timeout_seconds=15,
        )
    )

    effective = merge_tool_runtime_overrides(
        system_tools=system,
        agent_tools=agent,
    )

    assert effective.blockchain is agent.blockchain
    assert effective.blockchain.rpc_url == "http://agent:8545"
    assert effective.blockchain.receipt_timeout_seconds == 15


@pytest.mark.parametrize(
    "payload",
    [
        _enabled_config(extra=True),
        _enabled_config(enabled="yes"),
        _enabled_config(chain_id=0),
        _enabled_config(receipt_timeout_seconds=0),
        _enabled_config(receipt_timeout_seconds=301),
        _enabled_config(max_total_fee_wei="01"),
        _enabled_config(max_total_fee_wei="0"),
        _enabled_config(signer_secret_namespace=""),
        {"enabled": True, "chain_id": 31337},
        {"enabled": True, "rpc_url": "http://localhost:8545"},
        {"writes_enabled": True},
        _enabled_config(writes_enabled=True),
    ],
)
def test_invalid_blockchain_config_is_rejected(payload: dict[str, object]) -> None:
    with pytest.raises(ConfigError):
        coerce_tool_runtime_config({"blockchain": payload})


def test_write_enabled_config_requires_and_serializes_signer_reference() -> None:
    config = coerce_tool_runtime_config(
        {
            "blockchain": _enabled_config(
                writes_enabled=True,
                signer_secret_key="local-signer",
                signer_secret_namespace="blockchain",
            )
        }
    )

    assert config.blockchain is not None
    assert config.blockchain.writes_enabled is True
    assert config.blockchain.signer_secret_key == "local-signer"
