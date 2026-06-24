import logging
import re
import time
from typing import Any, Callable

from openminion.modules.controlplane.channels.telegram.bot_api import (
    TelegramAPIError,
    TelegramBotAPI,
    TelegramTransportError,
)
from openminion.modules.controlplane.channels.telegram.constants import (
    REPLY_MODE_TO_USER,
)
from openminion.modules.controlplane.channels.telegram.config import (
    ActionsConfig,
    DeliveryConfig,
    ReplyConfig,
)
from openminion.modules.controlplane.channels.telegram.interfaces import (
    TELEGRAM_INTERFACE_VERSION,
)
from openminion.modules.controlplane.channels.telegram.models import (
    DeliveryResult,
    TelegramReplyTarget,
)
from openminion.modules.controlplane.runtime.audit import AuditLogger

_SLEEP = time.sleep

_LOGGER = logging.getLogger(__name__)

_MD_V2_ESCAPE = re.compile(r"([_\*\[\]\(\)~`>#+\-=|{}.!\\])")

_CODE_FENCE = "```"


class TelegramDeliveryService:
    contract_version = TELEGRAM_INTERFACE_VERSION

    def __init__(
        self,
        *,
        api: TelegramBotAPI,
        delivery_config: DeliveryConfig,
        reply_config: ReplyConfig,
        sleep_fn: Callable[[float], None] = _SLEEP,
        actions_config: ActionsConfig | None = None,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self._api = api
        self._delivery = delivery_config
        self._reply = reply_config
        self._sleep = sleep_fn
        self._actions = actions_config or ActionsConfig()
        self._audit_logger = audit_logger

    def send_payload(
        self, payload: dict[str, Any], target: TelegramReplyTarget
    ) -> DeliveryResult:
        text = str(payload.get("text") or "")
        reply_markup = _extract_reply_markup(payload)
        return self.send_text(text=text, target=target, reply_markup=reply_markup)

    def send_text(
        self,
        *,
        text: str,
        target: TelegramReplyTarget,
        reply_markup: dict[str, Any] | None = None,
    ) -> DeliveryResult:
        normalized = text.strip()
        if not normalized:
            return DeliveryResult(ok=True, sent_messages=[])

        markdown_aware = self._delivery.parse_mode == "MarkdownV2"
        if markdown_aware:
            # tokenize MarkdownV2 first, then escape only the
            chunks = split_text_markdown_aware(
                normalized, limit=self._delivery.chunk_limit
            )
        else:
            escaped = self._encode_text(normalized)
            chunks = split_text(escaped, limit=self._delivery.chunk_limit)

        sent: list[dict[str, Any]] = []
        for idx, chunk in enumerate(chunks):
            payload = self._build_send_payload(
                chunk,
                target,
                is_first_chunk=(idx == 0),
                reply_markup=reply_markup if idx == 0 else None,
            )
            message = self._call_with_retry("send_message", payload)
            sent.append(message)

        return DeliveryResult(ok=True, sent_messages=sent)

    def edit_text(
        self,
        *,
        chat_id: int,
        message_id: int,
        text: str,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": self._encode_text(text),
            "disable_web_page_preview": not self._delivery.link_preview,
        }
        if self._delivery.parse_mode != "plain":
            payload["parse_mode"] = self._delivery.parse_mode
        return self._call_with_retry("edit_message_text", payload)

    def _build_send_payload(
        self,
        text: str,
        target: TelegramReplyTarget,
        *,
        is_first_chunk: bool,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chat_id": target.chat_id,
            "text": text,
            "disable_web_page_preview": not self._delivery.link_preview,
        }

        if self._delivery.parse_mode != "plain":
            payload["parse_mode"] = self._delivery.parse_mode

        if target.topic_id is not None:
            payload["message_thread_id"] = target.topic_id

        if is_first_chunk and self._reply.mode == REPLY_MODE_TO_USER:
            payload["reply_to_message_id"] = target.message_id

        if reply_markup is not None:
            if self._actions.inline_buttons:
                payload["reply_markup"] = reply_markup
            else:
                _LOGGER.warning(
                    "channel.inline_buttons.disabled.skipping_keyboard",
                    extra={"chat_id": target.chat_id},
                )

        return payload

    def _encode_text(self, text: str) -> str:
        if self._delivery.parse_mode == "MarkdownV2":
            return escape_markdown_v2(text)
        return text

    def _call_with_retry(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        max_attempts = self._delivery.retry.max_attempts
        backoff_ms = self._delivery.retry.backoff_ms

        attempt = 0
        while True:
            attempt += 1
            try:
                if method == "send_message":
                    return self._api.send_message(payload)
                if method == "edit_message_text":
                    return self._api.edit_message_text(payload)
                raise ValueError(f"unsupported method {method}")
            except (TelegramAPIError, TelegramTransportError) as exc:
                retryable = getattr(exc, "retryable", False)
                if not retryable or attempt >= max_attempts:
                    self._emit_delivery_failed(
                        exc=exc,
                        attempts=attempt,
                        method=method,
                        payload=payload,
                    )
                    raise

                wait_seconds = 0.0
                if isinstance(exc, TelegramAPIError) and exc.retry_after:
                    wait_seconds = float(exc.retry_after)
                elif attempt - 1 < len(backoff_ms):
                    wait_seconds = max(0.0, backoff_ms[attempt - 1] / 1000.0)

                if wait_seconds > 0:
                    self._sleep(wait_seconds)

    def _emit_delivery_failed(
        self,
        *,
        exc: TelegramAPIError | TelegramTransportError,
        attempts: int,
        method: str,
        payload: dict[str, Any],
    ) -> None:
        if self._audit_logger is None:
            return

        if isinstance(exc, TelegramAPIError):
            code: int | None = exc.code
            description = exc.description
        else:
            code = None
            description = getattr(exc, "message", str(exc))
        details = {
            "code": code,
            "description": description,
            "retryable": bool(getattr(exc, "retryable", False)),
            "attempts": attempts,
            "chat_id": str(payload.get("chat_id", "")),
            "method": method,
        }

        if hasattr(self._audit_logger, "emit"):
            self._audit_logger.emit(
                "cp.delivery.failed",
                outcome="failed",
                severity="error",
                details=details,
            )
            return
        if hasattr(self._audit_logger, "log"):
            self._audit_logger.log(
                "cp.delivery.failed",
                outcome="failed",
                severity="error",
                **details,
            )


def _extract_reply_markup(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Pull a Telegram-shaped reply_markup out of an outbound payload."""
    raw_markup = payload.get("reply_markup")
    if isinstance(raw_markup, dict) and raw_markup:
        return raw_markup

    ui = payload.get("ui")
    if isinstance(ui, dict):
        inline = ui.get("inline_buttons")
        if isinstance(inline, list) and inline:
            return {"inline_keyboard": inline}
    return None


def escape_markdown_v2(text: str) -> str:
    return _MD_V2_ESCAPE.sub(r"\\\1", text)


def split_text(text: str, *, limit: int) -> list[str]:
    normalized = (text or "").strip()
    if not normalized:
        return []
    if len(normalized) <= limit:
        return [normalized]

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    paragraphs = normalized.split("\n\n")
    for paragraph in paragraphs:
        piece = paragraph.strip()
        if not piece:
            continue

        if len(piece) > limit:
            _flush_chunk(current, chunks)
            current = []
            current_len = 0
            chunks.extend(_split_hard(piece, limit))
            continue

        extra = len(piece) + (2 if current else 0)
        if current_len + extra > limit:
            _flush_chunk(current, chunks)
            current = [piece]
            current_len = len(piece)
            continue

        current.append(piece)
        current_len += extra

    _flush_chunk(current, chunks)
    return chunks


def _split_hard(text: str, limit: int) -> list[str]:
    lines = text.splitlines()
    out: list[str] = []
    for line in lines:
        trimmed = line.rstrip()
        if not trimmed:
            continue
        if len(trimmed) <= limit:
            out.append(trimmed)
            continue
        start = 0
        while start < len(trimmed):
            out.append(trimmed[start : start + limit])
            start += limit
    return out


def _flush_chunk(current: list[str], out: list[str]) -> None:
    if not current:
        return
    out.append("\n\n".join(current))
    current.clear()


def split_text_markdown_aware(text: str, *, limit: int) -> list[str]:
    """MarkdownV2-aware chunker."""
    normalized = (text or "").strip()
    if not normalized:
        return []

    raw_tokens = _tokenize_markdown_v2(normalized)
    # Escape text tokens; pass code fences through verbatim.
    encoded_tokens: list[tuple[str, str]] = []
    for kind, body in raw_tokens:
        if not body:
            continue
        if kind == "text":
            encoded_tokens.append(("text", escape_markdown_v2(body)))
        else:
            encoded_tokens.append((kind, body))

    full = "".join(body for _, body in encoded_tokens)
    if len(full) <= limit:
        return [full]

    chunks: list[str] = []
    current = ""

    for token_kind, token_text in encoded_tokens:
        if not token_text:
            continue
        # If this token alone exceeds the limit, flush current and split it.
        if len(token_text) > limit:
            if current:
                chunks.append(current)
                current = ""
            if token_kind == "code_fence":
                chunks.extend(_split_code_fence(token_text, limit))
            elif token_kind == "link":
                chunks.extend(_split_link(token_text, limit))
            elif token_kind in {
                "bold",
                "italic",
                "underline",
                "strikethrough",
                "spoiler",
                "inline_code",
            }:
                chunks.extend(_split_wrapped_entity(token_kind, token_text, limit))
            else:
                chunks.extend(split_text(token_text, limit=limit))
            continue
        if not current:
            current = token_text
            continue
        candidate = current + token_text
        if len(candidate) <= limit:
            current = candidate
        else:
            chunks.append(current)
            current = token_text

    if current:
        chunks.append(current)
    return chunks


def _tokenize_markdown_v2(text: str) -> list[tuple[str, str]]:
    """Split ``text`` into a flat list of ``(kind, text)`` tokens."""
    tokens: list[tuple[str, str]] = []
    idx = 0
    n = len(text)
    text_start = 0

    def flush_text(end: int) -> None:
        nonlocal text_start
        if end > text_start:
            tokens.append(("text", text[text_start:end]))
        text_start = end

    while idx < n:
        if text.startswith(_CODE_FENCE, idx):
            fence_end = text.find(_CODE_FENCE, idx + len(_CODE_FENCE))
            if fence_end == -1:
                break
            flush_text(idx)
            end = fence_end + len(_CODE_FENCE)
            tokens.append(("code_fence", text[idx:end]))
            idx = end
            text_start = idx
            continue

        token = _match_inline_entity(text, idx)
        if token is None:
            idx += 1
            continue
        kind, end = token
        flush_text(idx)
        tokens.append((kind, text[idx:end]))
        idx = end
        text_start = idx
    flush_text(n)
    return tokens


def _match_inline_entity(text: str, start: int) -> tuple[str, int] | None:
    if text.startswith("[", start):
        link_end = _find_link_end(text, start)
        if link_end is not None:
            return ("link", link_end)
    inline_specs = (
        ("spoiler", "||"),
        ("underline", "__"),
        ("inline_code", "`"),
        ("bold", "*"),
        ("italic", "_"),
        ("strikethrough", "~"),
    )
    for kind, marker in inline_specs:
        if not text.startswith(marker, start):
            continue
        end = _find_marker_end(text, marker, start + len(marker))
        if end is None:
            return None
        return (kind, end + len(marker))
    return None


def _find_marker_end(text: str, marker: str, start: int) -> int | None:
    idx = start
    while True:
        idx = text.find(marker, idx)
        if idx == -1:
            return None
        if idx == start:
            idx += len(marker)
            continue
        if text[idx - 1] != "\\":
            return idx
        idx += len(marker)


def _find_link_end(text: str, start: int) -> int | None:
    text_end = _find_marker_end(text, "]", start + 1)
    if text_end is None:
        return None
    if text_end + 1 >= len(text) or text[text_end + 1] != "(":
        return None
    url_start = text_end + 2
    depth = 1
    idx = url_start
    while idx < len(text):
        ch = text[idx]
        if ch == "(" and text[idx - 1] != "\\":
            depth += 1
        elif ch == ")" and text[idx - 1] != "\\":
            depth -= 1
            if depth == 0:
                if idx == url_start:
                    return None
                return idx + 1
        idx += 1
    return None


def _split_code_fence(block: str, limit: int) -> list[str]:
    """Split a single code-fence token into independently parseable chunks."""
    fence = _CODE_FENCE
    overhead = len(fence) * 2 + 2  # opener + closer + two newlines
    inner_limit = max(1, limit - overhead)

    inner = block
    if inner.startswith(fence):
        inner = inner[len(fence) :]
    if inner.endswith(fence):
        inner = inner[: -len(fence)]
    inner = inner.strip("\n")

    chunks: list[str] = []
    start = 0
    while start < len(inner):
        end = min(start + inner_limit, len(inner))
        segment = inner[start:end]
        chunks.append(f"{fence}\n{segment}\n{fence}")
        start = end
    if not chunks:
        # Pathological empty fence — emit it verbatim.
        return [f"{fence}\n{fence}"]
    return chunks


def _entity_markers(kind: str) -> tuple[str, str]:
    markers = {
        "bold": ("*", "*"),
        "italic": ("_", "_"),
        "underline": ("__", "__"),
        "strikethrough": ("~", "~"),
        "spoiler": ("||", "||"),
        "inline_code": ("`", "`"),
    }
    return markers[kind]


def _split_wrapped_entity(kind: str, token_text: str, limit: int) -> list[str]:
    opener, closer = _entity_markers(kind)
    inner = token_text[len(opener) : len(token_text) - len(closer)]
    inner_limit = max(1, limit - len(opener) - len(closer))
    return [
        f"{opener}{segment}{closer}"
        for segment in _split_entity_body(inner, inner_limit)
    ]


def _split_link(token_text: str, limit: int) -> list[str]:
    text_end = token_text.find("]")
    label = token_text[1:text_end]
    url = token_text[text_end + 2 : -1]
    overhead = len("[]()") + len(url)
    label_limit = max(1, limit - overhead)
    return [f"[{segment}]({url})" for segment in _split_entity_body(label, label_limit)]


def _split_entity_body(body: str, limit: int) -> list[str]:
    if len(body) <= limit:
        return [body]
    chunks: list[str] = []
    start = 0
    while start < len(body):
        end = min(start + limit, len(body))
        chunks.append(body[start:end])
        start = end
    return chunks
