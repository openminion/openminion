from __future__ import annotations

from openminion.cli.presentation.models import ChatMessage, MessageKind

from ..widgets import FocusTranscript


class RuntimeCommandMixin:
    """Slash commands that inspect or switch the active runtime."""

    def _slash_model(self, args: str) -> None:
        provider = self._runtime_provider_name() or "(unknown)"
        model = self._runtime_model_name() or "(unknown)"
        arg = str(args or "").strip()
        if not arg:
            lister = getattr(self._runtime, "list_models", None)
            rows: list[tuple[str, str, bool]] = []
            if callable(lister):
                try:
                    rows = list(lister() or [])
                except Exception:
                    rows = []
            lines = [
                f"current    {provider}/{model}" if model else f"current    {provider}"
            ]
            if rows:
                lines.extend(("", "configured providers:"))
                for name, configured_model, is_active in rows:
                    marker = "◆" if is_active else " "
                    lines.append(
                        f"  {marker} {name:<12} {configured_model or '(none)'}"
                    )
                lines.extend(
                    (
                        "",
                        "Switch with `/model <provider>` or "
                        "`/model <provider>/<model>` (session-scoped).",
                    )
                )
            else:
                lines.append("(no providers configured)")
            self._push_runtime_message("\n".join(lines))
            return

        switcher = getattr(self._runtime, "switch_model", None)
        if not callable(switcher):
            self._push_runtime_message("(/model: runtime does not expose switch_model)")
            return
        try:
            new_provider, new_model = switcher(arg)
        except ValueError as exc:
            self._push_runtime_message(f"/model: {exc}")
            return
        label = (
            f"{new_provider}/{new_model}"
            if new_model
            else (new_provider or "(default)")
        )
        self._push_runtime_message(f"model → {label} (session-scoped; restart reverts)")

    def _slash_cost(self, _args: str) -> None:
        snapshot_getter = getattr(self._runtime, "token_usage_snapshot", None)
        snap = None
        if callable(snapshot_getter):
            try:
                snap = snapshot_getter()
            except (AttributeError, TypeError, ValueError):
                pass
        if snap is None or not getattr(snap, "has_any_usage", False):
            self._push_runtime_message(
                "No token / cost usage data available for this session."
            )
            return

        lines = ["Session usage:"]
        session_total = getattr(snap, "session_total_tokens", None)
        turn_total = getattr(snap, "turn_total_tokens", None)
        context_used = getattr(snap, "context_used_tokens", None)
        context_limit = getattr(snap, "context_limit_tokens", None)
        cost_usd = getattr(snap, "cost_usd", None)
        if session_total is not None:
            lines.append(f"  session tokens   {session_total}")
        if turn_total is not None:
            lines.append(f"  last turn        {turn_total}")
        if context_used is not None and context_limit:
            pct = snap.context_pct
            pct_str = f"  ({pct}%)" if pct is not None else ""
            lines.append(f"  context window   {context_used}/{context_limit}{pct_str}")
        if cost_usd is not None:
            try:
                lines.append(f"  estimated cost   ${float(cost_usd):.4f}")
            except (TypeError, ValueError):
                pass
        self._push_runtime_message("\n".join(lines))

    def _slash_agent(self, args: str) -> None:
        chat = self.query_one(FocusTranscript)
        runtime = self._runtime
        target = args.strip()
        try:
            agents = list(runtime.list_agents() or [])
        except Exception as exc:
            self._push_runtime_message(f"Could not list agents: {exc}")
            return
        active_id = str(getattr(runtime, "agent_id", "") or "").strip()
        if not target:
            if not agents:
                self._push_runtime_message("No agents registered.")
                return
            lines = ["Agents:"]
            for entry in agents:
                agent_id = str(getattr(entry, "id", entry)).strip() or "?"
                marker = "● " if agent_id == active_id else "  "
                lines.append(f"  {marker}{agent_id}")
            lines.extend(("", "Use `/agent <id>` to switch."))
            self._push_runtime_message("\n".join(lines))
            return

        known = {str(getattr(entry, "id", entry)).strip() for entry in agents}
        if target not in known:
            self._push_runtime_message(
                f"Unknown agent: {target!r}. Use bare `/agent` to list registered agents."
            )
            return
        try:
            runtime.switch_agent(target)
        except Exception as exc:
            self._push_runtime_message(f"Could not switch agent: {exc}")
            return
        self._tool_widgets.clear()
        chat.clear_messages()
        if not bool(getattr(runtime, "is_bound", False)):
            try:
                creator = getattr(runtime, "create_new_session", None)
                if callable(creator):
                    creator()
            except Exception as exc:
                self._refresh_header()
                self._push_runtime_message(
                    f"Switched to agent {target}, but could not create a new session: {exc}"
                )
                return
        self._refresh_header()
        self._push_runtime_message(f"Switched to agent {target}.")

    def _push_runtime_message(self, body: str) -> None:
        self.query_one(FocusTranscript).push_message(
            ChatMessage(kind=MessageKind.SYSTEM, sender="system", body=body)
        )
