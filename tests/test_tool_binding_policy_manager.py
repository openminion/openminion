from __future__ import annotations

from openminion.base.config import CapabilityBinding, ToolSelectionConfig
from openminion.modules.tool.bootstrap import build_runtime_bootstrap
from openminion.modules.tool.runtime.policy import ToolBindingPolicyManager


def test_policy_manager_from_config_reorders_runtime_chain() -> None:
    config = ToolSelectionConfig(
        runtime_bindings={
            "runtime.web.search": CapabilityBinding(
                primary="search.tavily.search",
                fallback_tools=["search.fallback"],
            )
        },
        runtime_binding_selection_strategy="ordered",
    )
    manager = ToolBindingPolicyManager.from_tool_selection_config(config)
    ordered = manager.reorder_runtime_chain(
        runtime_binding_id="runtime.web.search",
        default_chain=("search.fallback", "search.tavily.search"),
        available_tool_names=("search.fallback", "search.tavily.search"),
    )
    assert ordered == ("search.tavily.search", "search.fallback")


def test_policy_manager_config_overrides_registry_defaults_without_hiding_new_bindings() -> None:
    bootstrap = build_runtime_bootstrap(config=None, workspace_root=None, run_root=None)
    defaults = {
        binding_id: policy
        for binding_id, (primary, fallback_tools) in (
            bootstrap.manager.runtime_binding_policy_defaults().items()
        )
        if (
            policy := ToolBindingPolicyManager.default_policy(
                binding_id,
                (primary, *fallback_tools),
            )
        )
        is not None
    }
    config = ToolSelectionConfig(
        runtime_bindings={
            "runtime.web.search": CapabilityBinding(
                primary="search.serpapi.search",
                fallback_tools=["search.dispatch"],
            )
        }
    )

    manager = ToolBindingPolicyManager.from_tool_selection_config_with_defaults(
        config,
        default_policies=defaults,
    )

    assert manager.policy_for("runtime.web.search").primary == "search.serpapi.search"
    assert manager.policy_for("runtime.host.metrics").primary == "host.metrics"


def test_policy_manager_metadata_payload_contains_runtime_binding_fields() -> None:
    config = ToolSelectionConfig(
        runtime_bindings={
            "runtime.time.now": CapabilityBinding(
                primary="time.now",
                fallback_tools=["utility.utc_now"],
            )
        },
        runtime_binding_selection_strategy="ordered",
        runtime_fallback_on=["timeout", "provider_empty"],
        runtime_no_fallback_on=["policy_denied"],
    )
    manager = ToolBindingPolicyManager.from_tool_selection_config(config)
    payload = manager.metadata_payload()
    assert "runtime_binding_policies" in payload
    assert payload["runtime_binding_selection_strategy"] == "ordered"
    assert payload["runtime_fallback_on"] == ["timeout", "provider_empty"]
    assert payload["runtime_no_fallback_on"] == ["policy_denied"]


def test_policy_manager_from_payload_supports_dispatch_path() -> None:
    manager = ToolBindingPolicyManager.from_runtime_binding_policy_payload(
        {
            "runtime_binding_policies": {
                "runtime.web.fetch": {
                    "primary": "fetch.get",
                    "fallback_tools": ["gws.call", "fetch.head"],
                }
            }
        }
    )
    ordered = manager.reorder_runtime_chain(
        runtime_binding_id="runtime.web.fetch",
        default_chain=("gws.call", "fetch.get", "fetch.head"),
        available_tool_names=("gws.call", "fetch.get"),
    )
    assert ordered == ("fetch.get", "gws.call")


def test_policy_manager_should_fallback_respects_deny_precedence() -> None:
    manager = ToolBindingPolicyManager.from_runtime_binding_policy_payload(
        {
            "runtime_fallback_on": ["timeout", "unavailable"],
            "runtime_no_fallback_on": ["policy_denied", "approval"],
        }
    )
    assert manager.should_fallback(error_text="transient timeout from upstream")
    assert not manager.should_fallback(error_text="policy_denied timeout")
