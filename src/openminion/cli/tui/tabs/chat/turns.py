# mypy: disable-error-code="attr-defined,no-untyped-def,no-untyped-call,type-arg,assignment"

from __future__ import annotations

from collections.abc import Mapping

from textual.css.query import QueryError

from openminion.cli.status import PhaseStatusController, format_token_usage_summary
from openminion.cli.presentation import (
    ThinkingIndicator,
    ToolEvent,
    build_tool_event_from_progress,
    tool_call_body,
)

from ...widgets import ChatInputBar, ChatMessage, ChatView, MessageKind

class ChatTurnMixin:
    def _tick_elapsed(self) -> None:
        controller = getattr(self, "_active_status_controller", None)
        self._refresh_token_usage_summary()
        if controller is None:
            return
        try:
            indicator = self.query_one(ThinkingIndicator)
        except (QueryError, AttributeError):
            return
        if not bool(getattr(indicator, "is_thinking", False)):
            self._active_status_controller = None
            indicator.elapsed_text = ""
            return
        snapshot = controller.snapshot_elapsed_text()
        if snapshot is not None:
            indicator.elapsed_text = snapshot

    def _refresh_token_usage_summary(self) -> None:
        try:
            label = self.query_one("#dashboard-token-usage")
        except (QueryError, AttributeError):
            return
        snapshot_getter = getattr(self._runtime, "token_usage_snapshot", None)
        if not callable(snapshot_getter):
            label.update("")
            return
        try:
            summary = format_token_usage_summary(snapshot_getter())
        except (AttributeError, TypeError, ValueError):
            summary = ""
        label.update(summary)

    def _make_phase_callback(self):
        indicator = self.query_one(ThinkingIndicator)
        chat = self.query_one(ChatView)
        app = self.app
        controller = PhaseStatusController(fallback_label="thinking…")
        controller.start_turn()
        self._active_status_controller = controller

        def _on_phase(status):
            kind = ""
            if isinstance(status, Mapping):
                kind = str(status.get("kind", "") or "").strip()
            if kind.startswith("tool_"):
                try:
                    event = build_tool_event_from_progress(status)
                except (KeyError, TypeError, ValueError):
                    return
                app.call_from_thread(self._dashboard_push_tool_event, chat, event)
                return
            if isinstance(status, Mapping):
                if self._dashboard_push_activity_row(app, chat, dict(status)):
                    return
            try:
                view = controller.update(status)
            except (AttributeError, TypeError, ValueError):
                view = None
            if view is None:
                return
            label = view.primary_text.strip()
            if not label:
                return
            try:
                refreshed = controller.refresh_view_with_live_elapsed(view)
            except AttributeError:
                refreshed = view
            try:
                app.call_from_thread(setattr, indicator, "view_model", refreshed)
            except (AttributeError, RuntimeError):
                pass

        return _on_phase

    def _dashboard_push_tool_event(self, chat: ChatView, event: ToolEvent) -> None:
        chat.push_message(
            ChatMessage(
                kind=MessageKind.TOOL,
                sender=f"tool:{event.tool_name}",
                body=tool_call_body(event),
                tool_event=event,
                tool_result=event.content or None,
            )
        )

    def _dashboard_push_activity_row(self, app, chat: ChatView, payload: dict) -> bool:
        try:
            from openminion.cli.status.activity_ledger import (
                KIND_APPROVAL,
                KIND_BACKGROUND,
                KIND_BUDGET,
                KIND_ERROR,
                KIND_PLAN,
                activity_from_progress_payload,
                format_activity_line,
            )
        except Exception:
            return False
        event = activity_from_progress_payload(payload)
        if event is None or event.kind not in {
            KIND_PLAN,
            KIND_APPROVAL,
            KIND_BACKGROUND,
            KIND_BUDGET,
            KIND_ERROR,
        }:
            return False
        line = format_activity_line(event)
        if not line:
            return False
        message_kind = (
            MessageKind.ERROR if event.kind == KIND_ERROR else MessageKind.SYSTEM
        )
        message = ChatMessage(
            kind=message_kind,
            sender=f"activity:{event.kind}",
            body=line,
        )
        try:
            app.call_from_thread(chat.push_message, message)
        except Exception:
            try:
                chat.push_message(message)
            except Exception:
                return False
        return True

    async def _do_turn(self, text: str) -> None:
        import asyncio
        import inspect

        chat = self.query_one(ChatView)
        rt = self._runtime
        progress_cb = self._make_phase_callback()
        send_kwargs: dict = {}
        sig = inspect.signature(rt.send_message)
        if "progress_callback" in sig.parameters:
            send_kwargs["progress_callback"] = progress_cb

        try:
            loop = asyncio.get_running_loop()
            try:
                chunks: list[str] = await loop.run_in_executor(
                    None,
                    lambda: self._collect_chunks_sync(rt, text, send_kwargs),
                )
            except Exception as exc:
                chat.push_message(
                    ChatMessage(
                        kind=MessageKind.ERROR,
                        sender="error",
                        body=str(exc),
                    )
                )
                return

            accumulated = ""
            pending_tool_widgets: dict[str, object] = {}
            for chunk in chunks:
                normalized = str(chunk or "")
                if normalized.startswith("[tool-result:"):
                    tool_line, _, rest = normalized.partition("\n")
                    tool_id = self._tool_chunk_id(tool_line, prefix="[tool-result:")
                    tool_widget = pending_tool_widgets.get(tool_id)
                    if tool_widget is not None:
                        tool_widget.set_tool_result(rest.strip() or "Completed.")
                    else:
                        chat.push_message(
                            ChatMessage(
                                kind=MessageKind.TOOL,
                                sender=f"tool:{tool_id}",
                                body=f"{tool_id} completed",
                                tool_result=rest.strip() or "Completed.",
                            )
                        )
                    continue
                if normalized.startswith("[tool:"):
                    tool_line, _, rest = normalized.partition("\n")
                    tool_id = self._tool_chunk_id(tool_line, prefix="[tool:")
                    widget = chat.push_message(
                        ChatMessage(
                            kind=MessageKind.TOOL,
                            sender=f"tool:{tool_id}",
                            body=self._tool_chunk_body(tool_line),
                            tool_result=rest.strip() or None,
                        )
                    )
                    if rest.strip():
                        widget.set_tool_result(rest.strip())
                    else:
                        pending_tool_widgets[tool_id] = widget
                else:
                    accumulated += normalized
            if accumulated:
                chat.push_message(
                    ChatMessage(
                        kind=MessageKind.AGENT,
                        sender=rt.agent_id,
                        body=accumulated,
                    )
                )
                self._maybe_auto_name_session(accumulated)
            for widget in pending_tool_widgets.values():
                if getattr(widget, "_message", None) is not None and (
                    getattr(widget._message, "tool_result", None) is None
                ):
                    widget.set_tool_result("Completed.")
        finally:
            self._set_busy(False)
            self._refresh_token_usage_summary()

    @staticmethod
    def _collect_chunks_sync(rt, text: str, send_kwargs: dict) -> list[str]:
        import asyncio

        async def _gather():
            return [chunk async for chunk in rt.send_message(text, **send_kwargs)]

        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_gather())
        finally:
            loop.close()

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        try:
            self.query_one(ThinkingIndicator).is_thinking = busy
        except (QueryError, AttributeError):
            pass
        try:
            input_bar = self.query_one(ChatInputBar)
        except (QueryError, AttributeError):
            return
        input_bar.set_disabled(busy)
        if not busy:
            input_bar.focus_input()

    @staticmethod
    def _tool_chunk_id(tool_line: str, *, prefix: str) -> str:
        close_idx = tool_line.find("]")
        if close_idx <= len(prefix):
            return "unknown"
        return tool_line[len(prefix) : close_idx]

    @staticmethod
    def _tool_chunk_body(tool_line: str) -> str:
        close_idx = tool_line.find("]")
        if close_idx <= 0:
            return tool_line.strip()
        tail = tool_line[close_idx + 1 :].strip()
        return tail or tool_line.strip()
