from __future__ import annotations

from openminion.modules.llm.config import load_config, resolve_provider_config


def test_provider_cost_hint_parses_from_config() -> None:
    cfg = load_config(
        {
            "version": 1,
            "llmctl": {"default_provider": "openai", "default_model": "gpt-4o-mini"},
            "providers": {
                "openai": {
                    "api_key_env": "OPENAI_API_KEY",
                    "cost_hint": {
                        "input_per_1k": 0.01,
                        "output_per_1k": 0.02,
                    },
                }
            },
            "agents": {},
        }
    )

    assert cfg.providers["openai"].cost_hint is not None
    assert cfg.providers["openai"].cost_hint.input_per_1k == 0.01
    assert cfg.providers["openai"].cost_hint.output_per_1k == 0.02


def test_resolve_provider_config_includes_cost_hint() -> None:
    cfg = load_config(
        {
            "version": 1,
            "llmctl": {"default_provider": "openai", "default_model": "gpt-4o-mini"},
            "providers": {
                "openai": {
                    "api_key_env": "OPENAI_API_KEY",
                    "cost_hint": {
                        "input_per_1k": 0.01,
                        "output_per_1k": 0.02,
                    },
                }
            },
            "agents": {},
        }
    )

    resolved = resolve_provider_config(
        cfg, "openai", env={"OPENAI_API_KEY": "test-key"}
    )
    assert resolved["api_key"] == "test-key"
    assert resolved["cost_hint"] == {"input_per_1k": 0.01, "output_per_1k": 0.02}
