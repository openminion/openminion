from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..constants import PRINCIPAL_BINDING_STATUS_ACTIVE
from ..pairing.store import (
    now_ts as _pair_now_ts,
    scopes_json as _pair_scopes_json,
    scopes_list as _pair_scopes_list,
    token_hash as _pair_token_hash,
    validate_or_generate_token,
)
from .rows import json_dump as _json_dump


class PairTokenStoreMixin:
    if TYPE_CHECKING:
        _lock: Any
        _principals: Any
        _record_store: Any

        def _execute_count(
            self, sql: str, params: tuple[Any, ...] | list[Any] | None = None
        ) -> int: ...

        def _query_one(
            self, sql: str, params: tuple[Any, ...] | list[Any] | None = None
        ) -> dict[str, Any] | None: ...

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
    ) -> dict[str, Any]:
        normalized_channel = str(channel or "").strip()
        if not normalized_channel:
            raise ValueError("channel is required")
        generated = validate_or_generate_token(token)
        hashed = _pair_token_hash(generated, pepper=hash_pepper)
        now = _pair_now_ts()
        expires_at_ts = now + max(60, int(ttl_seconds))
        scoped = [str(scope) for scope in scopes if str(scope).strip()]
        with self._lock, self._record_store.transaction():
            self._execute_count(
                """
                INSERT INTO cp_pair_tokens(
                    token_hash, channel, token_hint, created_at_ts, expires_at_ts,
                    expected_account_id, expected_chat_key, scopes_json
                ) VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    hashed,
                    normalized_channel,
                    generated[:4],
                    now,
                    expires_at_ts,
                    expected_account_id,
                    expected_chat_key,
                    _pair_scopes_json(scoped),
                ),
            )
        return {
            "token": generated,
            "token_hint": generated[:4],
            "token_hash_prefix": hashed[:12],
            "expires_at_ts": expires_at_ts,
            "scopes": scoped,
        }

    def consume_pair_token(
        self,
        *,
        channel: str,
        token: str,
        consumer_account_id: str,
        consumer_chat_key: str,
        hash_pepper: str | None = None,
    ) -> dict[str, Any]:
        normalized_channel = str(channel or "").strip()
        hashed = _pair_token_hash(token, pepper=hash_pepper)
        now = _pair_now_ts()
        with self._lock, self._record_store.transaction():
            row = self._query_one(
                """
                SELECT token_hash, token_hint, expires_at_ts, used_at_ts,
                       expected_account_id, expected_chat_key, scopes_json
                FROM cp_pair_tokens
                WHERE token_hash = ? AND channel = ?
                LIMIT 1
                """,
                (hashed, normalized_channel),
            )
            if row is None:
                return _pair_token_failure("invalid_token", token[:4], hashed)
            token_hint = str(row.get("token_hint") or token[:4])
            if row.get("used_at_ts") is not None:
                return _pair_token_failure("already_used", token_hint, hashed)
            expires_at_ts = int(row.get("expires_at_ts") or 0)
            if expires_at_ts < now:
                return _pair_token_failure("expired_token", token_hint, hashed)
            mismatch = _pair_expected_mismatch(
                row,
                consumer_account_id=consumer_account_id,
                consumer_chat_key=consumer_chat_key,
            )
            if mismatch:
                return _pair_token_failure(mismatch, token_hint, hashed)
            changed = self._execute_count(
                """
                UPDATE cp_pair_tokens
                SET used_at_ts = ?,
                    consumer_account_id = ?,
                    consumer_chat_key = ?
                WHERE token_hash = ?
                  AND channel = ?
                  AND used_at_ts IS NULL
                  AND expires_at_ts >= ?
                """,
                (
                    now,
                    consumer_account_id,
                    consumer_chat_key,
                    hashed,
                    normalized_channel,
                    now,
                ),
            )
        if int(changed or 0) != 1:
            return _pair_token_failure("token_race", token[:4], hashed)
        return {
            "ok": True,
            "reason": "paired",
            "token_hint": token_hint,
            "token_hash_prefix": hashed[:12],
            "scopes": _pair_scopes_list(row.get("scopes_json")),
        }

    def count_recent_pair_attempts(
        self, *, channel: str, account_id: str, since_ts: int
    ) -> int:
        row = self._query_one(
            """
            SELECT COUNT(*) AS cnt
            FROM cp_pair_attempts
            WHERE channel = ? AND account_id = ? AND attempted_at_ts >= ?
            """,
            (channel, account_id, int(since_ts)),
        )
        return int(row["cnt"] if row else 0)

    def count_recent_pair_attempts_for_chat(
        self, *, channel: str, chat_key: str, since_ts: int
    ) -> int:
        row = self._query_one(
            """
            SELECT COUNT(*) AS cnt
            FROM cp_pair_attempts
            WHERE channel = ? AND chat_key = ? AND attempted_at_ts >= ?
            """,
            (channel, chat_key, int(since_ts)),
        )
        return int(row["cnt"] if row else 0)

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
    ) -> None:
        self._execute_count(
            """
            INSERT INTO cp_pair_attempts(
                channel, account_id, chat_key, attempted_at_ts,
                token_hash_prefix, outcome, detail_json
            ) VALUES (?,?,?,?,?,?,?)
            """,
            (
                channel,
                account_id,
                chat_key,
                _pair_now_ts(),
                _pair_token_hash(token, pepper=hash_pepper)[:12],
                outcome,
                _json_dump(detail or {}),
            ),
        )

    def has_pair_channel_data(self, *, channel: str) -> bool:
        row = self._query_one(
            "SELECT COUNT(*) AS cnt FROM cp_pair_tokens WHERE channel = ?",
            (channel,),
        )
        return int(row["cnt"] if row else 0) > 0

    def bulk_insert_pair_tokens(self, rows: Any) -> int:
        copied = 0
        with self._lock, self._record_store.transaction():
            for row in rows:
                channel = str(row.get("channel") or "telegram")
                expected_account_id = row.get("expected_account_id")
                expected_chat_key = row.get("expected_chat_key")
                if (
                    expected_account_id is None
                    and row.get("expected_user_id") is not None
                ):
                    expected_account_id = f"telegram-bot:user:{row['expected_user_id']}"
                if (
                    expected_chat_key is None
                    and row.get("expected_chat_id") is not None
                ):
                    expected_chat_key = f"telegram-bot:chat:{row['expected_chat_id']}"
                copied += self._execute_count(
                    """
                    INSERT INTO cp_pair_tokens(
                        token_hash, channel, token_hint, created_at_ts, expires_at_ts,
                        used_at_ts, expected_account_id, expected_chat_key,
                        consumer_account_id, consumer_chat_key, scopes_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(token_hash) DO NOTHING
                    """,
                    (
                        row["token_hash"],
                        channel,
                        row.get("token_hint") or str(row["token_hash"])[:4],
                        int(row.get("created_at_ts") or _pair_now_ts()),
                        int(row.get("expires_at_ts") or 0),
                        row.get("used_at_ts"),
                        expected_account_id,
                        expected_chat_key,
                        row.get("consumer_account_id"),
                        row.get("consumer_chat_key"),
                        _pair_scopes_json(_pair_scopes_list(row.get("scopes_json"))),
                    ),
                )
        return copied

    def bulk_insert_pair_attempts(self, rows: Any) -> int:
        copied = 0
        with self._lock, self._record_store.transaction():
            for row in rows:
                channel = str(row.get("channel") or "telegram")
                account_id = row.get("account_id")
                chat_key = row.get("chat_key")
                if account_id is None and row.get("user_id") is not None:
                    account_id = f"telegram-bot:user:{row['user_id']}"
                if chat_key is None and row.get("chat_id") is not None:
                    chat_key = f"telegram-bot:chat:{row['chat_id']}"
                copied += self._execute_count(
                    """
                    INSERT INTO cp_pair_attempts(
                        channel, account_id, chat_key, attempted_at_ts,
                        token_hash_prefix, outcome, detail_json
                    ) VALUES (?,?,?,?,?,?,?)
                    """,
                    (
                        channel,
                        account_id or "",
                        chat_key,
                        int(row.get("attempted_at_ts") or _pair_now_ts()),
                        row.get("token_hash_prefix") or "",
                        row.get("outcome") or "unknown",
                        _json_dump(row.get("detail") or {}),
                    ),
                )
        return copied

    def upsert_pairing(
        self,
        *,
        channel: str,
        chat_id: str,
        user_id: str,
        session_id: str,
        status: str = PRINCIPAL_BINDING_STATUS_ACTIVE,
        scopes: list[str] | tuple[str, ...] | None = None,
        note: str | None = None,
        pairing_id: str | None = None,
    ) -> str:
        return str(
            self._principals.upsert_pairing(
                channel=channel,
                chat_id=chat_id,
                user_id=user_id,
                session_id=session_id,
                status=status,
                scopes=scopes,
                note=note,
                pairing_id=pairing_id,
            )
        )


def _pair_token_failure(reason: str, token_hint: str, hashed: str) -> dict[str, Any]:
    return {
        "ok": False,
        "reason": reason,
        "token_hint": token_hint,
        "token_hash_prefix": hashed[:12],
        "scopes": [],
    }


def _pair_expected_mismatch(
    row: Any,
    *,
    consumer_account_id: str,
    consumer_chat_key: str,
) -> str:
    expected_account = row.get("expected_account_id")
    if expected_account is not None and str(expected_account) != str(
        consumer_account_id
    ):
        return "user_mismatch"
    expected_chat = row.get("expected_chat_key")
    if expected_chat is not None and str(expected_chat) != str(consumer_chat_key):
        return "chat_mismatch"
    return ""
