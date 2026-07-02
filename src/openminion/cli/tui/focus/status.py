from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from textual.css.query import QueryError

from openminion.cli.status import format_token_usage_summary
from openminion.cli.tui.terminal.shell.labels import _runtime_label

from .widgets import FocusStatusLine
from .widgets.debug_pane import FocusDebugPane


class FocusLabelsMixin:
    def _push_status_line(
        self,
        *,
        state: str | None = None,
        tool_name: str | None = None,
    ) -> None:
        """Mirror runtime state onto the bottom status line."""
        try:
            status_line = self.query_one(FocusStatusLine)
        except QueryError:
            return
        elapsed = self._status_controller.elapsed_seconds() or 0.0
        snapshot_getter = getattr(self._runtime, "token_usage_snapshot", None)
        usage_summary = ""
        if callable(snapshot_getter):
            try:
                usage_summary = format_token_usage_summary(snapshot_getter())
            except (AttributeError, TypeError, ValueError):
                usage_summary = ""
        token_segment = self._tokens_segment(snapshot_getter)
        cost_segment = self._cost_segment(snapshot_getter)
        tokens_severity = self._tokens_severity(snapshot_getter)
        status_line.set_state(
            state=state,
            elapsed_seconds=elapsed,
            tool_name=tool_name,
            usage_summary=usage_summary,
            model=self._runtime_label(),
            cwd=self._cwd_label(),
            branch=self._branch_label(),
            tokens=token_segment,
            cost=cost_segment,
            permission_mode=str(
                getattr(self._runtime, "permission_mode", "default") or "default"
            ),
            action_policy_mode=str(
                getattr(self._runtime, "action_policy_mode_override", "") or ""
            ),
            custom=self._statusline_custom_label(),
            queued_count=self._queued_count()
            if callable(getattr(self, "_queued_count", None))
            else len(getattr(self, "_queued_turns", []) or []),
            tokens_severity=tokens_severity,
        )

    def _statusline_custom_label(self) -> str:
        getter = getattr(self._runtime, "statusline_label", None)
        if not callable(getter):
            return ""
        try:
            return str(getter() or "").strip()
        except (AttributeError, TypeError, ValueError):
            return ""

    @staticmethod
    def _tokens_severity(snapshot_getter) -> str:
        """Classify token usage severity from the current snapshot."""
        from openminion.cli.tui.focus.widgets.status_line import (
            TOKENS_SEVERITY_NORMAL,
            classify_context_severity,
        )

        if not callable(snapshot_getter):
            return TOKENS_SEVERITY_NORMAL
        try:
            snap = snapshot_getter()
        except Exception:
            return TOKENS_SEVERITY_NORMAL
        if snap is None:
            return TOKENS_SEVERITY_NORMAL
        used = getattr(snap, "context_used_tokens", None)
        limit = getattr(snap, "context_limit_tokens", None)
        return classify_context_severity(used, limit)

    def _cwd_label(self) -> str:
        """Return the shortened working dir label used by the status line."""
        from openminion.cli.tui.presentation.header import shorten_working_dir

        return shorten_working_dir(self._working_dir) or ""

    def _branch_label(self) -> str:
        """Cached git branch for the status line."""
        cached = getattr(self, "_cached_branch_label", None)
        cached_dir = getattr(self, "_cached_branch_dir", None)
        if cached_dir == self._working_dir and cached is not None:
            return cached
        from openminion.cli.tui.presentation.git.branch import detect_branch

        result = detect_branch(self._working_dir) or ""
        self._cached_branch_label = result
        self._cached_branch_dir = self._working_dir
        return result

    @staticmethod
    def _tokens_segment(snapshot_getter) -> str:
        if not callable(snapshot_getter):
            return ""
        try:
            snap = snapshot_getter()
        except (AttributeError, TypeError, ValueError):
            return ""
        if snap is None:
            return ""
        used = (
            getattr(snap, "session_total_tokens", None)
            or getattr(snap, "turn_total_tokens", None)
            or getattr(snap, "context_used_tokens", None)
            or 0
        )
        try:
            used_int = int(used)
        except (TypeError, ValueError):
            return ""
        budget = getattr(snap, "context_limit_tokens", None)
        if budget:
            try:
                return f"{used_int}/{int(budget)}"
            except (TypeError, ValueError):
                return f"{used_int}"
        if used_int == 0:
            return ""
        return f"{used_int}"

    @staticmethod
    def _cost_segment(snapshot_getter) -> str:
        if not callable(snapshot_getter):
            return ""
        try:
            snap = snapshot_getter()
        except (AttributeError, TypeError, ValueError):
            return ""
        if snap is None:
            return ""
        cost = getattr(snap, "cost_usd", None)
        if cost is None:
            return ""
        try:
            return f"${float(cost):.2f}"
        except (TypeError, ValueError):
            return ""


class FocusRuntimeStateMixin:
    def _refresh_header(self, *, status_mode: str | None = None) -> None:
        """Refresh the focus status line header fields."""
        try:
            line = self.query_one(FocusStatusLine)
        except QueryError:
            return
        agent = str(getattr(self._runtime, "agent_id", "") or "").strip()
        line.set_state(
            state=status_mode if status_mode else None,
            agent=agent,
            cwd=self._working_dir,
            model=self._runtime_label(),
        )

    def _runtime_provider_name(self) -> str:
        return str(getattr(self._runtime, "provider_name", "") or "").strip()

    def _runtime_model_name(self) -> str:
        return str(getattr(self._runtime, "model_name", "") or "").strip()

    def _runtime_label(self) -> str:
        label = _runtime_label(self._runtime)
        return "runtime —" if label == "—" else label

    def _update_debug_snapshot(self) -> None:
        runtime_obj = getattr(self._runtime, "_rt", None)
        metadata: dict[str, Any] = {}
        if runtime_obj is not None and str(
            getattr(self._runtime, "session_id", "") or ""
        ):
            try:
                records = runtime_obj.sessions.list_messages(
                    session_id=self._runtime.session_id,
                    limit=1,
                )
                if records:
                    metadata = dict(getattr(records[-1], "metadata", {}) or {})
            except (AttributeError, TypeError, ValueError):
                metadata = {}
        payload = dict(self._last_turn_debug)
        payload["message_metadata"] = metadata
        self.query_one(FocusDebugPane).set_payload(payload)

    def _session_age_label(self, updated_at: str) -> str:
        value = str(updated_at or "").strip()
        if not value:
            return "recently"
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return "recently"
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)
        minutes = max(0, int(delta.total_seconds() // 60))
        if minutes < 60:
            return f"{minutes}m ago"
        return f"{minutes // 60}h ago"

    def _normalize_tool_args(self, args: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(args or {})
        raw_path = str(normalized.get("path", "") or "").strip()
        if raw_path:
            try:
                normalized["path"] = str(
                    Path(raw_path)
                    .resolve(strict=False)
                    .relative_to(Path(self._working_dir).resolve(strict=False))
                )
            except (ValueError, OSError):
                normalized["path"] = raw_path
        return normalized
