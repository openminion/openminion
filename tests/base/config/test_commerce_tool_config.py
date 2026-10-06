from __future__ import annotations

import pytest

from openminion.base.config.base import ConfigError
from openminion.base.config.runtime.capability_resolution import (
    merge_tool_runtime_overrides,
)
from openminion.base.config.runtime.tools import (
    CommerceToolRuntimeConfig,
    ToolRuntimeConfig,
    coerce_tool_runtime_config,
    tool_runtime_config_to_dict,
)


def test_disabled_commerce_config_round_trips() -> None:
    config = coerce_tool_runtime_config(
        {
            "commerce": {
                "enabled": False,
                "provider": "future-provider",
                "base_url": "https://merchant.example",
                "merchant_id": "merchant-1",
                "provider_secret_key": "merchant-token",
                "buyer_profile_record_id": "buyer-1",
                "payment_token_record_id": "payment-1",
                "writes_enabled": False,
                "order_actions_enabled": False,
            }
        }
    )

    assert config.commerce == CommerceToolRuntimeConfig(
        provider="future-provider",
        base_url="https://merchant.example",
        merchant_id="merchant-1",
        provider_secret_key="merchant-token",
        buyer_profile_record_id="buyer-1",
        payment_token_record_id="payment-1",
    )
    assert coerce_tool_runtime_config(tool_runtime_config_to_dict(config)) == config


def test_omitted_commerce_config_stays_omitted() -> None:
    assert tool_runtime_config_to_dict(ToolRuntimeConfig()) == {}


@pytest.mark.parametrize(
    "payload",
    [
        {"extra": True},
        {"enabled": "yes"},
        {"writes_enabled": "yes"},
        {"order_actions_enabled": "yes"},
        {"writes_enabled": True},
        {"enabled": True, "order_actions_enabled": True},
        {"enabled": True},
        {"enabled": True, "provider": "unknown-provider"},
        {"enabled": True, "provider": "fixture"},
    ],
)
def test_invalid_or_unavailable_commerce_config_is_rejected(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ConfigError):
        coerce_tool_runtime_config({"commerce": payload})


def test_agent_override_may_disable_without_replacing_system_values() -> None:
    system = ToolRuntimeConfig(
        commerce=CommerceToolRuntimeConfig(
            provider="selected-provider",
            base_url="https://merchant.example",
            merchant_id="merchant-1",
            provider_secret_key="merchant-token",
            buyer_profile_record_id="buyer-1",
            payment_token_record_id="payment-1",
        )
    )
    agent = ToolRuntimeConfig(commerce=CommerceToolRuntimeConfig(enabled=False))

    effective = merge_tool_runtime_overrides(
        system_tools=system,
        agent_tools=agent,
    )

    assert effective.commerce == CommerceToolRuntimeConfig(
        provider="selected-provider",
        base_url="https://merchant.example",
        merchant_id="merchant-1",
        provider_secret_key="merchant-token",
        buyer_profile_record_id="buyer-1",
        payment_token_record_id="payment-1",
    )


@pytest.mark.parametrize(
    "agent_commerce",
    [
        CommerceToolRuntimeConfig(provider="other"),
        CommerceToolRuntimeConfig(merchant_id="other"),
        CommerceToolRuntimeConfig(buyer_profile_record_id="other"),
        CommerceToolRuntimeConfig(payment_token_record_id="other"),
    ],
)
def test_agent_override_cannot_replace_system_owned_values(
    agent_commerce: CommerceToolRuntimeConfig,
) -> None:
    system = ToolRuntimeConfig(
        commerce=CommerceToolRuntimeConfig(
            provider="selected-provider",
            merchant_id="merchant-1",
            buyer_profile_record_id="buyer-1",
            payment_token_record_id="payment-1",
        )
    )

    with pytest.raises(ConfigError):
        merge_tool_runtime_overrides(
            system_tools=system,
            agent_tools=ToolRuntimeConfig(commerce=agent_commerce),
        )
