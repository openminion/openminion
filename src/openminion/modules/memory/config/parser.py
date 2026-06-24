# mypy: disable-error-code="attr-defined,import-untyped"

import os
from pathlib import Path
from string import Template
from typing import Any, Mapping, MutableMapping, get_args

import yaml

from openminion.base.config.paths import ensure_under_data_root
from openminion.modules.config import (
    resolve_module_config_path,
    resolve_module_data_root,
    resolve_module_home_root,
)

from .models import (
    CandidateLearningConfig,
    ConfigError,
    ConsolidationConfig,
    DefaultsConfig,
    MemctlConfig,
    PromotionConfig,
    RankingConfig,
    ReflectionConfig,
    RetentionConfig,
    RetrievalConfig,
    SQLiteRuntimeConfig,
    StoreConfig,
    merge_candidate_learning_config,
)
from ..constants import DEFAULT_CONFIG_FILENAME, DEFAULT_CONFIDENCE, MEMCTL_CONFIG_ENV
from ..models import MemorySource


def load_config(
    path: str | Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
    home_root: Path | None = None,
) -> MemctlConfig:
    env = dict(env or os.environ)
    path_source = "default_standalone"
    path_mode = "module_standalone"

    resolved_home_root = resolve_module_home_root(home_root, env)
    resolved_data_root = None
    if resolved_home_root is not None:
        path_mode = "integrated_runtime"
        path_source = "home_root" if home_root else "env_var"
        resolved_data_root = resolve_module_data_root(
            home_root=resolved_home_root,
            env=env,
        )
    elif path:
        path_source = "explicit_config"

    resolved_path = _resolve_config_path(path, env, home_root=resolved_home_root)
    if not resolved_path.exists():
        raise FileNotFoundError(f"config file not found: {resolved_path}")

    payload = _load_yaml(resolved_path)
    version = payload.get("version")
    if not isinstance(version, int):
        raise ConfigError("`version` must be an integer")

    memctl_section = _require_mapping(payload.get("memctl"), "memctl")
    store = _parse_store(
        memctl_section.get("store"),
        env,
        home_root=home_root,
        data_root=resolved_data_root,
    )
    defaults = _parse_defaults(memctl_section.get("defaults"))
    promotion = _parse_promotion(memctl_section.get("promotion"))
    retrieval = _parse_retrieval(memctl_section.get("retrieval"))
    ranking = _parse_ranking(memctl_section.get("ranking"), retrieval=retrieval)
    candidate_learning = merge_candidate_learning_config(
        _parse_candidate_learning(memctl_section.get("candidate_learning"))
        if "candidate_learning" in memctl_section
        else None,
        promotion=promotion,
    )
    retention = _parse_retention(memctl_section.get("retention"))
    reflection = _parse_reflection(memctl_section.get("reflection"))
    consolidation = _parse_consolidation(memctl_section.get("consolidation"))

    return MemctlConfig(
        version=version,
        store=store,
        defaults=defaults,
        promotion=promotion,
        retrieval=retrieval,
        ranking=ranking,
        candidate_learning=candidate_learning,
        retention=retention,
        reflection=reflection,
        consolidation=consolidation,
        trace_file=_parse_optional_path(
            memctl_section.get("trace_file"),
            env=env,
            home_root=resolved_home_root,
            data_root=resolved_data_root,
        ),
        path_mode=path_mode,
        path_source=path_source,
    )


def _resolve_config_path(
    path: str | Path | None, env: Mapping[str, str], home_root: Path | None = None
) -> Path:
    candidate = path or env.get(MEMCTL_CONFIG_ENV) or DEFAULT_CONFIG_FILENAME
    expanded = _expand_env(str(candidate), env)
    path_obj = Path(expanded)
    if path_obj.is_absolute():
        return resolve_module_config_path(path_obj)
    if home_root:
        return resolve_module_config_path(path_obj, home_root=home_root)
    return Path.home() / path_obj.expanduser()


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, MutableMapping):
        raise ConfigError("config root must be a mapping")
    return dict(data)


def _require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, MutableMapping):
        raise ConfigError(f"`{label}` section must be a mapping")
    return dict(value)


def _resolve_runtime_path(
    raw_path: str,
    *,
    env: Mapping[str, str],
    home_root: Path | None = None,
    data_root: Path | None = None,
    data_root_label: str | None = None,
) -> Path:
    expanded_path = Path(_expand_env(raw_path, env)).expanduser()
    if expanded_path.is_absolute():
        resolved = expanded_path.resolve()
    elif data_root is not None:
        resolved = (data_root / expanded_path).resolve()
    elif home_root:
        resolved = (home_root / expanded_path).resolve()
    else:
        resolved = (Path.home() / expanded_path).resolve()
    if data_root_label and data_root is not None and not expanded_path.is_absolute():
        return ensure_under_data_root(resolved, data_root, label=data_root_label)
    return resolved


def _parse_store(
    value: Any,
    env: Mapping[str, str],
    home_root: Path | None = None,
    data_root: Path | None = None,
) -> StoreConfig:
    data = _require_mapping(value, "store")
    backend = data.get("backend")
    if backend not in {"sqlite", "custom", "mock", "remote", "postgres"}:
        raise ConfigError(
            "store.backend must be 'sqlite', 'mock', 'remote', 'postgres', or 'custom'"
        )
    sqlite_section = data.get("sqlite")
    sqlite_path: Path | None = None
    postgres_url = ""
    remote_endpoint = ""
    remote_timeout_seconds = 5.0
    remote_max_retries = 1
    sqlite_section = _require_mapping(sqlite_section or {}, "store.sqlite")
    if backend == "sqlite":
        raw_path = data.get("sqlite_path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ConfigError("store.sqlite_path is required for sqlite backend")
        sqlite_path = _resolve_runtime_path(
            raw_path,
            env=env,
            home_root=home_root,
            data_root=data_root,
            data_root_label="memory_sqlite_path",
        )
    elif backend == "postgres":
        postgres_section = _require_mapping(
            data.get("postgres") or {}, "store.postgres"
        )
        postgres_url = str(
            postgres_section.get("url") or data.get("postgres_url") or ""
        ).strip()
        if not postgres_url:
            raise ConfigError("store.postgres.url is required for postgres backend")
    elif backend == "remote":
        remote_section = _require_mapping(data.get("remote") or {}, "store.remote")
        remote_endpoint = str(
            remote_section.get("endpoint") or data.get("remote_endpoint") or ""
        ).strip()
        if not remote_endpoint:
            raise ConfigError("store.remote.endpoint is required for remote backend")
        try:
            remote_timeout_seconds = max(
                0.1,
                float(
                    remote_section.get(
                        "timeout_seconds", data.get("remote_timeout_seconds", 5.0)
                    )
                ),
            )
        except (TypeError, ValueError) as exc:
            raise ConfigError("store.remote.timeout_seconds must be numeric") from exc
        try:
            remote_max_retries = max(
                0,
                int(
                    remote_section.get("max_retries", data.get("remote_max_retries", 1))
                ),
            )
        except (TypeError, ValueError) as exc:
            raise ConfigError(
                "store.remote.max_retries must be an integer >= 0"
            ) from exc

    sqlite_cfg = SQLiteRuntimeConfig(
        wal_mode=bool(sqlite_section.get("wal_mode", True)),
        busy_timeout_ms=int(sqlite_section.get("busy_timeout_ms", 5000)),
        fts5_enabled=bool(sqlite_section.get("fts5_enabled", True)),
    )

    return StoreConfig(
        backend=backend,
        sqlite_path=sqlite_path,
        sqlite=sqlite_cfg,
        postgres_url=postgres_url,
        remote_endpoint=remote_endpoint,
        remote_timeout_seconds=remote_timeout_seconds,
        remote_max_retries=remote_max_retries,
    )


def _parse_optional_path(
    value: Any,
    *,
    env: Mapping[str, str],
    home_root: Path | None = None,
    data_root: Path | None = None,
) -> Path | None:
    raw_path = str(value or "").strip()
    if not raw_path:
        return None
    return _resolve_runtime_path(
        raw_path,
        env=env,
        home_root=home_root,
        data_root=data_root,
        data_root_label="memory_optional_path",
    )


def _parse_defaults(value: Any) -> DefaultsConfig:
    data = _require_mapping(value, "defaults")
    confidence_section = _require_mapping(data.get("confidence"), "defaults.confidence")
    confidence: dict[MemorySource, float] = {}
    for source in get_args(MemorySource):
        raw_val = confidence_section.get(source, DEFAULT_CONFIDENCE[source])
        confidence[source] = _coerce_confidence(
            raw_val, f"defaults.confidence.{source}"
        )
    return DefaultsConfig(confidence=confidence)


def _parse_promotion(value: Any) -> PromotionConfig:
    data = _require_mapping(value, "promotion")
    rules = data.get("allowlisted_auto_rules", [])
    if not isinstance(rules, list):
        raise ConfigError("promotion.allowlisted_auto_rules must be a list")
    auto_extract_halflife_days = int(data.get("auto_extract_halflife_days", 7))
    if auto_extract_halflife_days <= 0:
        raise ConfigError("promotion.auto_extract_halflife_days must be positive")
    return PromotionConfig(
        require_approval_for_global=bool(data.get("require_approval_for_global", True)),
        auto_promote_agent_procedures=bool(
            data.get("auto_promote_agent_procedures", False)
        ),
        allowlisted_auto_rules=list(rules),
        auto_extract_enabled=bool(data.get("auto_extract_enabled", True)),
        auto_extract_halflife_days=auto_extract_halflife_days,
        auto_extract_notify=bool(data.get("auto_extract_notify", True)),
    )


def _parse_retrieval(value: Any) -> RetrievalConfig:
    data = _require_mapping(value, "retrieval")
    max_results = int(data.get("max_results", 20))
    if max_results <= 0:
        raise ConfigError("retrieval.max_results must be positive")
    session_handoff_max_summaries = int(data.get("session_handoff_max_summaries", 5))
    if session_handoff_max_summaries <= 0:
        raise ConfigError("retrieval.session_handoff_max_summaries must be positive")
    min_conf = _coerce_confidence(
        data.get("min_confidence_default", 0.6), "retrieval.min_confidence_default"
    )
    feedback_boost_on_reference = _coerce_confidence(
        data.get("feedback_boost_on_reference", 0.1),
        "retrieval.feedback_boost_on_reference",
    )
    feedback_demote_on_correction = _coerce_confidence(
        data.get("feedback_demote_on_correction", 0.3),
        "retrieval.feedback_demote_on_correction",
    )
    type_boost_correction = _coerce_positive_float(
        data.get("type_boost_correction", 1.5),
        "retrieval.type_boost_correction",
    )
    type_boost_user_preference = _coerce_positive_float(
        data.get("type_boost_user_preference", 1.3),
        "retrieval.type_boost_user_preference",
    )
    type_boost_pin = _coerce_positive_float(
        data.get("type_boost_pin", 1.2),
        "retrieval.type_boost_pin",
    )
    type_boost_project_convention = _coerce_positive_float(
        data.get("type_boost_project_convention", 1.1),
        "retrieval.type_boost_project_convention",
    )
    return RetrievalConfig(
        max_results=max_results,
        min_confidence_default=min_conf,
        pin_first=bool(data.get("pin_first", True)),
        session_handoff_max_summaries=session_handoff_max_summaries,
        feedback_boost_on_reference=feedback_boost_on_reference,
        feedback_demote_on_correction=feedback_demote_on_correction,
        type_boost_correction=type_boost_correction,
        type_boost_user_preference=type_boost_user_preference,
        type_boost_pin=type_boost_pin,
        type_boost_project_convention=type_boost_project_convention,
    )


def _parse_ranking(value: Any, *, retrieval: RetrievalConfig) -> RankingConfig:
    data = dict(value or {}) if isinstance(value, MutableMapping) else {}
    retrieval_type_boost_correction = object.__getattribute__(
        retrieval, "type_boost_correction"
    )
    retrieval_type_boost_user_preference = object.__getattribute__(
        retrieval, "type_boost_user_preference"
    )
    retrieval_type_boost_pin = object.__getattribute__(retrieval, "type_boost_pin")
    retrieval_type_boost_project_convention = object.__getattribute__(
        retrieval, "type_boost_project_convention"
    )
    return RankingConfig(
        w_relevance=_coerce_non_negative_float(
            data.get("w_relevance", 0.45), "ranking.w_relevance"
        ),
        w_recency=_coerce_non_negative_float(
            data.get("w_recency", 0.12), "ranking.w_recency"
        ),
        w_feedback=_coerce_non_negative_float(
            data.get("w_feedback", 0.08), "ranking.w_feedback"
        ),
        w_type_bonus=_coerce_non_negative_float(
            data.get("w_type_bonus", 0.13), "ranking.w_type_bonus"
        ),
        w_confidence=_coerce_non_negative_float(
            data.get("w_confidence", 0.07), "ranking.w_confidence"
        ),
        w_outcome_utility=_coerce_non_negative_float(
            data.get("w_outcome_utility", 0.15), "ranking.w_outcome_utility"
        ),
        recency_half_life_days=_coerce_positive_float(
            data.get("recency_half_life_days", 30.0), "ranking.recency_half_life_days"
        ),
        feedback_hit_divisor=_coerce_positive_float(
            data.get("feedback_hit_divisor", 10.0), "ranking.feedback_hit_divisor"
        ),
        feedback_max=_coerce_confidence(
            data.get("feedback_max", 1.0), "ranking.feedback_max"
        ),
        type_boost_correction=_coerce_positive_float(
            data.get("type_boost_correction", retrieval_type_boost_correction),
            "ranking.type_boost_correction",
        ),
        type_boost_user_preference=_coerce_positive_float(
            data.get(
                "type_boost_user_preference", retrieval_type_boost_user_preference
            ),
            "ranking.type_boost_user_preference",
        ),
        type_boost_pin=_coerce_positive_float(
            data.get("type_boost_pin", retrieval_type_boost_pin),
            "ranking.type_boost_pin",
        ),
        type_boost_project_convention=_coerce_positive_float(
            data.get(
                "type_boost_project_convention", retrieval_type_boost_project_convention
            ),
            "ranking.type_boost_project_convention",
        ),
        type_boost_meta_insight=_coerce_positive_float(
            data.get("type_boost_meta_insight", 1.05),
            "ranking.type_boost_meta_insight",
        ),
        feedback_decay_halflife_days=_coerce_positive_float(
            data.get("feedback_decay_halflife_days", 60.0),
            "ranking.feedback_decay_halflife_days",
        ),
        semantic_bm25_weight=_coerce_confidence(
            data.get("semantic_bm25_weight", 0.5),
            "ranking.semantic_bm25_weight",
        ),
        mmr_enabled=bool(data.get("mmr_enabled", True)),
        mmr_lambda=_coerce_confidence(
            data.get("mmr_lambda", 0.6), "ranking.mmr_lambda"
        ),
    )


def _parse_retention(value: Any) -> RetentionConfig:
    data = _require_mapping(value, "retention")
    gc_batch = int(data.get("gc_batch_size", 1000))
    if gc_batch <= 0:
        raise ConfigError("retention.gc_batch_size must be positive")
    session_summary_max_chars = int(data.get("session_summary_max_chars", 500))
    if session_summary_max_chars <= 0:
        raise ConfigError("retention.session_summary_max_chars must be positive")
    session_summary_checkpoint_message_interval = int(
        data.get("session_summary_checkpoint_message_interval", 2)
    )
    if session_summary_checkpoint_message_interval <= 0:
        raise ConfigError(
            "retention.session_summary_checkpoint_message_interval must be positive"
        )
    summary_compression_age_days = int(data.get("summary_compression_age_days", 14))
    if summary_compression_age_days <= 0:
        raise ConfigError("retention.summary_compression_age_days must be positive")
    max_records_per_scope = int(data.get("max_records_per_scope", 500))
    if max_records_per_scope <= 0:
        raise ConfigError("retention.max_records_per_scope must be positive")
    confidence_decay_interval_days = int(data.get("confidence_decay_interval_days", 7))
    if confidence_decay_interval_days <= 0:
        raise ConfigError("retention.confidence_decay_interval_days must be positive")
    confidence_decay_rate = _coerce_confidence(
        data.get("confidence_decay_rate", 0.05),
        "retention.confidence_decay_rate",
    )
    min_confidence_eviction = _coerce_confidence(
        data.get("min_confidence_eviction", 0.3),
        "retention.min_confidence_eviction",
    )
    disuse_threshold_days = int(data.get("disuse_threshold_days", 30))
    if disuse_threshold_days <= 0:
        raise ConfigError("retention.disuse_threshold_days must be positive")
    disuse_decay_multiplier = _coerce_positive_float(
        data.get("disuse_decay_multiplier", 2.0),
        "retention.disuse_decay_multiplier",
    )
    insight_staleness_days = int(data.get("insight_staleness_days", 60))
    if insight_staleness_days <= 0:
        raise ConfigError("retention.insight_staleness_days must be positive")
    summary_compression_max_chars = int(data.get("summary_compression_max_chars", 100))
    if summary_compression_max_chars <= 0:
        raise ConfigError("retention.summary_compression_max_chars must be positive")
    summary_delete_age_days = int(data.get("summary_delete_age_days", 90))
    if summary_delete_age_days <= 0:
        raise ConfigError("retention.summary_delete_age_days must be positive")
    tiering_promotion_age_days = int(data.get("tiering_promotion_age_days", 30))
    if tiering_promotion_age_days <= 0:
        raise ConfigError("retention.tiering_promotion_age_days must be positive")
    tiering_reaccess_promote_threshold = int(
        data.get("tiering_reaccess_promote_threshold", 3)
    )
    if tiering_reaccess_promote_threshold <= 0:
        raise ConfigError(
            "retention.tiering_reaccess_promote_threshold must be positive"
        )
    tiering_max_working_access_count = int(
        data.get("tiering_max_working_access_count", 1)
    )
    if tiering_max_working_access_count < 0:
        raise ConfigError(
            "retention.tiering_max_working_access_count must be non-negative"
        )
    return RetentionConfig(
        enable_soft_delete=bool(data.get("enable_soft_delete", True)),
        gc_enabled=bool(data.get("gc_enabled", True)),
        gc_batch_size=gc_batch,
        session_summary_max_chars=session_summary_max_chars,
        session_summary_checkpoint_message_interval=session_summary_checkpoint_message_interval,
        summary_compression_age_days=summary_compression_age_days,
        max_records_per_scope=max_records_per_scope,
        confidence_decay_interval_days=confidence_decay_interval_days,
        confidence_decay_rate=confidence_decay_rate,
        min_confidence_eviction=min_confidence_eviction,
        disuse_threshold_days=disuse_threshold_days,
        disuse_decay_multiplier=disuse_decay_multiplier,
        insight_staleness_days=insight_staleness_days,
        summary_compression_max_chars=summary_compression_max_chars,
        summary_delete_age_days=summary_delete_age_days,
        tiering_enabled=bool(data.get("tiering_enabled", True)),
        tiering_promotion_age_days=tiering_promotion_age_days,
        tiering_reaccess_promote_threshold=tiering_reaccess_promote_threshold,
        tiering_max_working_access_count=tiering_max_working_access_count,
    )


def _parse_candidate_learning(value: Any) -> CandidateLearningConfig:
    data = dict(value or {}) if isinstance(value, MutableMapping) else {}
    return CandidateLearningConfig(
        auto_extract_enabled=bool(data.get("auto_extract_enabled", False)),
        auto_extract_notify=bool(data.get("auto_extract_notify", True)),
        w_reconfirmation=_coerce_non_negative_float(
            data.get("w_reconfirmation", 0.25), "candidate_learning.w_reconfirmation"
        ),
        w_retrieval_hits=_coerce_non_negative_float(
            data.get("w_retrieval_hits", 0.20), "candidate_learning.w_retrieval_hits"
        ),
        w_survival=_coerce_non_negative_float(
            data.get("w_survival", 0.10), "candidate_learning.w_survival"
        ),
        w_confidence=_coerce_non_negative_float(
            data.get("w_confidence", 0.10), "candidate_learning.w_confidence"
        ),
        w_correction_resistance=_coerce_non_negative_float(
            data.get("w_correction_resistance", 0.15),
            "candidate_learning.w_correction_resistance",
        ),
        w_outcome_utility=_coerce_non_negative_float(
            data.get("w_outcome_utility", 0.20), "candidate_learning.w_outcome_utility"
        ),
        promotion_readiness_threshold=_coerce_confidence(
            data.get("promotion_readiness_threshold", 0.6),
            "candidate_learning.promotion_readiness_threshold",
        ),
        min_trust_for_promotion=_coerce_confidence(
            data.get("min_trust_for_promotion", 0.5),
            "candidate_learning.min_trust_for_promotion",
        ),
        reconfirmation_target=int(data.get("reconfirmation_target", 2)),
        retrieval_hit_target=int(data.get("retrieval_hit_target", 3)),
        survival_halflife_days=_coerce_positive_float(
            data.get("survival_halflife_days", 7.0),
            "candidate_learning.survival_halflife_days",
        ),
        candidate_max_age_days=int(data.get("candidate_max_age_days", 30)),
        confidence_boost_per_reconfirmation=_coerce_confidence(
            data.get("confidence_boost_per_reconfirmation", 0.1),
            "candidate_learning.confidence_boost_per_reconfirmation",
        ),
        confidence_max=_coerce_confidence(
            data.get("confidence_max", 0.9), "candidate_learning.confidence_max"
        ),
        confidence_initial_auto_extract=_coerce_confidence(
            data.get("confidence_initial_auto_extract", 0.4),
            "candidate_learning.confidence_initial_auto_extract",
        ),
    )


def _parse_reflection(value: Any) -> ReflectionConfig:
    data = _require_mapping(value or {}, "reflection")
    reflection_interval_sessions = int(data.get("reflection_interval_sessions", 5))
    if reflection_interval_sessions <= 0:
        raise ConfigError("reflection.reflection_interval_sessions must be positive")
    contradiction_similarity_threshold = _coerce_confidence(
        data.get("contradiction_similarity_threshold", 0.8),
        "reflection.contradiction_similarity_threshold",
    )
    max_insights_per_reflection = int(data.get("max_insights_per_reflection", 5))
    if max_insights_per_reflection <= 0:
        raise ConfigError("reflection.max_insights_per_reflection must be positive")
    correction_promotion_min_count = int(data.get("correction_promotion_min_count", 3))
    if correction_promotion_min_count <= 0:
        raise ConfigError("reflection.correction_promotion_min_count must be positive")
    preference_stability_min_sessions = int(
        data.get("preference_stability_min_sessions", 5)
    )
    if preference_stability_min_sessions <= 0:
        raise ConfigError(
            "reflection.preference_stability_min_sessions must be positive"
        )
    max_correction_promotions_per_run = int(
        data.get("max_correction_promotions_per_run", 2)
    )
    if max_correction_promotions_per_run <= 0:
        raise ConfigError(
            "reflection.max_correction_promotions_per_run must be positive"
        )
    max_preference_boosts_per_run = int(data.get("max_preference_boosts_per_run", 3))
    if max_preference_boosts_per_run <= 0:
        raise ConfigError("reflection.max_preference_boosts_per_run must be positive")
    overrides_raw = data.get("contradiction_threshold_overrides") or {}
    if not isinstance(overrides_raw, MutableMapping):
        raise ConfigError(
            "reflection.contradiction_threshold_overrides must be a mapping"
        )
    contradiction_threshold_overrides = {
        "correction": 0.6,
        "user_preference": 0.7,
        "project_convention": 0.7,
        "fact": 0.8,
        "pin": 0.9,
    }
    for key, value in dict(overrides_raw).items():
        contradiction_threshold_overrides[str(key)] = _coerce_confidence(
            value,
            f"reflection.contradiction_threshold_overrides.{key}",
        )
    return ReflectionConfig(
        reflection_enabled=bool(data.get("reflection_enabled", True)),
        reflection_interval_sessions=reflection_interval_sessions,
        contradiction_similarity_threshold=contradiction_similarity_threshold,
        max_insights_per_reflection=max_insights_per_reflection,
        promotion_enabled=bool(data.get("promotion_enabled", True)),
        correction_promotion_min_count=correction_promotion_min_count,
        correction_promotion_confidence=_coerce_confidence(
            data.get("correction_promotion_confidence", 0.85),
            "reflection.correction_promotion_confidence",
        ),
        preference_stability_min_sessions=preference_stability_min_sessions,
        preference_stability_boost=_coerce_confidence(
            data.get("preference_stability_boost", 0.1),
            "reflection.preference_stability_boost",
        ),
        max_correction_promotions_per_run=max_correction_promotions_per_run,
        max_preference_boosts_per_run=max_preference_boosts_per_run,
        reboost_cooldown_multiplier=_coerce_positive_float(
            data.get("reboost_cooldown_multiplier", 2.0),
            "reflection.reboost_cooldown_multiplier",
        ),
        contradiction_threshold_overrides=contradiction_threshold_overrides,
    )


def _parse_consolidation(value: Any) -> ConsolidationConfig:
    data = _require_mapping(value or {}, "consolidation")
    model = data.get("consolidation_model")
    normalized_model = None if model is None else (str(model).strip() or None)
    try:
        return ConsolidationConfig(
            recent_rollout_limit=int(data.get("recent_rollout_limit", 256)),
            idle_seconds_before_eligible=int(
                data.get("idle_seconds_before_eligible", 21600)
            ),
            min_rate_limit_remaining_percent=int(
                data.get("min_rate_limit_remaining_percent", 25)
            ),
            consolidation_model=normalized_model,
        )
    except ValueError as exc:
        raise ConfigError(f"invalid consolidation config: {exc}") from exc


def _coerce_confidence(value: Any, label: str) -> float:
    try:
        num = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{label} must be a float between 0 and 1") from exc
    if not (0.0 <= num <= 1.0):
        raise ConfigError(f"{label} must be between 0 and 1 inclusive")
    return num


def _coerce_non_negative_float(value: Any, label: str) -> float:
    try:
        num = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{label} must be a non-negative float") from exc
    if num < 0.0:
        raise ConfigError(f"{label} must be non-negative")
    return num


def _coerce_positive_float(value: Any, label: str) -> float:
    try:
        num = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{label} must be a positive float") from exc
    if num <= 0.0:
        raise ConfigError(f"{label} must be greater than 0")
    return num


def _expand_env(value: str, env: Mapping[str, str]) -> str:
    return Template(value).safe_substitute(env)
