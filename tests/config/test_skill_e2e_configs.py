from __future__ import annotations

import json
from pathlib import Path

import pytest

from openminion.base.config import ConfigManager, OpenMinionConfig
from openminion.services.bootstrap.config import bootstrap_config_manager
from tests.helpers.live_cli_chat_alibaba import (
    LIVE_CLI_CHAT_TIMEOUT_ENV,
    LIVE_CODING_PROJECT_TIMEOUT_ENV,
    LIVE_SKILL_DENSE_TIMEOUT_ENV,
    LIVE_SKILL_SIMPLE_TIMEOUT_ENV,
    timeout_seconds,
)
from tests.helpers.live_skill_targets import (
    MATRIX_TYPE_DENSE,
    MATRIX_TYPE_SIMPLE,
    SURFACE_KIND_SKILL_E2E,
    SkillLiveTarget,
    cross_provider_skill_dense_targets,
    official_skill_dense_targets,
    official_skill_matrix_target_ids,
    representative_skill_dense_targets,
    skill_simple_targets,
    validate_skill_live_target,
)


FRAMEWORK_ROOT = Path(__file__).resolve().parents[3]
TEST_CONFIG_ROOT = FRAMEWORK_ROOT / "test-configs"
EXPECTED_SKILL_E2E_CONFIG_NAMES = (
    "per-agent-alibaba-minimax-skill-e2e.json",
    "per-agent-minimax-official-skill-e2e.json",
    "per-agent-openrouter-claude-haiku-4-5-skill-e2e.json",
    "per-agent-openrouter-glm-5-turbo-skill-e2e.json",
    "per-agent-openrouter-gpt-4o-skill-e2e.json",
    "per-agent-openrouter-minimax-m2-7-skill-e2e.json",
    "per-agent-openrouter-minimax-matrix-skill-e2e.json",
    "per-agent-openrouter-qwen3-5-35b-a3b-skill-e2e.json",
    "per-agent-openrouter-qwen3-5-9b-skill-e2e.json",
)
OFFICIAL_MINIMAX_CONFIG = TEST_CONFIG_ROOT / "per-agent-minimax-official.json"


def test_openminion_config_preserves_module_sections_roundtrip() -> None:
    payload = {
        "agents": {
            "hello-agent": {"name": "hello-agent", "provider": "openrouter"},
        },
        "providers": {"openrouter": {"model": "anthropic/claude-haiku-4.5"}},
        "skill": {
            "sqlite_path": "skill/skill.db",
            "default_status_filter": ["draft", "verified", "blessed"],
        },
        # `brain.context_budget_prerouting_enabled` removed; this
        # test now only asserts skill module config round-trips cleanly.
    }

    config = OpenMinionConfig.from_dict(payload)

    assert config.module_configs["skill"]["sqlite_path"] == "skill/skill.db"

    roundtrip = config.to_dict()
    assert roundtrip["skill"]["sqlite_path"] == "skill/skill.db"


def test_skill_e2e_checked_in_config_family_is_explicit() -> None:
    discovered = tuple(
        path.name for path in sorted(TEST_CONFIG_ROOT.glob("*skill-e2e.json"))
    )
    assert discovered == EXPECTED_SKILL_E2E_CONFIG_NAMES


def test_skill_e2e_configs_load_module_overrides(tmp_path: Path) -> None:
    for config_name in EXPECTED_SKILL_E2E_CONFIG_NAMES:
        manager = ConfigManager.load(
            config_path=str(TEST_CONFIG_ROOT / config_name),
            home_root=FRAMEWORK_ROOT,
            data_root=tmp_path / config_name.removesuffix(".json"),
        )
        bootstrap_config_manager(manager)

        skill_cfg = manager.get("skill")
        brain_cfg = manager.get("brain")

        assert skill_cfg.sqlite_path.endswith("/skill/skill.db")
        assert skill_cfg.default_status_filter == ["draft", "verified", "blessed"]
        _ = brain_cfg.brain.budgets  # cheap structural access to guard load
        assert (
            manager.base_config.module_configs["skill"]["sqlite_path"]
            == "skill/skill.db"
        )


def test_canonical_skill_live_targets_validate_checked_in_surfaces() -> None:
    validated: set[tuple[str, str, str, str]] = set()
    for target in (
        *skill_simple_targets(),
        *official_skill_dense_targets(),
        *cross_provider_skill_dense_targets(),
    ):
        key = (
            target.target_id,
            target.config_path.name,
            target.agent_id,
            target.surface_kind,
        )
        if key in validated:
            continue
        validate_skill_live_target(target)
        validated.add(key)


def test_validate_skill_live_target_rejects_wrong_profile_surface_pairing() -> None:
    with pytest.raises(AssertionError, match="not in configured ids"):
        validate_skill_live_target(
            SkillLiveTarget(
                target_id="wrong-openrouter-gpt-4o-surface",
                config_path=Path("per-agent-openrouter-gpt-4o.json"),
                agent_id="openrouter-gpt-4o",
                matrix_type=MATRIX_TYPE_DENSE,
                surface_kind=SURFACE_KIND_SKILL_E2E,
            )
        )


def test_official_skill_matrix_truth_is_explicit_and_current() -> None:
    official_ids = official_skill_matrix_target_ids()
    assert official_ids == ("minimax-m2-5", "minimax-m2-7")
    assert tuple(target.target_id for target in official_skill_dense_targets()) == (
        official_ids
    )

    representative_ids = {
        target.target_id for target in representative_skill_dense_targets()
    }
    assert representative_ids == {
        "minimax-m2-5",
        "minimax-m2-7",
        "ollamacloud-glm-5",
        "ollamacloud-minimax-m2-7",
        "openrouter-minimax-m2-7",
        "openrouter-claude-haiku-4-5",
        "openrouter-gpt-4o",
    }


def test_live_skill_timeout_seconds_uses_matrix_defaults_and_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for env_name in (
        LIVE_CLI_CHAT_TIMEOUT_ENV,
        LIVE_CODING_PROJECT_TIMEOUT_ENV,
        LIVE_SKILL_SIMPLE_TIMEOUT_ENV,
        LIVE_SKILL_DENSE_TIMEOUT_ENV,
    ):
        monkeypatch.delenv(env_name, raising=False)

    assert timeout_seconds() == 180
    assert timeout_seconds(MATRIX_TYPE_SIMPLE) == 180
    assert timeout_seconds(MATRIX_TYPE_DENSE) == 420
    assert timeout_seconds("coding_project") == 1200

    monkeypatch.setenv(LIVE_CLI_CHAT_TIMEOUT_ENV, "210")
    assert timeout_seconds() == 210
    assert timeout_seconds(MATRIX_TYPE_SIMPLE) == 210
    assert timeout_seconds(MATRIX_TYPE_DENSE) == 210
    assert timeout_seconds("coding_project") == 210

    monkeypatch.setenv(LIVE_SKILL_SIMPLE_TIMEOUT_ENV, "240")
    monkeypatch.setenv(LIVE_SKILL_DENSE_TIMEOUT_ENV, "480")
    monkeypatch.setenv(LIVE_CODING_PROJECT_TIMEOUT_ENV, "900")
    assert timeout_seconds(MATRIX_TYPE_SIMPLE) == 240
    assert timeout_seconds(MATRIX_TYPE_DENSE) == 480
    assert timeout_seconds("coding_project") == 900


def test_official_live_minimax_config_rebases_brain_runtime_budgets() -> None:
    payload = json.loads(OFFICIAL_MINIMAX_CONFIG.read_text(encoding="utf-8"))
    runtime_env = payload["runtime"]["env"]
    assert runtime_env["OPENMINION_BRAIN_MAX_ELAPSED_MS"] == "300000"
    assert runtime_env["OPENMINION_BRAIN_MAX_TICKS"] == "16"
    assert runtime_env["OPENMINION_BRAIN_MAX_TOTAL_LLM_TOKENS"] == "250000"
    assert runtime_env["OPENMINION_BRAIN_MAX_TOOL_CALLS"] == "32"
