import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Callable

from openminion.base.time import utc_now_iso
from openminion.base.config.env import resolve_environment_config
from openminion.modules.memory.config import (
    CandidateLearningConfig,
    RankingConfig,
    merge_candidate_learning_config,
    merge_ranking_config,
)
from openminion.modules.memory.constants import OPENMINION_MEMORY_TRACE_ENV
from openminion.modules.memory.errors import (
    MemctlError,
    MemoryQueryUnavailableError,
    StoreReadError,
)
from openminion.modules.memory.interfaces import ListQueryOptions, SearchQueryOptions
from openminion.modules.memory.models import MemoryPatchResult
from openminion.modules.memory.diagnostics.operability import (
    configured_trace_file_path,
    serialize_for_json,
)
from openminion.modules.memory.diagnostics.export import export_memory_debug
from openminion.modules.memory.service.agent_gateway import (
    MEMORY_ANTONYMS_FILENAME,
    LearningMixin,
    RetrievalPipeline,
    SessionLifecycleMixin,
    TurnRecordingMixin,
    config_section,
    config_value,
    is_mock_like,
    coerce_bool,
    coerce_float,
    coerce_int,
    build_empty_meta,
    ensure_default_antonym_config,
)
from openminion.modules.memory.surfacing.agent_context import ContextBuildersMixin
from openminion.services.config import resolve_services_path, resolve_services_roots
from openminion.services.bootstrap.paths import (
    SERVICES_CONFIG_SUBDIR,
)


class MemoryServiceGatewayAdapter(
    SessionLifecycleMixin,
    LearningMixin,
    TurnRecordingMixin,
    ContextBuildersMixin,
):
    """Gateway-facing V2 memory adapter."""

    def __init__(
        self,
        service: Any,
        *,
        agent_id: str,
        project_id: str | None = None,
        session_context: Any | None = None,
        logger: logging.Logger | None = None,
        retrieval_max_chars: int = 2000,
        capsule_max_chars: int = 1600,
        log_retention_days: int = 30,
        max_facts: int = 200,
        max_todos: int = 200,
        session_summary_max_chars: int = 500,
        session_handoff_max_summaries: int = 5,
        trace_enabled: bool | None = None,
        memory_config: Any | None = None,
        retrieve_ctl: Any | None = None,
        ranking_config: RankingConfig | None = None,
        candidate_learning_config: CandidateLearningConfig | None = None,
        working_state_getter: Callable[[str], dict[str, Any] | None] | None = None,
        brain_sessions_db_path: str | Path | None = None,
        session_summary_structurer: Callable[[str, int], dict[str, Any] | None]
        | None = None,
        session_summary_structurer_timeout_seconds: float = 5.0,
    ) -> None:
        self._init_core_state(
            service=service,
            agent_id=agent_id,
            project_id=project_id,
            session_context=session_context,
            logger=logger,
            retrieval_max_chars=retrieval_max_chars,
            capsule_max_chars=capsule_max_chars,
            session_summary_max_chars=session_summary_max_chars,
            session_handoff_max_summaries=session_handoff_max_summaries,
            log_retention_days=log_retention_days,
            max_facts=max_facts,
            max_todos=max_todos,
            memory_config=memory_config,
            retrieve_ctl=retrieve_ctl,
            ranking_config=ranking_config,
            candidate_learning_config=candidate_learning_config,
            working_state_getter=working_state_getter,
            brain_sessions_db_path=brain_sessions_db_path,
            session_summary_structurer=session_summary_structurer,
            session_summary_structurer_timeout_seconds=session_summary_structurer_timeout_seconds,
        )
        self._init_trace_state(trace_enabled=trace_enabled)
        self._generation = 0
        self._pipeline = RetrievalPipeline(
            retrieve_ctl=self._retrieve_ctl,
            config=self._config,
            ranking_config=self._ranking_config,
            logger=self._logger,
            agent_id=self._agent_id,
            retrieval_max_chars=self._retrieval_max_chars,
            feedback_boost_on_reference=self._feedback_boost_on_reference,
            trace_fn=self._trace,
        )

        self._apply_candidate_learning_config()
        self._apply_retrieval_config()
        self._apply_retention_config()
        self._apply_reflection_config()
        self._apply_ranking_and_antonym_config()

        if self._session_context is not None and hasattr(
            self._session_context, "register_close_callback"
        ):
            try:
                self._session_context.register_close_callback(
                    self.write_session_summary
                )
            except Exception:
                pass

    def _apply_candidate_learning_config(self) -> None:
        if self._candidate_learning_config is None:
            self._candidate_learning_config = merge_candidate_learning_config(
                getattr(self._memory_config, "candidate_learning", None),
                promotion=getattr(self._memory_config, "promotion", None),
            )
        self._candidate_learning_readiness_enabled = (
            self._candidate_learning_config is not None
        )
        if self._candidate_learning_config is None:
            return
        self._auto_extract_enabled = bool(
            getattr(self._candidate_learning_config, "auto_extract_enabled", False)
        )
        self._auto_extract_halflife_days = coerce_int(
            getattr(self._candidate_learning_config, "survival_halflife_days", 14),
            14,
            minimum=1,
        )
        self._auto_extract_notify = bool(
            getattr(self._candidate_learning_config, "auto_extract_notify", True)
        )

    def _apply_retrieval_config(self) -> None:
        retrieval_cfg = config_section(self._memory_config, "retrieval")
        if retrieval_cfg is None:
            return
        self._retrieval_min_confidence = coerce_float(
            config_value(retrieval_cfg, "min_confidence_default", 0.6),
            0.6,
        )
        self._feedback_boost_on_reference = coerce_float(
            config_value(retrieval_cfg, "feedback_boost_on_reference", 0.1),
            0.1,
        )
        self._feedback_demote_on_correction = coerce_float(
            config_value(retrieval_cfg, "feedback_demote_on_correction", 0.3),
            0.3,
        )

    def _apply_retention_config(self) -> None:
        retention_cfg = config_section(self._memory_config, "retention")
        if retention_cfg is None:
            return
        self._session_summary_max_chars = coerce_int(
            config_value(
                retention_cfg,
                "session_summary_max_chars",
                self._session_summary_max_chars,
            ),
            self._session_summary_max_chars,
            minimum=64,
        )
        self._session_summary_checkpoint_message_interval = coerce_int(
            config_value(
                retention_cfg,
                "session_summary_checkpoint_message_interval",
                self._session_summary_checkpoint_message_interval,
            ),
            self._session_summary_checkpoint_message_interval,
            minimum=1,
        )
        self._summary_compression_age_days = coerce_int(
            config_value(retention_cfg, "summary_compression_age_days", 14),
            14,
            minimum=1,
        )
        self._summary_compression_max_chars = coerce_int(
            config_value(retention_cfg, "summary_compression_max_chars", 100),
            100,
            minimum=1,
        )

    def _apply_reflection_config(self) -> None:
        reflection_cfg = config_section(self._memory_config, "reflection")
        if reflection_cfg is None:
            return
        self._reflection_enabled = coerce_bool(
            config_value(reflection_cfg, "reflection_enabled", True), True
        )
        self._reflection_interval_sessions = coerce_int(
            config_value(reflection_cfg, "reflection_interval_sessions", 5),
            5,
            minimum=1,
        )
        self._contradiction_similarity_threshold = coerce_float(
            config_value(reflection_cfg, "contradiction_similarity_threshold", 0.8),
            0.8,
        )
        self._max_insights_per_reflection = coerce_int(
            config_value(reflection_cfg, "max_insights_per_reflection", 5), 5, minimum=1
        )
        self._promotion_enabled = coerce_bool(
            config_value(reflection_cfg, "promotion_enabled", True), True
        )
        self._correction_promotion_min_count = coerce_int(
            config_value(reflection_cfg, "correction_promotion_min_count", 3),
            3,
            minimum=1,
        )
        self._correction_promotion_confidence = coerce_float(
            config_value(reflection_cfg, "correction_promotion_confidence", 0.85), 0.85
        )
        self._preference_stability_min_sessions = coerce_int(
            config_value(reflection_cfg, "preference_stability_min_sessions", 5),
            5,
            minimum=1,
        )
        self._preference_stability_boost = coerce_float(
            config_value(reflection_cfg, "preference_stability_boost", 0.1), 0.1
        )
        self._max_correction_promotions_per_run = coerce_int(
            config_value(reflection_cfg, "max_correction_promotions_per_run", 2),
            2,
            minimum=1,
        )
        self._max_preference_boosts_per_run = coerce_int(
            config_value(reflection_cfg, "max_preference_boosts_per_run", 3),
            3,
            minimum=1,
        )
        self._reboost_cooldown_multiplier = coerce_float(
            config_value(reflection_cfg, "reboost_cooldown_multiplier", 2.0),
            2.0,
            minimum=0.1,
            maximum=365.0,
        )
        raw_threshold_overrides = config_value(
            reflection_cfg, "contradiction_threshold_overrides", {}
        )
        if isinstance(raw_threshold_overrides, dict):
            self._contradiction_threshold_overrides = {
                str(key): coerce_float(value, self._contradiction_similarity_threshold)
                for key, value in raw_threshold_overrides.items()
            }

    def _apply_ranking_and_antonym_config(self) -> None:
        if self._ranking_config is None:
            self._ranking_config = merge_ranking_config(
                getattr(self._memory_config, "ranking", None),
                retrieval=getattr(self._memory_config, "retrieval", None),
                retrieve_defaults=getattr(self._config, "defaults", None),
            )
        roots = resolve_services_roots(fallback_to_cwd=True)
        ensure_default_antonym_config(
            resolve_services_path(
                Path(SERVICES_CONFIG_SUBDIR) / MEMORY_ANTONYMS_FILENAME,
                roots=roots,
                relative_to="data_root",
            )
        )

    def _init_core_state(
        self,
        *,
        service: Any,
        agent_id: str,
        project_id: str | None,
        session_context: Any | None,
        logger: logging.Logger | None,
        retrieval_max_chars: int,
        capsule_max_chars: int,
        session_summary_max_chars: int,
        session_handoff_max_summaries: int,
        log_retention_days: int,
        max_facts: int,
        max_todos: int,
        memory_config: Any | None,
        retrieve_ctl: Any | None,
        ranking_config: RankingConfig | None,
        candidate_learning_config: CandidateLearningConfig | None,
        working_state_getter: Callable[[str], dict[str, Any] | None] | None,
        brain_sessions_db_path: str | Path | None,
        session_summary_structurer: Callable[[str, int], dict[str, Any] | None] | None,
        session_summary_structurer_timeout_seconds: float,
    ) -> None:
        self._service = service
        self._agent_id = str(agent_id or "").strip() or "openminion"
        self._project_id = str(project_id or "").strip() or None
        self._session_context = session_context
        self._logger = logger or logging.getLogger(__name__)
        self._retrieval_max_chars = max(256, int(retrieval_max_chars))
        self._capsule_max_chars = max(256, int(capsule_max_chars))
        self._session_summary_max_chars = max(64, int(session_summary_max_chars))
        self._session_handoff_max_summaries = max(1, int(session_handoff_max_summaries))
        self._session_summary_checkpoint_message_interval = 2
        self._session_summary_token_pressure_checkpoint_turns: dict[str, int] = {}
        self._brain_sessions_db_path = (
            Path(brain_sessions_db_path).expanduser().resolve(strict=False)
            if brain_sessions_db_path is not None
            else None
        )
        self._brain_session_store: Any | None = None
        self._working_state_getter = (
            working_state_getter
            if working_state_getter is not None
            else self._default_working_state_getter
        )
        self._summary_compression_age_days = 14
        self._summary_compression_max_chars = 100
        self._log_retention_days = max(1, int(log_retention_days))
        self._max_facts = max(1, int(max_facts))
        self._max_todos = max(1, int(max_todos))
        self._retrieve_ctl = retrieve_ctl
        raw_retrieve_config = getattr(retrieve_ctl, "config", None)
        self._config = (
            None if is_mock_like(raw_retrieve_config) else raw_retrieve_config
        )
        self._memory_config = None if is_mock_like(memory_config) else memory_config
        self._ranking_config = ranking_config
        self._candidate_learning_config = candidate_learning_config
        self._candidate_learning_readiness_enabled = False
        self._preamble_shown: dict[str, bool] = {}
        self._last_retrieved_items: dict[str, list[dict[str, Any]]] = {}
        self._init_learning_defaults()
        self._session_lifecycle_done: dict[str, bool] = {}
        self._last_decay_run_at: str | None = None
        self._session_summary_structurer = session_summary_structurer
        self._session_summary_structurer_timeout_seconds = max(
            0.0, float(session_summary_structurer_timeout_seconds or 0.0)
        )
        self._session_summary_structurer_disabled = False
        self._trace_file_handle = None

    def _init_learning_defaults(self) -> None:
        self._auto_extract_enabled = False
        self._auto_extract_halflife_days = 14
        self._auto_extract_notify = True
        self._feedback_boost_on_reference = 0.1
        self._feedback_demote_on_correction = 0.3
        self._reflection_enabled = True
        self._reflection_interval_sessions = 5
        self._contradiction_similarity_threshold = 0.8
        self._contradiction_threshold_overrides: dict[str, float] = {}
        self._max_insights_per_reflection = 5
        self._promotion_enabled = True
        self._correction_promotion_min_count = 3
        self._correction_promotion_confidence = 0.85
        self._preference_stability_min_sessions = 5
        self._preference_stability_boost = 0.1
        self._max_correction_promotions_per_run = 2
        self._max_preference_boosts_per_run = 3
        self._reboost_cooldown_multiplier = 2.0
        self._retrieval_min_confidence = 0.6

    def _init_trace_state(self, *, trace_enabled: bool | None) -> None:
        self._trace_file_path = configured_trace_file_path(
            memory_config=self._memory_config
        )
        if trace_enabled is None:
            trace_enabled = resolve_environment_config().get(
                OPENMINION_MEMORY_TRACE_ENV,
                "",
            ).strip().lower() in {"1", "true", "yes", "on"} or (
                self._trace_file_path is not None
            )
        self._trace_enabled = bool(trace_enabled)
        if not self._trace_enabled or self._trace_file_path is None:
            return
        try:
            self._trace_file_path.parent.mkdir(parents=True, exist_ok=True)
            self._trace_file_handle = self._trace_file_path.open("a", encoding="utf-8")
        except Exception as exc:
            self._logger.warning(
                "memory.trace_file_open_failed agent_id=%s path=%s error=%s",
                self._agent_id,
                self._trace_file_path,
                exc,
            )
            self._trace_file_handle = None

    def configure_session_summary_structurer(
        self,
        structurer: Callable[[str, int], dict[str, Any] | None] | None,
    ) -> None:
        self._session_summary_structurer = structurer
        self._session_summary_structurer_disabled = False

    @property
    def enabled(self) -> bool:
        return True

    def list_records(self, options: ListQueryOptions) -> list[Any]:
        try:
            return list(self._service.list(options))
        except MemctlError:
            raise
        except (RuntimeError, ValueError, TypeError, OSError) as exc:
            raise StoreReadError(f"memory list query failed: {exc}") from exc

    def search_records(self, options: SearchQueryOptions) -> list[Any]:
        try:
            return list(self._service.search(options))
        except MemctlError:
            raise
        except (RuntimeError, ValueError, TypeError, OSError) as exc:
            raise StoreReadError(f"memory search query failed: {exc}") from exc

    def derive_patch_id(
        self,
        *,
        session_id: str,
        run_id: str,
        request_id: str,
        user_message: str,
    ) -> str:
        payload = f"{session_id}|{run_id}|{request_id}|{user_message}"
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]

    def export_debug_snapshot(
        self,
        output_dir: Path,
        *,
        session_id: str,
    ) -> Path:
        return export_memory_debug(self, output_dir, session_id=session_id)

    def _trace(self, event_type: str, payload: dict[str, Any]) -> None:
        if not self._trace_enabled:
            return
        try:
            ts = utc_now_iso()
            logging.getLogger("openminion").warning(
                "OPENMINION_MEMORY_TRACE event=%s agent_id=%s ts=%s payload=%s",
                event_type,
                self._agent_id,
                ts,
                payload,
            )
            if self._trace_file_handle is not None:
                event = {
                    "event": str(event_type),
                    "agent_id": str(self._agent_id),
                    "ts": str(ts),
                    **{
                        str(key): serialize_for_json(value)
                        for key, value in payload.items()
                    },
                }
                self._trace_file_handle.write(json.dumps(event, default=str) + "\n")
                self._trace_file_handle.flush()
        except Exception:
            pass

    def _close_trace_file(self) -> None:
        handle = getattr(self, "_trace_file_handle", None)
        if handle is None:
            close_handle = False
        else:
            close_handle = True
        if close_handle:
            try:
                handle.close()
            except Exception:
                pass
        self._trace_file_handle = None
        store = getattr(self, "_brain_session_store", None)
        close_store = getattr(store, "close", None)
        if callable(close_store):
            try:
                close_store()
            except Exception:
                pass
        self._brain_session_store = None

    def _default_working_state_getter(self, session_id: str) -> dict[str, Any] | None:
        if self._brain_sessions_db_path is None:
            return None
        if self._brain_session_store is None:
            try:
                from openminion.modules.session.storage.store import SQLiteSessionStore

                self._brain_session_store = SQLiteSessionStore(
                    self._brain_sessions_db_path
                )
            except Exception:
                return None
        get_latest = getattr(
            self._brain_session_store, "get_latest_working_state", None
        )
        if not callable(get_latest):
            return None
        try:
            return get_latest(session_id)
        except Exception:
            return None

    def __del__(self) -> None:
        self._close_trace_file()


class DisabledMemoryGatewayAdapter:
    """V2 no-op adapter for disabled memory mode."""

    def __init__(self, *, agent_id: str, logger: logging.Logger | None = None) -> None:
        self._agent_id = str(agent_id or "").strip() or "openminion"
        self._logger = logger or logging.getLogger(__name__)

    @property
    def enabled(self) -> bool:
        return False

    def list_records(self, options: ListQueryOptions) -> list[Any]:
        del options
        raise MemoryQueryUnavailableError("durable memory queries are disabled")

    def search_records(self, options: SearchQueryOptions) -> list[Any]:
        del options
        raise MemoryQueryUnavailableError("durable memory queries are disabled")

    def derive_patch_id(
        self, *, session_id: str, run_id: str, request_id: str, user_message: str
    ) -> str:
        return ""

    def record_turn(
        self,
        *,
        session_id: str,
        run_id: str,
        request_id: str,
        channel: str,
        target: str,
        user_message: str,
        assistant_message: str,
    ) -> MemoryPatchResult:
        return MemoryPatchResult(facts_added=0, todos_added=0, todos_completed=0)

    def build_context(self, *, session_id: str, user_message: str) -> str:
        return ""

    def build_context_with_metadata(
        self, *, session_id: str, user_message: str
    ) -> tuple[str, dict[str, str]]:
        return "", build_empty_meta("capsule", 0)

    def build_retrieval_context(
        self, *, session_id: str, user_message: str, max_chars: int | None = None
    ) -> str:
        return ""

    def build_retrieval_context_with_metadata(
        self, *, session_id: str, user_message: str, max_chars: int | None = None
    ) -> tuple[str, dict[str, str]]:
        return "", build_empty_meta("retrieval", 0)


__all__ = [
    "DisabledMemoryGatewayAdapter",
    "MemoryServiceGatewayAdapter",
]
