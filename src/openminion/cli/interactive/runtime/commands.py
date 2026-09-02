from typing import Any

from openminion.cli.commands.agent.delegation import (
    render_agent_delegate_result,
    request_from_slash_args,
)
from openminion.cli.presentation.models import ChatMessage, MessageKind

from ..widgets import FocusTranscript


class RuntimeCommandMixin:
    """Slash commands that inspect or switch the active runtime."""

    _runtime: Any

    def _slash_model(self, args: str) -> None:
        connection = self._runtime_provider_name() or "(unknown)"
        model = self._runtime_model_name() or "(unknown)"
        arg = str(args or "").strip()
        if not arg:
            lister = getattr(self._runtime, "list_models", None)
            rows: list[Any] = []
            if callable(lister):
                try:
                    rows = list(lister() or [])
                except Exception:
                    rows = []
            lines = [f"current    {connection} / {model}"]
            if rows:
                lines.extend(("", "configured models:"))
                for row in rows:
                    marker = "◆" if row.active else " "
                    default = " (agent default)" if row.agent_default else ""
                    lines.append(
                        f"  {marker} {row.index}. {row.connection_name:<12} "
                        f"{row.model}{default}"
                    )
                lines.extend(
                    (
                        "",
                        "Use `/model use <#>` for this session, "
                        "`/model default <#>` for this agent, or `/model add`.",
                    )
                )
            else:
                lines.append("(this agent has no configured models)")
            self._push_runtime_message("\n".join(lines))
            return

        if arg == "add":
            self._push_runtime_message(
                "Add a model with the existing setup flow, then restart Focus:\n"
                + self._runtime.model_setup_command()
            )
            return

        action, _, target = arg.partition(" ")
        if action == "default":
            if not target.strip():
                self._push_runtime_message("/model: use `/model default <#>`")
                return
            try:
                selected = self._runtime.set_default_model(target.strip())
            except ValueError as exc:
                self._push_runtime_message(f"/model: {exc}")
                return
            self._push_runtime_message(
                f"model default → {selected.connection_name} / {selected.model} "
                f"for agent {self._runtime.agent_id}"
            )
            return

        switcher = getattr(self._runtime, "switch_model", None)
        if not callable(switcher):
            self._push_runtime_message("(/model: runtime does not expose switch_model)")
            return
        target = target.strip() if action == "use" else arg
        try:
            selected = switcher(target)
        except ValueError as exc:
            self._push_runtime_message(f"/model: {exc}")
            return
        self._push_runtime_message(
            f"model → {selected.connection_name} / {selected.model} "
            "(saved for this session)"
        )

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
                lines.append("  cost             unavailable")
        else:
            lines.append("  cost             unavailable")
        self._push_runtime_message("\n".join(lines))

    def _slash_tokens(self, _args: str) -> None:
        report = self._runtime.token_usage_report().strip()
        self._push_runtime_message(report or "No durable token usage data available.")

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

    def _slash_delegate(self, args: str) -> None:
        runner = getattr(self._runtime, "delegate_task", None)
        if not callable(runner):
            self._push_runtime_message("This runtime does not expose delegation.")
            return
        try:
            request = request_from_slash_args(args)
        except ValueError as exc:
            self._push_runtime_message(str(exc))
            return
        result = runner(
            mode=request.mode,
            target_agent_id=request.target_agent_id,
            instruction=request.instruction,
            task_id=request.task_id,
            timeout_seconds=request.timeout_seconds,
            child_artifact=request.child_artifact,
            workspace_root=request.workspace_root,
            approval_callback=getattr(self, "_approval_callback", None),
        )
        self._push_runtime_message(render_agent_delegate_result(dict(result or {})))

    def _slash_participants(self, _args: str) -> None:
        try:
            body = self._runtime.room_participants_report()
        except (RuntimeError, ValueError) as exc:
            body = f"/participants: {exc}"
        self._push_runtime_message(body)

    def _slash_invite(self, args: str) -> None:
        parts = str(args or "").split()
        try:
            if len(parts) == 2 and parts[0] == "agent":
                participant = self._runtime.room_invite_agent(parts[1])
            elif len(parts) in {2, 3} and parts[0] == "human":
                participant = self._runtime.room_invite_human(
                    parts[1],
                    role=parts[2] if len(parts) == 3 else "participant",
                )
            else:
                raise ValueError(
                    "usage: /invite agent <id> or /invite human <id> [role]"
                )
            body = (
                f"invited {participant.participant_type} "
                f"{participant.participant_id} as {participant.role}"
            )
        except (RuntimeError, ValueError) as exc:
            body = f"/invite: {exc}"
        self._push_runtime_message(body)

    def _slash_kick(self, args: str) -> None:
        parts = str(args or "").split()
        try:
            if len(parts) != 2:
                raise ValueError("usage: /kick <agent|human> <id>")
            removed = self._runtime.room_kick(parts[0], parts[1])
            body = "participant removed" if removed else "participant not found"
        except (RuntimeError, ValueError) as exc:
            body = f"/kick: {exc}"
        self._push_runtime_message(body)

    def _slash_activate(self, args: str) -> None:
        try:
            agent_id = str(args or "").strip()
            if not agent_id or len(agent_id.split()) != 1:
                raise ValueError("usage: /activate <agent-id>")
            self._runtime.room_activate(agent_id)
            body = f"active room agent: {agent_id}"
        except (RuntimeError, ValueError) as exc:
            body = f"/activate: {exc}"
        self._push_runtime_message(body)

    def _slash_routing(self, args: str) -> None:
        mode = str(args or "").strip()
        try:
            if not mode:
                body = self._runtime.room_participants_report()
            elif len(mode.split()) != 1:
                raise ValueError("usage: /routing [addressed|broadcast|sequential]")
            else:
                self._runtime.room_set_routing(mode)
                body = f"room routing: {mode.lower()}"
        except (RuntimeError, ValueError) as exc:
            body = f"/routing: {exc}"
        self._push_runtime_message(body)

    def _push_runtime_message(self, body: str) -> None:
        self.query_one(FocusTranscript).push_message(
            ChatMessage(kind=MessageKind.SYSTEM, sender="system", body=body)
        )
