import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from openminion.modules.controlplane.channels.telegram.interfaces import (
    TELEGRAM_INTERFACE_VERSION,
)


@dataclass(frozen=True)
class TelegramAPIError(RuntimeError):
    code: int
    description: str
    retry_after: int | None = None

    def __post_init__(self) -> None:
        message = (
            f"telegram api error: code={self.code} "
            f"description={self.description!r} "
            f"retryable={self.retryable}"
        )
        if self.retry_after is not None:
            message += f" retry_after={self.retry_after}"
        RuntimeError.__init__(self, message)

    @property
    def retryable(self) -> bool:
        return self.code == 429 or self.code >= 500


@dataclass(frozen=True)
class TelegramTransportError(RuntimeError):
    message: str

    @property
    def retryable(self) -> bool:
        return True


HttpPost = Callable[[str, dict[str, Any], float], dict[str, Any]]


class TelegramBotAPI:
    contract_version = TELEGRAM_INTERFACE_VERSION

    def __init__(
        self,
        token: str,
        *,
        base_url: str = "https://api.telegram.org",
        http_post: HttpPost | None = None,
        request_timeout_seconds: float = 45.0,
    ) -> None:
        if not token:
            raise ValueError("telegram bot token is required")
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._http_post = http_post or _default_http_post
        self._request_timeout_seconds = request_timeout_seconds

    def call(
        self, method: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        url = f"{self._base_url}/bot{self._token}/{method}"
        body = payload or {}
        try:
            response = self._http_post(url, body, self._request_timeout_seconds)
        except TelegramTransportError:
            raise
        except Exception as exc:  # pragma: no cover
            raise TelegramTransportError(str(exc)) from exc

        if not isinstance(response, dict):
            raise TelegramTransportError("telegram response is not JSON object")

        ok = bool(response.get("ok"))
        if not ok:
            code = int(response.get("error_code") or 500)
            description = str(response.get("description") or "telegram api error")
            retry_after = None
            parameters = response.get("parameters")
            if isinstance(parameters, dict):
                raw_retry = parameters.get("retry_after")
                try:
                    retry_after = int(raw_retry) if raw_retry is not None else None
                except (TypeError, ValueError):
                    retry_after = None
            raise TelegramAPIError(
                code=code, description=description, retry_after=retry_after
            )

        result = response.get("result")
        return result if isinstance(result, dict) else {"value": result}

    def get_me(self) -> dict[str, Any]:
        return self.call("getMe")

    def delete_webhook(self, *, drop_pending_updates: bool = False) -> dict[str, Any]:
        return self.call(
            "deleteWebhook", {"drop_pending_updates": bool(drop_pending_updates)}
        )

    def set_webhook(
        self,
        *,
        url: str,
        secret_token: str | None = None,
        drop_pending_updates: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "url": url,
            "drop_pending_updates": bool(drop_pending_updates),
        }
        if secret_token:
            payload["secret_token"] = secret_token
        return self.call("setWebhook", payload)

    def get_updates(
        self,
        *,
        offset: int | None,
        timeout: int,
        limit: int,
        allowed_updates: list[str],
    ) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "timeout": max(0, int(timeout)),
            "limit": max(1, min(100, int(limit))),
            "allowed_updates": allowed_updates,
        }
        if offset is not None:
            payload["offset"] = int(offset)
        result = self.call("getUpdates", payload)
        if isinstance(result.get("value"), list):
            raw_updates = result["value"]
        else:
            raw_updates = result if isinstance(result, list) else []
        return [row for row in raw_updates if isinstance(row, dict)]

    def send_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.call("sendMessage", payload)

    def edit_message_text(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.call("editMessageText", payload)

    def answer_callback_query(self, callback_query_id: str) -> dict[str, Any]:
        return self.call(
            "answerCallbackQuery", {"callback_query_id": callback_query_id}
        )

    def set_message_reaction(
        self,
        *,
        chat_id: str | int,
        message_id: str | int,
        emoji: str,
        is_big: bool = False,
        remove_all: bool = False,
    ) -> dict[str, Any]:
        reaction: list[dict[str, str]] = []
        if not remove_all and emoji:
            reaction = [{"type": "emoji", "emoji": emoji}]
        payload = {
            "chat_id": chat_id,
            "message_id": int(message_id),
            "reaction": reaction,
            "is_big": bool(is_big),
        }
        return self.call("setMessageReaction", payload)


def _default_http_post(
    url: str, payload: dict[str, Any], timeout_seconds: float
) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.URLError as exc:
        raise TelegramTransportError(str(exc)) from exc

    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise TelegramTransportError(f"invalid telegram json response: {exc}") from exc
    if not isinstance(decoded, dict):
        raise TelegramTransportError("telegram response was not a json object")
    return decoded
