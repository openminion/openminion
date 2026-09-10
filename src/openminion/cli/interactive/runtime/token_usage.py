import time
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Callable, Mapping

from openminion.cli.status import (
    TokenUsageSnapshot,
    TokenUsageTotals,
    accumulate_usage,
    build_token_usage_snapshot,
    usage_totals_from_mapping,
)

_LIVE_USAGE_THROTTLE_SECONDS = 0.5


class RuntimeTokenUsageMixin:
    _channel: str
    _completed_session_usage: TokenUsageTotals
    _current_turn_has_live_deltas: bool
    _current_turn_started_at_monotonic: float | None
    _current_turn_usage: TokenUsageTotals | None
    _history_limit: int
    _last_chat_phase_timing_payload: dict[str, object] | None
    _last_live_usage_update_at: float | None
    _last_turn_elapsed_seconds: float | None
    _last_turn_usage: TokenUsageTotals
    _rt: Any
    _target: str
    _usage_updated_at_monotonic: float | None

    @property
    def turn_usage_display(self) -> str:
        return str(self._rt.resolve_agent_profile(self.agent_id).turn_usage_display)

    if TYPE_CHECKING:

        @property
        def agent_id(self) -> str: ...

        @property
        def is_bound(self) -> bool: ...

        @property
        def session_id(self) -> str: ...

        def _turn_session_id(self) -> str: ...

    def token_usage_snapshot(self) -> TokenUsageSnapshot:
        turn_usage = self._current_turn_usage or self._last_turn_usage
        session_usage = self._completed_session_usage
        if self._current_turn_usage is not None:
            session_usage = (
                accumulate_usage(session_usage, self._current_turn_usage)
                or TokenUsageTotals()
            )
        turn_elapsed_seconds = self._last_turn_elapsed_seconds
        if self._current_turn_started_at_monotonic is not None:
            turn_elapsed_seconds = max(
                0.0,
                time.monotonic() - self._current_turn_started_at_monotonic,
            )
        context_limit = self._context_limit_tokens()
        context_used = getattr(session_usage, "total_tokens", None)
        if context_limit is None:
            context_used = None
        return build_token_usage_snapshot(
            turn=turn_usage,
            session=session_usage,
            context_used_tokens=context_used,
            context_limit_tokens=context_limit,
            has_live_deltas=self._current_turn_has_live_deltas,
            turn_elapsed_seconds=turn_elapsed_seconds,
            updated_at_monotonic=self._usage_updated_at_monotonic,
        )

    def token_usage_report(self, *, recent: int | None = None) -> str:
        if not self.is_bound:
            return "No active session."
        from openminion.cli.presentation.tokens import (
            format_interactive_token_history,
            format_interactive_token_summary,
        )
        from openminion.modules.telemetry.usage import StatsService

        store, owns_store = self._token_usage_store()
        try:
            service = StatsService(store)
            if recent is None:
                return format_interactive_token_summary(
                    service.get_session_token_usage(self._turn_session_id())
                )
            summaries = tuple(
                replace(
                    service.get_session_token_usage(
                        self._usage_event_session_id(session)
                    ),
                    session_id=str(getattr(session, "id", "") or ""),
                )
                for session in self._recent_surface_sessions(recent)
            )
            return format_interactive_token_history(summaries, requested=recent)
        finally:
            if owns_store:
                store.close()

    def _token_usage_store(self) -> tuple[Any, bool]:
        if store := getattr(self._rt, "context_trace_store", None):
            return store, False
        from openminion.modules.brain.paths import resolve_brain_sessions_db_path
        from openminion.modules.session.runtime.factory import (
            build_module_session_store,
        )
        from openminion.modules.storage.engine import StorageEngineConfig

        session_path = resolve_brain_sessions_db_path(
            storage_path=self._rt.storage_path
        )
        storage = self._rt.config.storage
        return (
            build_module_session_store(
                config=StorageEngineConfig(
                    root_dir=session_path.parent,
                    sqlite_path=session_path,
                    fallback_root=session_path.parent,
                    record_backend=storage.record_backend(),
                    record_backend_options=storage.record_backend_options(),
                ),
                database_path=session_path,
                env=self._rt.config_manager.env,
            ),
            True,
        )

    def _recent_surface_sessions(self, limit: int) -> tuple[Any, ...]:
        return tuple(
            self._rt.sessions.list_sessions(
                limit=limit,
                agent_id=self.agent_id,
                channel=self._channel,
                target=self._target,
            )
        )

    @staticmethod
    def _usage_event_session_id(session: Any) -> str:
        session_id = str(getattr(session, "id", "") or "").strip()
        metadata = getattr(session, "metadata", {}) or {}
        conversation_id = str(metadata.get("conversation_id", "") or "").strip()
        if not conversation_id and str(getattr(session, "target", "")) == "focus":
            conversation_id = f"focus-{session_id}"
        return (
            f"{session_id}::conv:{conversation_id}" if conversation_id else session_id
        )

    def _begin_turn_usage_tracking(self) -> None:
        self._last_turn_usage = TokenUsageTotals()
        self._current_turn_usage = None
        self._current_turn_has_live_deltas = False
        self._last_live_usage_update_at = None
        started_at = time.monotonic()
        self._current_turn_started_at_monotonic = started_at
        self._usage_updated_at_monotonic = started_at

    def _wrap_progress_callback(
        self,
        progress_callback: Callable[[dict[str, Any]], None] | None,
    ) -> Callable[[dict[str, Any]], None]:
        def _wrapped(payload: dict[str, Any]) -> None:
            self._consume_live_usage_payload(payload)
            if progress_callback is not None:
                progress_callback(payload)

        setattr(_wrapped, "__self__", getattr(progress_callback, "__self__", None))
        return _wrapped

    def _consume_live_usage_payload(self, payload: Mapping[str, Any] | None) -> None:
        turn_usage = usage_totals_from_mapping(payload)
        if turn_usage is None:
            return
        now = time.monotonic()
        last_updated = self._last_live_usage_update_at
        if (
            last_updated is not None
            and (now - last_updated) < _LIVE_USAGE_THROTTLE_SECONDS
        ):
            return
        self._current_turn_usage = turn_usage
        self._current_turn_has_live_deltas = True
        self._last_live_usage_update_at = now
        self._usage_updated_at_monotonic = now

    def _finalize_turn_usage(
        self,
        metadata: Mapping[str, Any] | None,
        *,
        succeeded: bool,
    ) -> None:
        now = time.monotonic()
        turn_started_at = self._current_turn_started_at_monotonic
        if turn_started_at is not None:
            self._last_turn_elapsed_seconds = max(0.0, now - turn_started_at)
        final_turn_usage = (
            usage_totals_from_mapping(metadata) or self._current_turn_usage
        )
        if succeeded and final_turn_usage is not None:
            self._last_turn_usage = final_turn_usage
            self._completed_session_usage = (
                accumulate_usage(self._completed_session_usage, final_turn_usage)
                or TokenUsageTotals()
            )
        self._current_turn_usage = None
        self._current_turn_has_live_deltas = False
        self._current_turn_started_at_monotonic = None
        self._last_live_usage_update_at = None
        self._usage_updated_at_monotonic = now

    def _reset_token_usage_accounting(self) -> None:
        self._completed_session_usage = TokenUsageTotals()
        self._last_turn_usage = TokenUsageTotals()
        self._current_turn_usage = None
        self._current_turn_has_live_deltas = False
        self._current_turn_started_at_monotonic = None
        self._last_turn_elapsed_seconds = None
        self._last_chat_phase_timing_payload = None
        self._last_live_usage_update_at = None
        self._usage_updated_at_monotonic = None

    def _context_limit_tokens(self) -> int | None:
        runtime_cfg = getattr(getattr(self._rt, "config", None), "runtime", None)
        value = getattr(runtime_cfg, "session_context_token_budget", None)
        if value in (None, "", 0, "0"):
            return None
        try:
            return max(0, int(str(value)))
        except (TypeError, ValueError):
            return None


__all__ = ["RuntimeTokenUsageMixin"]
