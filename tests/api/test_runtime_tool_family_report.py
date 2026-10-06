from types import SimpleNamespace

from openminion.api.queries import tool_families
from openminion.base.config import (
    OpenMinionConfig,
    build_capability_runtime_diagnostics,
    resolve_runtime_profile,
)


def _search_item(config: OpenMinionConfig, monkeypatch):
    profile = resolve_runtime_profile(config)
    policy = build_capability_runtime_diagnostics(config)["tools"]
    monkeypatch.setattr(
        tool_families,
        "_search_diagnostics",
        lambda payload: {
            "available_providers": ["tavily", "duckduckgo"],
            "effective_provider_order": list(
                payload.get("provider_order") or ["tavily", "duckduckgo"]
            ),
            "effective_default_provider": str(
                payload.get("default_provider") or "tavily"
            ),
            "providers": [],
        },
    )
    items = tool_families.build_tool_family_items(
        SimpleNamespace(config=config),
        profile=profile,
        tool_policy=policy,
    )
    return next(item for item in items if item["name"] == "search")


def test_tool_family_report_exposes_code_default_source(monkeypatch) -> None:
    config = OpenMinionConfig.from_dict(
        {"default_agent": "mini", "agents": {"mini": {"name": "mini"}}}
    )

    search = _search_item(config, monkeypatch)

    assert search["configured"] is False
    assert search["source_layer"] == "code_default"
    assert search["effective_provider_order"] == ["tavily", "duckduckgo"]
    assert search["effective_default_provider"] == "tavily"


def test_tool_family_report_exposes_agent_override_source(monkeypatch) -> None:
    config = OpenMinionConfig.from_dict(
        {
            "default_agent": "mini",
            "runtime": {
                "tools": {
                    "search": {
                        "enabled_providers": ["tavily", "duckduckgo"],
                        "provider_order": ["tavily", "duckduckgo"],
                    }
                }
            },
            "agents": {
                "mini": {
                    "name": "mini",
                    "tools": {
                        "search": {
                            "enabled_providers": ["duckduckgo"],
                            "default_provider": "duckduckgo",
                            "provider_order": ["duckduckgo"],
                        }
                    },
                }
            },
        }
    )

    search = _search_item(config, monkeypatch)

    assert search["configured"] is True
    assert search["source_layer"] == "agent_profile"
    assert search["enabled_providers"] == ["duckduckgo"]
    assert search["effective_provider_order"] == ["duckduckgo"]
    assert search["effective_default_provider"] == "duckduckgo"
