from typing import Any

from openminion.modules.controlplane.channels.telegram.bot_api import TelegramBotAPI


class TelegramReactionsAdapter:
    def __init__(self, api: TelegramBotAPI) -> None:
        self._api = api

    def react_add(self, message: Any, emoji: str) -> None:
        chat_id, message_id = _message_ref(message)
        self._api.set_message_reaction(
            chat_id=chat_id,
            message_id=message_id,
            emoji=emoji,
            remove_all=False,
        )

    def react_remove_one(self, message: Any, emoji: str) -> None:
        del emoji
        self.react_remove_all_bot(message)

    def react_remove_all_bot(self, message: Any) -> None:
        chat_id, message_id = _message_ref(message)
        self._api.set_message_reaction(
            chat_id=chat_id,
            message_id=message_id,
            emoji="",
            remove_all=True,
        )

    def list_reactions(
        self, message: Any, scope: str = "bot_only"
    ) -> list[dict[str, Any]]:
        # Telegram Bot API does not expose a list-reactions endpoint for arbitrary messages.
        return []


def maybe_register_reactions_adapter(api: TelegramBotAPI) -> None:
    try:
        from openminion.tools.reaction.plugin import register_channel_adapter
    except Exception:
        return

    register_channel_adapter("telegram", TelegramReactionsAdapter(api))


def _message_ref(message: Any) -> tuple[str, int]:
    if hasattr(message, "conversation_id") and hasattr(message, "message_id"):
        chat_id = str(getattr(message, "conversation_id"))
        message_id = int(getattr(message, "message_id"))
        return chat_id, message_id

    if isinstance(message, dict):
        chat_id = str(message.get("conversation_id") or message.get("chat_id") or "")
        message_id = int(message.get("message_id"))
        return chat_id, message_id

    raise ValueError("invalid message reference")
