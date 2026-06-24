# mypy: disable-error-code="attr-defined"

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, get_args
import warnings

from openminion.base.config import OpenMinionConfig

from ..constants import (
    DEFAULT_CONFIDENCE,
    DEFAULT_INTEGRATED_SQLITE_SUBPATH,
)
from ..models import MemorySource


class ConfigError(ValueError):
    """Raised when configuration payloads are invalid."""


@dataclass(frozen=True)
class SQLiteRuntimeConfig:
    wal_mode: bool
    busy_timeout_ms: int
    fts5_enabled: bool


@dataclass(frozen=True)
class StoreConfig:
    backend: str
    sqlite_path: Path | None
    sqlite: SQLiteRuntimeConfig
    postgres_url: str = ""
    remote_endpoint: str = ""
    remote_timeout_seconds: float = 5.0
    remote_max_retries: int = 1


@dataclass(frozen=True)
class DefaultsConfig:
    confidence: dict[MemorySource, float]


@dataclass(frozen=True)
class PromotionConfig:
    require_approval_for_global: bool
    auto_promote_agent_procedures: bool
    allowlisted_auto_rules: list[dict[str, Any]]
    auto_extract_enabled: bool
    auto_extract_halflife_days: int
    auto_extract_notify: bool


@dataclass(frozen=True)
class RankingConfig:
    w_relevance: float = 0.45
    w_recency: float = 0.12
    w_feedback: float = 0.08
    w_type_bonus: float = 0.13
    w_confidence: float = 0.07
    w_outcome_utility: float = 0.15
    recency_half_life_days: float = 30.0
    feedback_hit_divisor: float = 10.0
    feedback_max: float = 1.0
    type_boost_correction: float = 1.5
    type_boost_user_preference: float = 1.3
    type_boost_pin: float = 1.2
    type_boost_project_convention: float = 1.1
    type_boost_meta_insight: float = 1.05
    feedback_decay_halflife_days: float = 60.0
    semantic_bm25_weight: float = 0.5
    mmr_enabled: bool = True
    mmr_lambda: float = 0.6

    def __post_init__(self) -> None:
        weights = {
            "w_relevance": float(self.w_relevance),
            "w_recency": float(self.w_recency),
            "w_feedback": float(self.w_feedback),
            "w_type_bonus": float(self.w_type_bonus),
            "w_confidence": float(self.w_confidence),
            "w_outcome_utility": float(self.w_outcome_utility),
        }
        if any(value < 0.0 for value in weights.values()):
            raise ConfigError("ranking weights must be non-negative")
        total = sum(weights.values())
        if total <= 0.0:
            raise ConfigError("ranking weights must sum to a positive value")
        if abs(total - 1.0) > 1e-9:
            warnings.warn(
                "RankingConfig weights do not sum to 1.0; auto-normalizing.",
                UserWarning,
                stacklevel=2,
            )
            for name, value in weights.items():
                object.__setattr__(self, name, value / total)

    @property
    def weights(self) -> Any:
        from openminion.modules.memory.runtime.scorer import RankingWeights

        return RankingWeights(
            relevance=self.w_relevance,
            recency=self.w_recency,
            feedback=self.w_feedback,
            type_bonus=self.w_type_bonus,
            confidence=self.w_confidence,
            outcome_utility=self.w_outcome_utility,
        )


@dataclass(frozen=True)
class CandidateLearningConfig:
    auto_extract_enabled: bool = True
    auto_extract_notify: bool = True
    w_reconfirmation: float = 0.25
    w_retrieval_hits: float = 0.20
    w_survival: float = 0.10
    w_confidence: float = 0.10
    w_correction_resistance: float = 0.15
    w_outcome_utility: float = 0.20
    promotion_readiness_threshold: float = 0.6
    min_trust_for_promotion: float = 0.5
    reconfirmation_target: int = 2
    retrieval_hit_target: int = 3
    survival_halflife_days: float = 7.0
    candidate_max_age_days: int = 30
    confidence_boost_per_reconfirmation: float = 0.1
    confidence_max: float = 0.9
    confidence_initial_auto_extract: float = 0.4

    def __post_init__(self) -> None:
        weights = {
            "w_reconfirmation": float(self.w_reconfirmation),
            "w_retrieval_hits": float(self.w_retrieval_hits),
            "w_survival": float(self.w_survival),
            "w_confidence": float(self.w_confidence),
            "w_correction_resistance": float(self.w_correction_resistance),
            "w_outcome_utility": float(self.w_outcome_utility),
        }
        if any(value < 0.0 for value in weights.values()):
            raise ConfigError("candidate learning weights must be non-negative")
        total = sum(weights.values())
        if total <= 0.0:
            raise ConfigError("candidate learning weights must sum to a positive value")
        if abs(total - 1.0) > 1e-9:
            warnings.warn(
                "CandidateLearningConfig weights do not sum to 1.0; auto-normalizing.",
                UserWarning,
                stacklevel=2,
            )
            for name, value in weights.items():
                object.__setattr__(self, name, value / total)
        if self.reconfirmation_target <= 0:
            raise ConfigError(
                "candidate learning reconfirmation_target must be positive"
            )
        if self.retrieval_hit_target <= 0:
            raise ConfigError(
                "candidate learning retrieval_hit_target must be positive"
            )
        if self.survival_halflife_days <= 0:
            raise ConfigError(
                "candidate learning survival_halflife_days must be positive"
            )
        if self.candidate_max_age_days <= 0:
            raise ConfigError(
                "candidate learning candidate_max_age_days must be positive"
            )

    @property
    def weights(self) -> Any:
        from openminion.modules.memory.runtime.candidate_readiness import (
            PromotionWeights,
        )

        return PromotionWeights(
            reconfirmation=self.w_reconfirmation,
            retrieval_hits=self.w_retrieval_hits,
            survival=self.w_survival,
            confidence=self.w_confidence,
            correction_resistance=self.w_correction_resistance,
            outcome_utility=self.w_outcome_utility,
        )


@dataclass(frozen=True)
class RetrievalConfig:
    max_results: int
    min_confidence_default: float
    pin_first: bool
    session_handoff_max_summaries: int
    feedback_boost_on_reference: float
    feedback_demote_on_correction: float
    type_boost_correction: float
    type_boost_user_preference: float
    type_boost_pin: float
    type_boost_project_convention: float


@dataclass(frozen=True)
class RetentionConfig:
    enable_soft_delete: bool
    gc_enabled: bool
    gc_batch_size: int
    session_summary_max_chars: int
    summary_compression_age_days: int
    max_records_per_scope: int
    confidence_decay_interval_days: int
    confidence_decay_rate: float
    min_confidence_eviction: float
    session_summary_checkpoint_message_interval: int = 2
    disuse_threshold_days: int = 30
    disuse_decay_multiplier: float = 2.0
    insight_staleness_days: int = 60
    summary_compression_max_chars: int = 100
    summary_delete_age_days: int = 90
    tiering_enabled: bool = True
    tiering_promotion_age_days: int = 30
    tiering_reaccess_promote_threshold: int = 3
    tiering_max_working_access_count: int = 1


@dataclass(frozen=True)
class ReflectionConfig:
    reflection_enabled: bool
    reflection_interval_sessions: int
    contradiction_similarity_threshold: float
    max_insights_per_reflection: int
    promotion_enabled: bool = True
    correction_promotion_min_count: int = 3
    correction_promotion_confidence: float = 0.85
    preference_stability_min_sessions: int = 5
    preference_stability_boost: float = 0.1
    max_correction_promotions_per_run: int = 2
    max_preference_boosts_per_run: int = 3
    reboost_cooldown_multiplier: float = 2.0
    contradiction_threshold_overrides: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class ConsolidationConfig:
    recent_rollout_limit: int = 256
    idle_seconds_before_eligible: int = 21600
    min_rate_limit_remaining_percent: int = 25
    consolidation_model: str | None = None

    def __post_init__(self) -> None:
        if self.recent_rollout_limit <= 0:
            raise ConfigError(
                "consolidation recent_rollout_limit must be a positive integer"
            )
        if self.idle_seconds_before_eligible < 0:
            raise ConfigError(
                "consolidation idle_seconds_before_eligible must be non-negative"
            )
        if not 0 <= self.min_rate_limit_remaining_percent <= 100:
            raise ConfigError(
                "consolidation min_rate_limit_remaining_percent must be within [0, 100]"
            )


@dataclass(frozen=True)
class MemctlConfig:
    version: int
    store: StoreConfig
    defaults: DefaultsConfig
    promotion: PromotionConfig
    retrieval: RetrievalConfig
    ranking: RankingConfig
    retention: RetentionConfig
    reflection: ReflectionConfig
    consolidation: ConsolidationConfig = field(default_factory=ConsolidationConfig)
    candidate_learning: CandidateLearningConfig = field(
        default_factory=CandidateLearningConfig
    )
    trace_file: Path | None = None
    path_mode: str = "module_standalone"
    path_source: str = "default_standalone"


def merge_ranking_config(
    ranking: RankingConfig | None,
    *,
    retrieval: RetrievalConfig | None = None,
    retrieve_defaults: Any | None = None,
) -> RankingConfig:
    base = ranking or RankingConfig()
    default_ranking = RankingConfig()

    if retrieval is not None:
        retrieval_type_boosts = {
            "type_boost_correction": object.__getattribute__(
                retrieval, "type_boost_correction"
            ),
            "type_boost_user_preference": object.__getattribute__(
                retrieval, "type_boost_user_preference"
            ),
            "type_boost_pin": object.__getattribute__(retrieval, "type_boost_pin"),
            "type_boost_project_convention": object.__getattribute__(
                retrieval, "type_boost_project_convention"
            ),
        }
        override_payload = {
            name: value
            for name, value in retrieval_type_boosts.items()
            if getattr(base, name) == getattr(default_ranking, name)
            and value != getattr(default_ranking, name)
        }
        if override_payload:
            base = replace(base, **override_payload)

    if retrieve_defaults is not None:
        default_defaults = {
            "decay_halflife_days": 30,
            "mmr_enabled": True,
            "mmr_lambda": 0.6,
            "feedback_decay_halflife_days": 60,
            "recency_half_life_hours": 72,
        }
        legacy_overrides: dict[str, Any] = {}
        translation_map = {
            "decay_halflife_days": "recency_half_life_days",
            "mmr_enabled": "mmr_enabled",
            "mmr_lambda": "mmr_lambda",
            "feedback_decay_halflife_days": "feedback_decay_halflife_days",
        }
        for legacy_key, ranking_key in translation_map.items():
            legacy_value = getattr(
                retrieve_defaults,
                legacy_key,
                default_defaults[legacy_key],
            )
            if legacy_value == default_defaults[legacy_key]:
                continue
            if getattr(base, ranking_key) != getattr(default_ranking, ranking_key):
                continue
            legacy_overrides[ranking_key] = legacy_value
            warnings.warn(
                f"Retrieve defaults field `{legacy_key}` is deprecated; use memory ranking config `{ranking_key}` instead.",
                DeprecationWarning,
                stacklevel=2,
            )
        hours_value = getattr(
            retrieve_defaults,
            "recency_half_life_hours",
            default_defaults["recency_half_life_hours"],
        )
        if (
            hours_value != default_defaults["recency_half_life_hours"]
            and base.recency_half_life_days == default_ranking.recency_half_life_days
            and "recency_half_life_days" not in legacy_overrides
        ):
            legacy_overrides["recency_half_life_days"] = float(hours_value) / 24.0
            warnings.warn(
                "Retrieve defaults field `recency_half_life_hours` is deprecated; use memory ranking config `recency_half_life_days` instead.",
                DeprecationWarning,
                stacklevel=2,
            )
        if legacy_overrides:
            base = replace(base, **legacy_overrides)

    return base


def merge_candidate_learning_config(
    candidate_learning: CandidateLearningConfig | None,
    *,
    promotion: PromotionConfig | None = None,
) -> CandidateLearningConfig:
    base = candidate_learning or CandidateLearningConfig()
    default_cfg = CandidateLearningConfig()
    if promotion is None:
        return base

    overrides: dict[str, Any] = {}
    legacy_fields = (
        ("auto_extract_enabled", promotion.auto_extract_enabled),
        ("auto_extract_notify", promotion.auto_extract_notify),
        ("survival_halflife_days", float(promotion.auto_extract_halflife_days)),
    )
    for field_name, legacy_value in legacy_fields:
        if getattr(base, field_name) != getattr(default_cfg, field_name):
            continue
        if legacy_value == getattr(default_cfg, field_name):
            continue
        overrides[field_name] = legacy_value
        source_name = (
            "auto_extract_halflife_days"
            if field_name == "survival_halflife_days"
            else field_name
        )
        warnings.warn(
            f"Memory promotion field `{source_name}` is deprecated; use candidate_learning.{field_name} instead.",
            DeprecationWarning,
            stacklevel=2,
        )
    if overrides:
        base = replace(base, **overrides)
    return base


def from_base_config(
    *,
    base_config: OpenMinionConfig,
    home_root: Path,
    data_root: Path,
) -> MemctlConfig:
    del base_config, home_root
    sqlite_path = (data_root / DEFAULT_INTEGRATED_SQLITE_SUBPATH).resolve(strict=False)
    confidence: dict[MemorySource, float] = {}
    for source in get_args(MemorySource):
        confidence[source] = DEFAULT_CONFIDENCE[source]
    return MemctlConfig(
        version=1,
        store=StoreConfig(
            backend="sqlite",
            sqlite_path=sqlite_path,
            sqlite=SQLiteRuntimeConfig(
                wal_mode=True,
                busy_timeout_ms=5000,
                fts5_enabled=True,
            ),
            remote_endpoint="",
            remote_timeout_seconds=5.0,
            remote_max_retries=1,
        ),
        defaults=DefaultsConfig(confidence=confidence),
        promotion=PromotionConfig(
            require_approval_for_global=True,
            auto_promote_agent_procedures=False,
            allowlisted_auto_rules=[],
            auto_extract_enabled=True,
            auto_extract_halflife_days=7,
            auto_extract_notify=True,
        ),
        retrieval=RetrievalConfig(
            max_results=20,
            min_confidence_default=0.6,
            pin_first=True,
            session_handoff_max_summaries=5,
            feedback_boost_on_reference=0.1,
            feedback_demote_on_correction=0.3,
            type_boost_correction=1.5,
            type_boost_user_preference=1.3,
            type_boost_pin=1.2,
            type_boost_project_convention=1.1,
        ),
        ranking=RankingConfig(),
        candidate_learning=CandidateLearningConfig(),
        retention=RetentionConfig(
            enable_soft_delete=True,
            gc_enabled=True,
            gc_batch_size=1000,
            session_summary_max_chars=500,
            session_summary_checkpoint_message_interval=2,
            summary_compression_age_days=14,
            max_records_per_scope=500,
            confidence_decay_interval_days=7,
            confidence_decay_rate=0.05,
            min_confidence_eviction=0.3,
            disuse_threshold_days=30,
            disuse_decay_multiplier=2.0,
            insight_staleness_days=60,
            summary_compression_max_chars=100,
            summary_delete_age_days=90,
            tiering_enabled=True,
            tiering_promotion_age_days=30,
            tiering_reaccess_promote_threshold=3,
            tiering_max_working_access_count=1,
        ),
        reflection=ReflectionConfig(
            reflection_enabled=True,
            reflection_interval_sessions=5,
            contradiction_similarity_threshold=0.8,
            max_insights_per_reflection=5,
            promotion_enabled=True,
            correction_promotion_min_count=3,
            correction_promotion_confidence=0.85,
            preference_stability_min_sessions=5,
            preference_stability_boost=0.1,
            max_correction_promotions_per_run=2,
            max_preference_boosts_per_run=3,
            reboost_cooldown_multiplier=2.0,
            contradiction_threshold_overrides={
                "correction": 0.6,
                "user_preference": 0.7,
                "project_convention": 0.7,
                "fact": 0.8,
                "pin": 0.9,
            },
        ),
        consolidation=ConsolidationConfig(),
        path_mode="integrated_runtime",
        path_source="default_integrated",
    )
