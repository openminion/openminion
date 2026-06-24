from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class MockUpdate:
    update_id: int
    payload: dict[str, Any]
    enqueued_at: float = field(default_factory=time.time)


class MockTelegramBotAPI:
    def __init__(self, bot_token: str = "test-bot-token"):
        self._bot_token = bot_token
        self._lock = threading.Lock()

        # Inbound queue for polling
        self._update_queue: list[MockUpdate] = []
        self._next_update_id = 1

        # Captured outbound calls
        self._captured_calls: list[dict[str, Any]] = []

        # Bot info
        self._bot_info = {
            "id": 123456789,
            "is_bot": True,
            "first_name": "TestBot",
            "username": "testbot",
        }

        # Webhook state
        self._webhook_set = False
        self._webhook_url: str | None = None

        # get_me returns cached bot info.
        self.get_me = lambda: self._bot_info.copy()

    def reset(self) -> None:
        with self._lock:
            self._update_queue.clear()
            self._next_update_id = 1
            self._captured_calls.clear()
            self._webhook_set = False
            self._webhook_url = None

    # --- Polling API ---

    def enqueue_update(self, update: dict[str, Any]) -> int:
        with self._lock:
            update_id = self._next_update_id
            self._next_update_id += 1

            # Always use auto-generated update_id, ignore any existing one in payload
            update["update_id"] = update_id

            self._update_queue.append(MockUpdate(update_id=update_id, payload=update))
            return update_id

    def get_updates(
        self,
        *,
        offset: int = 0,
        timeout: int = 0,
        limit: int = 100,
        allowed_updates: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            result = []
            for mock_update in self._update_queue:
                if mock_update.update_id >= offset:
                    if allowed_updates is None or self._check_allowed(
                        mock_update.payload, allowed_updates
                    ):
                        result.append(mock_update.payload)
                        if len(result) >= limit:
                            break

            # Remove consumed updates
            if result:
                max_consumed = max(u["update_id"] for u in result)
                self._update_queue = [
                    u for u in self._update_queue if u.update_id > max_consumed
                ]

            return result

    def _check_allowed(
        self, update: dict[str, Any], allowed_updates: list[str]
    ) -> bool:
        for update_type in allowed_updates:
            if update_type == "message" and "message" in update:
                return True
            if update_type == "edited_message" and "edited_message" in update:
                return True
            if update_type == "callback_query" and "callback_query" in update:
                return True
        return True  # Default allow

    def delete_webhook(self, *, drop_pending_updates: bool = False) -> dict[str, Any]:
        with self._lock:
            self._webhook_set = False
            self._webhook_url = None
            if drop_pending_updates:
                self._update_queue.clear()
            return {"ok": True}

    def set_webhook(
        self,
        url: str,
        secret_token: str | None = None,
        allowed_updates: list[str] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._webhook_set = True
            self._webhook_url = url
            return {"ok": True}

    # --- Outbound API ---

    def call(
        self, method: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if method == "sendMessage":
            return self.send_message(
                chat_id=payload.get("chat_id") if payload else None,
                text=payload.get("text") if payload else "",
                parse_mode=payload.get("parse_mode") if payload else None,
                reply_markup=payload.get("reply_markup") if payload else None,
                message_thread_id=payload.get("message_thread_id") if payload else None,
            )
        if method == "answerCallbackQuery":
            return self.answer_callback_query(
                callback_query_id=payload.get("callback_query_id") if payload else "",
                text=payload.get("text") if payload else None,
                show_alert=payload.get("show_alert") if payload else False,
            )
        if method == "editMessageText":
            return self.edit_message_text(
                chat_id=payload.get("chat_id") if payload else None,
                message_id=payload.get("message_id") if payload else 0,
                text=payload.get("text") if payload else "",
                parse_mode=payload.get("parse_mode") if payload else None,
                reply_markup=payload.get("reply_markup") if payload else None,
            )
        if method == "sendChatAction":
            return self.send_chat_action(
                chat_id=payload.get("chat_id") if payload else None,
                action=payload.get("action") if payload else "",
            )
        if method == "getMe":
            return self.get_me()
        if method == "deleteWebhook":
            return self.delete_webhook()
        return {"ok": True}

    def send_message(
        self,
        chat_id: int | str | dict[str, Any] | None = None,
        text: str = "",
        parse_mode: str | None = None,
        reply_markup: dict[str, Any] | None = None,
        message_thread_id: int | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if isinstance(chat_id, dict):
            payload = chat_id
            chat_id = payload.get("chat_id")
            text = str(payload.get("text") or "")
            parse_mode = payload.get("parse_mode")
            reply_markup = payload.get("reply_markup")
            raw_thread_id = payload.get("message_thread_id")
            message_thread_id = (
                int(raw_thread_id) if raw_thread_id is not None else None
            )
        with self._lock:
            result = {
                "ok": True,
                "result": {
                    "message_id": 1,
                    "chat": {"id": chat_id, "type": "private"},
                    "text": text,
                    "date": int(time.time()),
                },
            }
            if message_thread_id:
                result["result"]["message_thread_id"] = message_thread_id

            self._captured_calls.append(
                {
                    "method": "send_message",
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": parse_mode,
                    "reply_markup": reply_markup,
                    "result": result,
                }
            )
            return result

    def answer_callback_query(
        self,
        callback_query_id: str,
        text: str | None = None,
        show_alert: bool = False,
    ) -> dict[str, Any]:
        with self._lock:
            result = {"ok": True}
            self._captured_calls.append(
                {
                    "method": "answer_callback_query",
                    "callback_query_id": callback_query_id,
                    "text": text,
                }
            )
            return result

    def edit_message_text(
        self,
        chat_id: int | str | dict[str, Any],
        message_id: int = 0,
        text: str = "",
        parse_mode: str | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if isinstance(chat_id, dict):
            payload = chat_id
            chat_id = payload.get("chat_id")
            message_id = int(payload.get("message_id") or 0)
            text = str(payload.get("text") or "")
            parse_mode = payload.get("parse_mode")
            reply_markup = payload.get("reply_markup")
        with self._lock:
            result = {
                "ok": True,
                "result": {
                    "message_id": message_id,
                    "chat": {"id": chat_id, "type": "private"},
                    "text": text,
                },
            }
            self._captured_calls.append(
                {
                    "method": "edit_message_text",
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": text,
                    "parse_mode": parse_mode,
                    "reply_markup": reply_markup,
                }
            )
            return result

    def send_chat_action(
        self,
        chat_id: int | str,
        action: str,
    ) -> dict[str, Any]:
        with self._lock:
            self._captured_calls.append(
                {
                    "method": "send_chat_action",
                    "chat_id": chat_id,
                    "action": action,
                }
            )
            return {"ok": True}

    # --- Captured calls access ---

    def get_captured_calls(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._captured_calls)

    def clear_captured_calls(self) -> None:
        with self._lock:
            self._captured_calls.clear()

    def get_last_sent_messages(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                c["text"] for c in self._captured_calls if c["method"] == "send_message"
            ]


class DeterministicTelegramTransport:
    def __init__(self, bot_token: str = "test-bot-token"):
        self._api = MockTelegramBotAPI(bot_token=bot_token)
        self._lock = threading.Lock()

    @property
    def api(self) -> MockTelegramBotAPI:
        return self._api

    def inject_message(
        self,
        chat_id: int = 123,
        user_id: int = 456,
        text: str = "Hello",
        message_id: int = 1,
    ) -> int:
        update = {
            "update_id": 0,  # Will be assigned by enqueue_update
            "message": {
                "message_id": message_id,
                "from": {
                    "id": user_id,
                    "is_bot": False,
                    "first_name": "Test",
                },
                "chat": {
                    "id": chat_id,
                    "type": "private",
                },
                "date": int(time.time()),
                "text": text,
            },
        }
        return self._api.enqueue_update(update)

    def inject_callback_query(
        self,
        chat_id: int = 123,
        user_id: int = 456,
        message_id: int = 1,
        data: str = "callback_data",
        callback_query_id: str = "callback_123",
    ) -> int:
        update = {
            "update_id": 0,
            "callback_query": {
                "id": callback_query_id,
                "from": {
                    "id": user_id,
                    "is_bot": False,
                    "first_name": "Test",
                },
                "chat_instance": "123",
                "data": data,
                "game_short_name": None,
                "message": {
                    "message_id": message_id,
                    "chat": {
                        "id": chat_id,
                        "type": "private",
                    },
                    "date": int(time.time()),
                    "text": "Callback message",
                },
            },
        }
        return self._api.enqueue_update(update)

    def inject_command(
        self,
        chat_id: int = 123,
        user_id: int = 456,
        command: str = "/start",
        message_id: int = 1,
    ) -> int:
        return self.inject_message(
            chat_id=chat_id,
            user_id=user_id,
            text=command,
            message_id=message_id,
        )

    def get_outbound_texts(self) -> list[str]:
        return self._api.get_last_sent_messages()

    def clear(self) -> None:
        self._api.reset()
