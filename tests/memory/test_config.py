from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
import warnings

import yaml

from openminion.modules.memory.config import (
    MEMCTL_CONFIG_ENV,
    CandidateLearningConfig,
    ConfigError,
    RankingConfig,
    load_config,
    merge_candidate_learning_config,
    merge_ranking_config,
)
from openminion.modules.memory.config import PromotionConfig
from openminion.modules.retrieve.config import DefaultsConfig


BASE_CONFIG: dict[str, object] = {
    "version": 1,
    "memctl": {
        "store": {
            "backend": "sqlite",
            "sqlite_path": "${HOME}/.memctl/memory.db",
            "sqlite": {
                "wal_mode": True,
                "busy_timeout_ms": 7000,
                "fts5_enabled": False,
            },
        },
        "defaults": {
            "confidence": {
                "user_said": 0.7,
                "tool_output": 0.8,
                "agent_inferred": 0.4,
                "validated": 0.95,
                "imported": 0.9,
            }
        },
        "promotion": {
            "require_approval_for_global": True,
            "auto_promote_agent_procedures": True,
            "allowlisted_auto_rules": [],
        },
        "retrieval": {
            "max_results": 25,
            "min_confidence_default": 0.55,
            "pin_first": True,
        },
        "retention": {
            "enable_soft_delete": True,
            "gc_enabled": True,
            "gc_batch_size": 500,
        },
    },
}


def _write_config(
    tmp_path: Path, payload: dict[str, object], name: str = "memory.yaml"
) -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return path


class ConfigLoaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)

    def test_load_from_path(self) -> None:
        config_data = deepcopy(BASE_CONFIG)
        path = _write_config(self.tmp_path, config_data)
        env = {"HOME": str(self.tmp_path)}

        config = load_config(path, env=env)

        expected_sqlite_path = (self.tmp_path / ".memctl/memory.db").resolve()
        self.assertEqual(config.version, 1)
        self.assertEqual(config.store.backend, "sqlite")
        self.assertEqual(config.store.sqlite_path, expected_sqlite_path)
        self.assertEqual(config.defaults.confidence["user_said"], 0.7)

    def test_env_variable_path_resolution(self) -> None:
        path = _write_config(self.tmp_path, deepcopy(BASE_CONFIG), name="custom.yaml")
        env = {
            "HOME": str(self.tmp_path),
            MEMCTL_CONFIG_ENV: str(path),
        }

        config = load_config(None, env=env)

        self.assertEqual(config.retrieval.max_results, 25)

    def test_missing_file_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            load_config(self.tmp_path / "missing.yaml")

    def test_invalid_backend_raises(self) -> None:
        data = deepcopy(BASE_CONFIG)
        data["memctl"]["store"]["backend"] = "invalid"  # type: ignore[index]
        path = _write_config(self.tmp_path, data)

        with self.assertRaises(ConfigError):
            load_config(path)

    def test_confidence_out_of_range(self) -> None:
        data = deepcopy(BASE_CONFIG)
        data["memctl"]["defaults"]["confidence"]["user_said"] = 2.0  # type: ignore[index]
        path = _write_config(self.tmp_path, data)

        with self.assertRaises(ConfigError):
            load_config(path)

    def test_mock_backend_allows_missing_sqlite_path(self) -> None:
        data = deepcopy(BASE_CONFIG)
        data["memctl"]["store"]["backend"] = "mock"  # type: ignore[index]
        data["memctl"]["store"].pop("sqlite_path", None)  # type: ignore[index]
        path = _write_config(self.tmp_path, data)

        config = load_config(path, env={"HOME": str(self.tmp_path)})
        self.assertEqual(config.store.backend, "mock")
        self.assertIsNone(config.store.sqlite_path)

    def test_remote_backend_requires_endpoint_and_parses_transport_knobs(self) -> None:
        data = deepcopy(BASE_CONFIG)
        data["memctl"]["store"]["backend"] = "remote"  # type: ignore[index]
        data["memctl"]["store"]["remote"] = {  # type: ignore[index]
            "endpoint": "https://memory.example/v1",
            "timeout_seconds": 2.5,
            "max_retries": 2,
        }
        path = _write_config(self.tmp_path, data)

        config = load_config(path, env={"HOME": str(self.tmp_path)})
        self.assertEqual(config.store.backend, "remote")
        self.assertEqual(config.store.remote_endpoint, "https://memory.example/v1")
        self.assertEqual(config.store.remote_timeout_seconds, 2.5)
        self.assertEqual(config.store.remote_max_retries, 2)

    def test_postgres_backend_requires_url_and_parses_it(self) -> None:
        data = deepcopy(BASE_CONFIG)
        data["memctl"]["store"]["backend"] = "postgres"  # type: ignore[index]
        data["memctl"]["store"]["postgres"] = {  # type: ignore[index]
            "url": "postgresql+psycopg://test@localhost/postgres"
        }
        path = _write_config(self.tmp_path, data)

        config = load_config(path, env={"HOME": str(self.tmp_path)})
        self.assertEqual(config.store.backend, "postgres")
        self.assertEqual(
            config.store.postgres_url,
            "postgresql+psycopg://test@localhost/postgres",
        )

    def test_ranking_section_parses_and_normalizes_weights(self) -> None:
        data = deepcopy(BASE_CONFIG)
        data["memctl"]["ranking"] = {  # type: ignore[index]
            "w_relevance": 5.0,
            "w_recency": 3.0,
            "w_feedback": 1.0,
            "w_type_bonus": 1.0,
            "w_confidence": 0.0,
            "w_outcome_utility": 0.0,
            "semantic_bm25_weight": 0.7,
            "type_boost_meta_insight": 1.08,
        }
        path = _write_config(self.tmp_path, data)

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            config = load_config(path, env={"HOME": str(self.tmp_path)})

        self.assertAlmostEqual(
            config.ranking.w_relevance
            + config.ranking.w_recency
            + config.ranking.w_feedback
            + config.ranking.w_type_bonus
            + config.ranking.w_confidence
            + config.ranking.w_outcome_utility,
            1.0,
        )
        self.assertEqual(config.ranking.semantic_bm25_weight, 0.7)
        self.assertEqual(config.ranking.type_boost_meta_insight, 1.08)
        self.assertTrue(any("auto-normalizing" in str(item.message) for item in caught))

    def test_reflection_section_parses_phase6_promotion_fields(self) -> None:
        data = deepcopy(BASE_CONFIG)
        data["memctl"]["reflection"] = {  # type: ignore[index]
            "reflection_enabled": True,
            "reflection_interval_sessions": 4,
            "contradiction_similarity_threshold": 0.75,
            "max_insights_per_reflection": 6,
            "promotion_enabled": False,
            "correction_promotion_min_count": 4,
            "correction_promotion_confidence": 0.9,
            "preference_stability_min_sessions": 6,
            "preference_stability_boost": 0.12,
            "max_correction_promotions_per_run": 3,
            "max_preference_boosts_per_run": 4,
            "reboost_cooldown_multiplier": 3.5,
        }
        path = _write_config(self.tmp_path, data)

        config = load_config(path, env={"HOME": str(self.tmp_path)})

        self.assertFalse(config.reflection.promotion_enabled)
        self.assertEqual(config.reflection.correction_promotion_min_count, 4)
        self.assertEqual(config.reflection.correction_promotion_confidence, 0.9)
        self.assertEqual(config.reflection.preference_stability_min_sessions, 6)
        self.assertEqual(config.reflection.preference_stability_boost, 0.12)
        self.assertEqual(config.reflection.max_correction_promotions_per_run, 3)
        self.assertEqual(config.reflection.max_preference_boosts_per_run, 4)
        self.assertEqual(config.reflection.reboost_cooldown_multiplier, 3.5)

    def test_consolidation_section_parses_contract_defaults_and_model_override(
        self,
    ) -> None:
        data = deepcopy(BASE_CONFIG)
        data["memctl"]["consolidation"] = {  # type: ignore[index]
            "recent_rollout_limit": 128,
            "idle_seconds_before_eligible": 1800,
            "min_rate_limit_remaining_percent": 40,
            "consolidation_model": "gpt-4.2-mini",
        }
        path = _write_config(self.tmp_path, data)

        config = load_config(path, env={"HOME": str(self.tmp_path)})

        self.assertEqual(config.consolidation.recent_rollout_limit, 128)
        self.assertEqual(config.consolidation.idle_seconds_before_eligible, 1800)
        self.assertEqual(
            config.consolidation.min_rate_limit_remaining_percent,
            40,
        )
        self.assertEqual(config.consolidation.consolidation_model, "gpt-4.2-mini")

    def test_consolidation_defaults_are_present_without_explicit_section(self) -> None:
        path = _write_config(self.tmp_path, deepcopy(BASE_CONFIG))

        config = load_config(path, env={"HOME": str(self.tmp_path)})

        self.assertEqual(config.consolidation.recent_rollout_limit, 256)
        self.assertEqual(config.consolidation.idle_seconds_before_eligible, 21600)
        self.assertEqual(config.consolidation.min_rate_limit_remaining_percent, 25)
        self.assertIsNone(config.consolidation.consolidation_model)

    def test_merge_ranking_config_translates_legacy_retrieve_defaults(self) -> None:
        defaults = DefaultsConfig(
            decay_halflife_days=45,
            mmr_enabled=False,
            mmr_lambda=0.2,
            feedback_decay_halflife_days=90,
            recency_half_life_hours=120,
        )

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            merged = merge_ranking_config(None, retrieve_defaults=defaults)

        self.assertEqual(merged.recency_half_life_days, 45)
        self.assertEqual(merged.mmr_enabled, False)
        self.assertEqual(merged.mmr_lambda, 0.2)
        self.assertEqual(merged.feedback_decay_halflife_days, 90)
        self.assertTrue(
            any("decay_halflife_days" in str(item.message) for item in caught)
        )

    def test_explicit_ranking_config_wins_over_legacy_defaults(self) -> None:
        defaults = DefaultsConfig(decay_halflife_days=45, mmr_lambda=0.2)
        explicit = RankingConfig(recency_half_life_days=14.0, mmr_lambda=0.9)

        merged = merge_ranking_config(explicit, retrieve_defaults=defaults)

        self.assertEqual(merged.recency_half_life_days, 14.0)
        self.assertEqual(merged.mmr_lambda, 0.9)

    def test_candidate_learning_section_parses_and_normalizes_weights(self) -> None:
        data = deepcopy(BASE_CONFIG)
        data["memctl"]["candidate_learning"] = {  # type: ignore[index]
            "w_reconfirmation": 3.0,
            "w_retrieval_hits": 2.0,
            "w_survival": 1.0,
            "w_confidence": 0.0,
            "w_correction_resistance": 0.0,
            "w_outcome_utility": 0.0,
            "auto_extract_enabled": True,
            "auto_extract_notify": False,
        }
        path = _write_config(self.tmp_path, data)

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            config = load_config(path, env={"HOME": str(self.tmp_path)})

        self.assertTrue(config.candidate_learning.auto_extract_enabled)
        self.assertFalse(config.candidate_learning.auto_extract_notify)
        self.assertEqual(config.candidate_learning.min_trust_for_promotion, 0.5)
        self.assertAlmostEqual(
            config.candidate_learning.w_reconfirmation
            + config.candidate_learning.w_retrieval_hits
            + config.candidate_learning.w_survival
            + config.candidate_learning.w_confidence
            + config.candidate_learning.w_correction_resistance
            + config.candidate_learning.w_outcome_utility,
            1.0,
        )
        self.assertTrue(any("auto-normalizing" in str(item.message) for item in caught))

    def test_merge_candidate_learning_config_translates_legacy_promotion_fields(
        self,
    ) -> None:
        promotion = PromotionConfig(
            require_approval_for_global=True,
            auto_promote_agent_procedures=False,
            allowlisted_auto_rules=[],
            auto_extract_enabled=False,
            auto_extract_halflife_days=21,
            auto_extract_notify=False,
        )

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            merged = merge_candidate_learning_config(None, promotion=promotion)

        self.assertFalse(merged.auto_extract_enabled)
        self.assertFalse(merged.auto_extract_notify)
        self.assertEqual(merged.survival_halflife_days, 21.0)
        self.assertTrue(
            any("auto_extract_enabled" in str(item.message) for item in caught)
        )

    def test_explicit_candidate_learning_wins_over_legacy_promotion(self) -> None:
        explicit = CandidateLearningConfig(
            auto_extract_enabled=False,
            auto_extract_notify=False,
            survival_halflife_days=5.0,
        )
        promotion = PromotionConfig(
            require_approval_for_global=True,
            auto_promote_agent_procedures=False,
            allowlisted_auto_rules=[],
            auto_extract_enabled=True,
            auto_extract_halflife_days=21,
            auto_extract_notify=False,
        )

        merged = merge_candidate_learning_config(explicit, promotion=promotion)

        self.assertEqual(merged.survival_halflife_days, 5.0)
        self.assertFalse(merged.auto_extract_notify)
