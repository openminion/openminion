# mypy: disable-error-code="attr-defined,has-type,no-any-return"

from openminion.cli.presentation.models import ChatMessage, MessageKind

_COPYABLE_KINDS: frozenset[MessageKind] = frozenset(
    {
        MessageKind.USER,
        MessageKind.AGENT,
        MessageKind.TOOL,
        MessageKind.SYSTEM,
        MessageKind.ERROR,
    }
)


def _tool_copy_text(message: ChatMessage) -> str | None:
    result = str(message.tool_result or "").strip()
    if result:
        return result
    tool_event = message.tool_event
    if tool_event is not None:
        full = str(getattr(tool_event, "full_content", "") or "").strip()
        if full:
            return full
        content = str(getattr(tool_event, "content", "") or "").strip()
        if content:
            return content
    body = str(message.body or "").strip()
    return body or None


def copyable_text_for_message(message: ChatMessage) -> str | None:
    if message.kind == MessageKind.TOOL:
        return _tool_copy_text(message)
    body = str(message.body or "").strip()
    return body or None


class ChatSelectionMixin:
    @property
    def selected_message_id(self) -> str | None:
        return self._selected_message_id

    def _copyable_messages(self) -> list[ChatMessage]:
        return [m for m in self._messages if m.kind in _COPYABLE_KINDS]

    def _find_message(self, msg_id: str) -> ChatMessage | None:
        for msg in self._messages:
            if msg.msg_id == msg_id:
                return msg
        return None

    def _apply_selection(self, msg_id: str | None) -> None:
        from .chat import MessageWidget

        previous = self._selected_message_id
        self._selected_message_id = msg_id
        for widget in self.query(MessageWidget):
            current_id = getattr(getattr(widget, "_message", None), "msg_id", "")
            if current_id == msg_id:
                widget.add_class("--selected")
            elif current_id == previous:
                widget.remove_class("--selected")

    def select_message(self, msg_id: str | None) -> None:
        if msg_id is None:
            self._apply_selection(None)
            return
        if self._find_message(msg_id) is None:
            return
        self._apply_selection(msg_id)

    def clear_selection(self) -> None:
        self._apply_selection(None)

    def select_next_message(self) -> None:
        copyable = self._copyable_messages()
        if not copyable:
            return
        ids = [m.msg_id for m in copyable]
        if self._selected_message_id is None:
            self._apply_selection(ids[0])
            return
        try:
            idx = ids.index(self._selected_message_id)
        except ValueError:
            self._apply_selection(ids[0])
            return
        self._apply_selection(ids[min(idx + 1, len(ids) - 1)])

    def select_previous_message(self) -> None:
        copyable = self._copyable_messages()
        if not copyable:
            return
        ids = [m.msg_id for m in copyable]
        if self._selected_message_id is None:
            self._apply_selection(ids[-1])
            return
        try:
            idx = ids.index(self._selected_message_id)
        except ValueError:
            self._apply_selection(ids[-1])
            return
        self._apply_selection(ids[max(idx - 1, 0)])

    def select_first_message(self) -> None:
        copyable = self._copyable_messages()
        if copyable:
            self._apply_selection(copyable[0].msg_id)

    def select_last_message(self) -> None:
        copyable = self._copyable_messages()
        if copyable:
            self._apply_selection(copyable[-1].msg_id)

    def copy_selected_message(self) -> str | None:
        if self._selected_message_id is None:
            return None
        msg = self._find_message(self._selected_message_id)
        if msg is None:
            return None
        return copyable_text_for_message(msg)

    def copy_last_copyable_message(self) -> str | None:
        for msg in reversed(self._messages):
            if msg.kind not in _COPYABLE_KINDS:
                continue
            text = copyable_text_for_message(msg)
            if text:
                return text
        return None

    def action_select_next(self) -> None:
        self.select_next_message()

    def action_select_previous(self) -> None:
        self.select_previous_message()

    def action_select_first(self) -> None:
        self.select_first_message()

    def action_select_last(self) -> None:
        self.select_last_message()

    def action_clear_selection(self) -> None:
        self.clear_selection()
