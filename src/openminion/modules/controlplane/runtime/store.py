import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Tuple

from ..constants import PRINCIPAL_BINDING_STATUS_ACTIVE
from ..interfaces import CONTROLPLANE_INTERFACE_VERSION
from ..contracts.models import AttachmentInput, AttachmentRef, InboundMessage
from ..pairing.store import (
    now_ts as _pair_now_ts,
    scopes_json as _pair_scopes_json,
    scopes_list as _pair_scopes_list,
    token_hash as _pair_token_hash,
    validate_or_generate_token,
)


@dataclass
class StoredTurn:
    role: str
    content: str
    attachments: list[str]
    meta: dict[str, Any]


class InMemoryControlPlaneStore:
    """Thread-safe in-memory persistence suitable for tests and CLI demo."""

    contract_version = CONTROLPLANE_INTERFACE_VERSION

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._session_bindings: Dict[Tuple[str, str], str] = {}
        self._session_agents: Dict[str, str] = {}
        self._session_titles: Dict[str, str] = {}
        self._session_index: Dict[str, dict[str, Any]] = {}
        self._sessions: Dict[str, list[StoredTurn]] = {}
        self._pending_clarify: Dict[str, dict[str, Any]] = {}
        self._principals: Dict[str, dict[str, Any]] = {}
        self._channel_subjects: Dict[Tuple[str, str], dict[str, Any]] = {}
        self._pairings: Dict[Tuple[str, str], dict[str, Any]] = {}
        self._pair_tokens: Dict[str, dict[str, Any]] = {}
        self._pair_attempts: list[dict[str, Any]] = []
        self._agents: Dict[str, dict[str, Any]] = {
            "agent:default": {"id": "agent:default", "name": "Default Agent"},
            "agent:brain": {"id": "agent:brain", "name": "Brain Agent"},
        }
        self._counter = 0

    def resolve_session(self, user_key: str, chat_key: str) -> str:
        key = (user_key, chat_key)
        with self._lock:
            if key not in self._session_bindings:
                session_id = self._create_session_locked(user_key, chat_key)
                self._session_bindings[key] = session_id
            return self._session_bindings[key]

    def new_session(self, user_key: str, chat_key: str) -> str:
        with self._lock:
            session_id = self._create_session_locked(user_key, chat_key)
            self._session_bindings[(user_key, chat_key)] = session_id
            return session_id

    def rebind_session(self, user_key: str, chat_key: str) -> str:
        return self.new_session(user_key, chat_key)

    def _create_session_locked(self, user_key: str, chat_key: str) -> str:
        self._counter += 1
        session_id = f"sess-{self._counter:04d}"
        self._sessions[session_id] = []
        self._session_agents.setdefault(session_id, "agent:default")
        self._session_index[session_id] = {
            "session_id": session_id,
            "user_key": user_key,
            "chat_key": chat_key,
            "title": self._session_titles.get(session_id),
        }
        return session_id

    def set_agent(self, session_id: str, agent_id: str) -> None:
        with self._lock:
            if agent_id not in self._agents:
                raise ValueError(f"unknown agent id: {agent_id}")
            self._session_agents[session_id] = agent_id

    def resolve_agent(self, session_id: str) -> str:
        with self._lock:
            return self._session_agents.get(session_id, "agent:default")

    def list_agents(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._agents.values())

    def ensure_agent(self, agent_id: str, name: str | None = None) -> None:
        with self._lock:
            self._agents.setdefault(
                agent_id, {"id": agent_id, "name": name or agent_id}
            )

    def list_sessions(
        self, user_key: str, chat_key: str | None = None
    ) -> list[dict[str, Any]]:
        with self._lock:
            sessions = [
                dict(meta)
                for meta in self._session_index.values()
                if meta.get("user_key") == user_key
                and (chat_key is None or meta.get("chat_key") == chat_key)
            ]
        return sorted(sessions, key=lambda item: str(item.get("session_id", "")))

    def bind_session(self, user_key: str, chat_key: str, session_id: str) -> None:
        with self._lock:
            self._sessions.setdefault(session_id, [])
            self._session_agents.setdefault(session_id, "agent:default")
            self._session_index.setdefault(
                session_id,
                {
                    "session_id": session_id,
                    "user_key": user_key,
                    "chat_key": chat_key,
                    "title": self._session_titles.get(session_id),
                },
            )
            self._session_bindings[(user_key, chat_key)] = session_id

    def session_owner(self, session_id: str) -> str | None:
        with self._lock:
            session = self._session_index.get(session_id)
            if not isinstance(session, dict):
                return None
            owner = session.get("user_key")
            return str(owner) if owner is not None else None

    def bind_session_owned(
        self,
        *,
        user_key: str,
        chat_key: str,
        session_id: str,
        is_admin: bool,
    ) -> bool:
        owner = self.session_owner(session_id)
        if owner is None:
            return False
        if owner != user_key and not is_admin:
            return False
        self.bind_session(user_key, chat_key, session_id)
        return True

    def set_session_title(self, session_id: str, title: str) -> None:
        normalized = title.strip()
        with self._lock:
            self._session_titles[session_id] = normalized
            self._session_index.setdefault(
                session_id,
                {
                    "session_id": session_id,
                    "user_key": None,
                    "chat_key": None,
                    "title": normalized,
                },
            )
            self._session_index[session_id]["title"] = normalized

    def get_session_title(self, session_id: str) -> str | None:
        with self._lock:
            return self._session_titles.get(session_id)

    def list_session_bindings(self, limit: int = 1000) -> list[dict[str, Any]]:
        max_items = max(1, int(limit))
        with self._lock:
            rows: list[dict[str, Any]] = []
            for (user_key, chat_key), session_id in list(
                self._session_bindings.items()
            )[:max_items]:
                session = self._session_index.get(session_id, {})
                rows.append(
                    {
                        "user_key": user_key,
                        "chat_key": chat_key,
                        "session_id": session_id,
                        "owner_user_key": session.get("user_key"),
                        "session_chat_key": session.get("chat_key"),
                    }
                )
            return rows

    # SessionClient protocol helpers
    def create_session(
        self, meta: dict[str, Any] | None = None
    ) -> str:  # pragma: no cover - convenience
        return (
            self.new_session(meta.get("user_key", "user"), meta.get("chat_key", "chat"))
            if meta
            else self.new_session("user", "chat")
        )

    def append_turn(
        self,
        *,
        session_id: str,
        role: str,
        content: str,
        attachments: list[str] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> str:
        turn = StoredTurn(
            role=role, content=content, attachments=attachments or [], meta=meta or {}
        )
        with self._lock:
            self._sessions.setdefault(session_id, []).append(turn)
        return session_id

    def attachment_refs_from_inputs(
        self, inputs: list[AttachmentInput | AttachmentRef]
    ) -> list[str]:
        refs: list[str] = []
        for item in inputs:
            if isinstance(item, AttachmentRef):
                refs.append(item.ref)
                continue
            ref = item.url or f"artifact://local/{uuid.uuid4().hex}"
            refs.append(ref)
        return refs

    def persist_inbound(self, inbound: InboundMessage, session_id: str) -> None:
        meta = {
            "channel": inbound.channel,
            "thread_key": inbound.thread_key,
        }
        self.append_turn(
            session_id=session_id,
            role="user",
            content=inbound.text,
            attachments=[],
            meta=meta,
        )

    def list_turns(self, session_id: str) -> list[StoredTurn]:
        with self._lock:
            return list(self._sessions.get(session_id, []))

    def set_pending_clarify(self, session_id: str, payload: dict[str, Any]) -> None:
        with self._lock:
            self._pending_clarify[session_id] = dict(payload)

    def get_pending_clarify(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            payload = self._pending_clarify.get(session_id)
            return dict(payload) if isinstance(payload, dict) else None

    def clear_pending_clarify(self, session_id: str) -> None:
        with self._lock:
            self._pending_clarify.pop(session_id, None)

    def list_pending_clarifies(self) -> list[dict[str, Any]]:
        with self._lock:
            result: list[dict[str, Any]] = []
            for session_id, payload in self._pending_clarify.items():
                if not isinstance(payload, dict):
                    continue
                entry = dict(payload)
                entry.setdefault("session_id", session_id)
                result.append(entry)
            return result

    # Cross-channel pairing token storage

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
        generated = validate_or_generate_token(token)
        hashed = _pair_token_hash(generated, pepper=hash_pepper)
        now = _pair_now_ts()
        row = {
            "token_hash": hashed,
            "channel": str(channel),
            "token_hint": generated[:4],
            "created_at_ts": now,
            "expires_at_ts": now + max(60, int(ttl_seconds)),
            "used_at_ts": None,
            "expected_account_id": expected_account_id,
            "expected_chat_key": expected_chat_key,
            "consumer_account_id": None,
            "consumer_chat_key": None,
            "scopes_json": _pair_scopes_json(scopes),
        }
        with self._lock:
            self._pair_tokens[hashed] = row
        return {
            "token": generated,
            "token_hint": generated[:4],
            "token_hash_prefix": hashed[:12],
            "expires_at_ts": row["expires_at_ts"],
            "scopes": list(scopes),
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
        hashed = _pair_token_hash(token, pepper=hash_pepper)
        now = _pair_now_ts()
        with self._lock:
            row = self._pair_tokens.get(hashed)
            if row is None or row.get("channel") != channel:
                return self._pair_consume_result(token, hashed, "invalid_token")
            token_hint = str(row.get("token_hint") or token[:4])
            if row.get("used_at_ts") is not None:
                return self._pair_consume_result(token_hint, hashed, "already_used")
            if int(row.get("expires_at_ts") or 0) < now:
                return self._pair_consume_result(token_hint, hashed, "expired_token")
            expected_account = row.get("expected_account_id")
            if expected_account is not None and str(expected_account) != str(
                consumer_account_id
            ):
                return self._pair_consume_result(token_hint, hashed, "user_mismatch")
            expected_chat = row.get("expected_chat_key")
            if expected_chat is not None and str(expected_chat) != str(
                consumer_chat_key
            ):
                return self._pair_consume_result(token_hint, hashed, "chat_mismatch")
            row["used_at_ts"] = now
            row["consumer_account_id"] = consumer_account_id
            row["consumer_chat_key"] = consumer_chat_key
            return {
                "ok": True,
                "reason": "paired",
                "token_hint": token_hint,
                "token_hash_prefix": hashed[:12],
                "scopes": _pair_scopes_list(row.get("scopes_json")),
            }

    def _pair_consume_result(
        self, token_or_hint: str, hashed: str, reason: str
    ) -> dict[str, Any]:
        return {
            "ok": False,
            "reason": reason,
            "token_hint": token_or_hint[:4],
            "token_hash_prefix": hashed[:12],
            "scopes": [],
        }

    def count_recent_pair_attempts(
        self, *, channel: str, account_id: str, since_ts: int
    ) -> int:
        with self._lock:
            return sum(
                1
                for row in self._pair_attempts
                if row["channel"] == channel
                and row["account_id"] == account_id
                and int(row["attempted_at_ts"]) >= int(since_ts)
            )

    def count_recent_pair_attempts_for_chat(
        self, *, channel: str, chat_key: str, since_ts: int
    ) -> int:
        with self._lock:
            return sum(
                1
                for row in self._pair_attempts
                if row["channel"] == channel
                and row.get("chat_key") == chat_key
                and int(row["attempted_at_ts"]) >= int(since_ts)
            )

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
        with self._lock:
            self._pair_attempts.append(
                {
                    "channel": channel,
                    "account_id": account_id,
                    "chat_key": chat_key,
                    "attempted_at_ts": _pair_now_ts(),
                    "token_hash_prefix": _pair_token_hash(token, pepper=hash_pepper)[
                        :12
                    ],
                    "outcome": outcome,
                    "detail": dict(detail or {}),
                }
            )

    def has_pair_channel_data(self, *, channel: str) -> bool:
        with self._lock:
            return any(
                row.get("channel") == channel for row in self._pair_tokens.values()
            )

    def bulk_insert_pair_tokens(self, rows: Any) -> int:
        copied = 0
        with self._lock:
            for row in rows:
                token_hash = str(row.get("token_hash") or "")
                if not token_hash or token_hash in self._pair_tokens:
                    continue
                item = dict(row)
                item.setdefault("channel", "telegram")
                if (
                    item.get("expected_account_id") is None
                    and item.get("expected_user_id") is not None
                ):
                    item["expected_account_id"] = (
                        f"telegram-bot:user:{item['expected_user_id']}"
                    )
                if (
                    item.get("expected_chat_key") is None
                    and item.get("expected_chat_id") is not None
                ):
                    item["expected_chat_key"] = (
                        f"telegram-bot:chat:{item['expected_chat_id']}"
                    )
                self._pair_tokens[token_hash] = item
                copied += 1
        return copied

    def bulk_insert_pair_attempts(self, rows: Any) -> int:
        copied = 0
        with self._lock:
            for row in rows:
                item = dict(row)
                item.setdefault("channel", "telegram")
                if item.get("account_id") is None and item.get("user_id") is not None:
                    item["account_id"] = f"telegram-bot:user:{item['user_id']}"
                if item.get("chat_key") is None and item.get("chat_id") is not None:
                    item["chat_key"] = f"telegram-bot:chat:{item['chat_id']}"
                self._pair_attempts.append(item)
                copied += 1
        return copied

    # P3b v1 principal identity mappings

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
        now = datetime.now(timezone.utc).isoformat()
        key = (str(channel), str(chat_id))
        with self._lock:
            pid = pairing_id or self._pairings.get(key, {}).get("pairing_id")
            pid = str(pid or f"pairing-{uuid.uuid4().hex}")
            created_at = self._pairings.get(key, {}).get("created_at", now)
            row = {
                "pairing_id": pid,
                "channel": key[0],
                "chat_id": key[1],
                "user_id": str(user_id),
                "session_id": str(session_id),
                "created_at": created_at,
                "last_seen_at": now,
                "status": str(status or PRINCIPAL_BINDING_STATUS_ACTIVE),
                "scopes": list(scopes or ()),
                "note": note,
            }
            self._pairings[key] = row
            self._principals.setdefault(
                pid,
                {
                    "principal_id": pid,
                    "created_at": now,
                    "updated_at": now,
                    "meta": {},
                },
            )
            self._channel_subjects[(key[0], key[1])] = {
                "principal_id": pid,
                "channel": key[0],
                "subject_id": key[1],
                "status": row["status"],
                "scopes": row["scopes"],
                "note": note,
                "created_at": created_at,
                "last_seen_at": now,
                "meta": {"source": "cp_pairings_dual_write"},
            }
            return pid

    def get_pairing(self, *, channel: str, chat_id: str) -> dict[str, Any] | None:
        key = (str(channel), str(chat_id))
        with self._lock:
            row = self._pairings.get(key)
            if not isinstance(row, dict):
                return None
            if str(row.get("status") or "").lower() != PRINCIPAL_BINDING_STATUS_ACTIVE:
                return None
            return dict(row)

    def list_pairings(
        self, *, channel: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = [
                dict(row)
                for row in self._pairings.values()
                if (channel is None or row.get("channel") == channel)
                and str(row.get("status") or "").lower()
                == PRINCIPAL_BINDING_STATUS_ACTIVE
            ]
        return rows[: max(1, int(limit))]

    def touch_pairing(self, *, channel: str, chat_id: str) -> None:
        key = (str(channel), str(chat_id))
        with self._lock:
            if key in self._pairings:
                self._pairings[key]["last_seen_at"] = datetime.now(
                    timezone.utc
                ).isoformat()

    def upsert_principal(
        self,
        *,
        principal_id: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> str:
        pid = str(principal_id or f"principal-{uuid.uuid4().hex}").strip()
        if not pid:
            raise ValueError("principal_id must be non-empty")
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            existing = self._principals.get(pid)
            if existing is None:
                self._principals[pid] = {
                    "principal_id": pid,
                    "created_at": now,
                    "updated_at": now,
                    "meta": dict(meta or {}),
                }
            else:
                existing["updated_at"] = now
                if meta is not None:
                    existing["meta"] = dict(meta)
        return pid

    def bind_principal_subject(
        self,
        *,
        principal_id: str,
        channel: str,
        subject_id: str,
        status: str = PRINCIPAL_BINDING_STATUS_ACTIVE,
        scopes: list[str] | tuple[str, ...] | None = None,
        note: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        pid = str(principal_id or "").strip()
        key = (str(channel or "").strip(), str(subject_id or "").strip())
        if not pid:
            raise ValueError("principal_id must be non-empty")
        if not key[0] or not key[1]:
            raise ValueError("channel and subject_id must be non-empty")
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            if pid not in self._principals:
                raise ValueError(f"unknown principal_id: {pid}")
            created_at = self._channel_subjects.get(key, {}).get("created_at", now)
            self._channel_subjects[key] = {
                "principal_id": pid,
                "channel": key[0],
                "subject_id": key[1],
                "status": str(status or PRINCIPAL_BINDING_STATUS_ACTIVE),
                "scopes": [
                    str(scope) for scope in (scopes or ()) if str(scope).strip()
                ],
                "note": note,
                "created_at": created_at,
                "last_seen_at": now,
                "meta": dict(meta or {}),
            }

    def resolve_principal(self, *, channel: str, subject_id: str) -> str | None:
        key = (str(channel or "").strip(), str(subject_id or "").strip())
        with self._lock:
            binding = self._channel_subjects.get(key)
            if not isinstance(binding, dict):
                return None
            if (
                str(binding.get("status") or "").lower()
                != PRINCIPAL_BINDING_STATUS_ACTIVE
            ):
                return None
            principal_id = str(binding.get("principal_id") or "").strip()
            return principal_id or None

    def get_channel_subject(
        self, *, channel: str, subject_id: str
    ) -> dict[str, Any] | None:
        key = (str(channel or "").strip(), str(subject_id or "").strip())
        with self._lock:
            binding = self._channel_subjects.get(key)
            return dict(binding) if isinstance(binding, dict) else None

    def touch_channel_subject(self, *, channel: str, subject_id: str) -> None:
        key = (str(channel or "").strip(), str(subject_id or "").strip())
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            if key in self._channel_subjects:
                self._channel_subjects[key]["last_seen_at"] = now
