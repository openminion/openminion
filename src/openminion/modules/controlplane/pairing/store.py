from __future__ import annotations

import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Protocol

from openminion.base.time import utc_now_iso

_TOKEN_ALLOWED_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def now_ts() -> int:
    return int(datetime.fromisoformat(utc_now_iso()).timestamp())


def token_hash(token: str, *, pepper: str | None) -> str:
    material = f"{pepper or ''}{token}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def scopes_json(scopes: list[str] | tuple[str, ...] | None) -> str:
    return json.dumps({"scopes": list(scopes or ())}, ensure_ascii=True, sort_keys=True)


def scopes_list(raw: Any) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(str(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if isinstance(parsed, dict):
        values = parsed.get("scopes", [])
    elif isinstance(parsed, list):
        values = parsed
    else:
        values = []
    return [text for item in values if (text := str(item).strip())]


class PairingStoreAPI(Protocol):
    def issue_pair_token(
        self,
        *,
        channel: str,
        expected_account_id: str | None,
        expected_chat_key: str | None,
        scopes: list[str],
        token: str | None,
        ttl_seconds: int,
        hash_pepper: str | None = None,
    ) -> dict[str, Any]: ...

    def consume_pair_token(
        self,
        *,
        channel: str,
        token: str,
        consumer_account_id: str,
        consumer_chat_key: str,
        hash_pepper: str | None = None,
    ) -> dict[str, Any]: ...

    def count_recent_pair_attempts(
        self, *, channel: str, account_id: str, since_ts: int
    ) -> int: ...

    def count_recent_pair_attempts_for_chat(
        self, *, channel: str, chat_key: str, since_ts: int
    ) -> int: ...

    def record_pair_attempt(
        self,
        *,
        channel: str,
        account_id: str,
        chat_key: str | None,
        token: str,
        outcome: str,
        hash_pepper: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None: ...

    def has_pair_channel_data(self, *, channel: str) -> bool: ...

    def bulk_insert_pair_tokens(self, rows: Iterable[dict[str, Any]]) -> int: ...

    def bulk_insert_pair_attempts(self, rows: Iterable[dict[str, Any]]) -> int: ...


@dataclass
class ControlPlanePairingStore:
    """Small facade over the canonical controlplane store pairing methods."""

    store: PairingStoreAPI

    def issue_token(
        self,
        *,
        channel: str,
        expected_account_id: str | None,
        expected_chat_key: str | None,
        scopes: list[str],
        token: str | None,
        ttl_seconds: int,
        hash_pepper: str | None = None,
    ) -> dict[str, Any]:
        return self.store.issue_pair_token(
            channel=channel,
            expected_account_id=expected_account_id,
            expected_chat_key=expected_chat_key,
            scopes=scopes,
            token=token,
            ttl_seconds=ttl_seconds,
            hash_pepper=hash_pepper,
        )

    def consume_pair_token(
        self,
        *,
        channel: str,
        token: str,
        consumer_account_id: str,
        consumer_chat_key: str,
        hash_pepper: str | None = None,
    ) -> dict[str, Any]:
        return self.store.consume_pair_token(
            channel=channel,
            token=token,
            consumer_account_id=consumer_account_id,
            consumer_chat_key=consumer_chat_key,
            hash_pepper=hash_pepper,
        )

    def count_recent_attempts(
        self, *, channel: str, account_id: str, since_ts: int
    ) -> int:
        return self.store.count_recent_pair_attempts(
            channel=channel, account_id=account_id, since_ts=since_ts
        )

    def count_recent_attempts_for_chat(
        self, *, channel: str, chat_key: str, since_ts: int
    ) -> int:
        return self.store.count_recent_pair_attempts_for_chat(
            channel=channel, chat_key=chat_key, since_ts=since_ts
        )

    def record_attempt(
        self,
        *,
        channel: str,
        account_id: str,
        chat_key: str | None,
        token: str,
        outcome: str,
        hash_pepper: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        self.store.record_pair_attempt(
            channel=channel,
            account_id=account_id,
            chat_key=chat_key,
            token=token,
            outcome=outcome,
            hash_pepper=hash_pepper,
            detail=detail,
        )

    def has_channel_data(self, *, channel: str) -> bool:
        return self.store.has_pair_channel_data(channel=channel)

    def bulk_insert_tokens(self, rows: Iterable[dict[str, Any]]) -> int:
        return self.store.bulk_insert_pair_tokens(rows)

    def bulk_insert_attempts(self, rows: Iterable[dict[str, Any]]) -> int:
        return self.store.bulk_insert_pair_attempts(rows)


def validate_or_generate_token(token: str | None) -> str:
    generated = token or secrets.token_urlsafe(24).rstrip("=")
    if not _TOKEN_ALLOWED_RE.fullmatch(generated):
        raise ValueError("pair token must match channel-safe charset and length")
    return generated
