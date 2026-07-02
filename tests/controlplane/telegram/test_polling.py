from __future__ import annotations

from pathlib import Path
import threading
import uuid
from typing import Any

from openminion.modules.controlplane.channels.telegram.config import (
    AccessConfig,
    ActionsConfig,
    DeliveryConfig,
    PairingConfig,
    PollingConfig,
    ReplyConfig,
    TelegramChannelConfig,
)
from openminion.modules.controlplane.channels.telegram.delivery import (
    TelegramDeliveryService,
)
from openminion.modules.controlplane.channels.telegram.normalization import (
    session_scope_key,
)
from openminion.modules.controlplane.channels.telegram.polling import (
    TelegramPollingRunner,
)
from openminion.modules.controlplane.channels.telegram.state import (
    TelegramPollStateStore,
)


class _FakeRuntime:
    def __init__(self) -> None:
        self.inputs: list[str] = []

    def handle_inbound(self, inbound: Any) -> dict[str, Any]:
        self.inputs.append(inbound.text)
        return {
            "type": "chat",
            "text": f"echo:{inbound.text}",
            "session_id": "sess-1",
            "agent_id": "agent:default",
        }


class _ClarifyRuntime:
    def __init__(self) -> None:
        self.calls: list[Any] = []
        self.store = _ClarifyOnlyStore()

    def handle_inbound(self, inbound: Any) -> dict[str, Any]:
        self.calls.append(inbound)
        if len(self.calls) == 1:
            return {
                "type": "chat",
                "text": "Which location should I check weather for?",
                "status": "waiting_user",
                "session_id": "sess-clarify",
                "agent_id": "agent:default",
                "clarify": {
                    "clarify_id": "clar-1",
                    "trace_id": "trace-clar-1",
                    "session_id": "sess-clarify",
                    "blocking": True,
                    "questions": [
                        {
                            "id": "q1",
                            "type": "missing_field",
                            "question": "Which location should I check weather for?",
                            "is_blocking": True,
                        }
                    ],
                },
            }
        return {
            "type": "chat",
            "text": "Weather for San Diego is mild.",
            "status": "completed",
            "session_id": "sess-clarify",
            "agent_id": "agent:default",
        }


class _BrokenThenRecoveringRuntime:
    def __init__(self) -> None:
        self.inputs: list[str] = []
        self.calls = 0

    def handle_inbound(self, inbound: Any) -> dict[str, Any]:
        self.calls += 1
        self.inputs.append(inbound.text)
        if self.calls == 1:
            raise RuntimeError("simulated runtime dispatch failure")
        return {
            "type": "chat",
            "text": f"echo:{inbound.text}",
            "session_id": "sess-recovered",
            "agent_id": "agent:default",
        }


class _AuthStore:
    def __init__(self, pairing: dict[str, Any] | None) -> None:
        self._pairing = pairing
        self.lookups: list[tuple[str, str]] = []

    def get_pairing(self, *, channel: str, chat_id: str) -> dict[str, Any] | None:
        self.lookups.append((channel, chat_id))
        return self._pairing


class _ClarifyOnlyStore:
    def __init__(self) -> None:
        self._pending: dict[str, dict[str, Any]] = {}

    def resolve_session(self, user_key: str, chat_key: str) -> str:
        del user_key, chat_key
        return "sess-clarify"

    def set_pending_clarify(self, session_id: str, payload: dict[str, Any]) -> None:
        self._pending[session_id] = dict(payload)

    def get_pending_clarify(self, session_id: str) -> dict[str, Any] | None:
        payload = self._pending.get(session_id)
        return dict(payload) if isinstance(payload, dict) else None

    def clear_pending_clarify(self, session_id: str) -> None:
        self._pending.pop(session_id, None)


class _PairingAuthStore:
    def __init__(self) -> None:
        self._pairings: dict[tuple[str, str], dict[str, Any]] = {}
        self._pending: dict[str, dict[str, Any]] = {}
        self._bindings: dict[tuple[str, str], dict[str, Any]] = {}

    def resolve_session(self, user_key: str, chat_key: str) -> str:
        del user_key
        return f"sess:{chat_key}"

    def upsert_pairing(
        self,
        *,
        channel: str,
        chat_id: str,
        user_id: str,
        session_id: str,
        status: str = "active",
        scopes: list[str] | tuple[str, ...] | None = None,
        note: str | None = None,
        pairing_id: str | None = None,
    ) -> str:
        pid = str(pairing_id or f"pair-{uuid.uuid4().hex[:12]}")
        key = (str(channel), str(chat_id))
        payload = {
            "pairing_id": pid,
            "channel": str(channel),
            "chat_id": str(chat_id),
            "user_id": str(user_id),
            "session_id": str(session_id),
            "status": str(status),
            "scopes": list(scopes or []),
            "note": note,
        }
        self._pairings[key] = payload
        self._bindings[key] = {
            "principal_id": pid,
            "channel": str(channel),
            "subject_id": str(chat_id),
            "status": str(status),
            "scopes": list(scopes or []),
            "note": note,
            "meta": {"source": "cp_pairings_dual_write"},
        }
        return pid

    def get_pairing(self, *, channel: str, chat_id: str) -> dict[str, Any] | None:
        payload = self._pairings.get((str(channel), str(chat_id)))
        return dict(payload) if isinstance(payload, dict) else None

    def resolve_principal(self, *, channel: str, subject_id: str) -> str | None:
        binding = self._bindings.get((str(channel), str(subject_id)))
        if not isinstance(binding, dict):
            return None
        return str(binding.get("principal_id") or "")

    def get_channel_subject(
        self, *, channel: str, subject_id: str
    ) -> dict[str, Any] | None:
        binding = self._bindings.get((str(channel), str(subject_id)))
        return dict(binding) if isinstance(binding, dict) else None

    def set_pending_clarify(self, session_id: str, payload: dict[str, Any]) -> None:
        self._pending[session_id] = dict(payload)

    def get_pending_clarify(self, session_id: str) -> dict[str, Any] | None:
        payload = self._pending.get(session_id)
        return dict(payload) if isinstance(payload, dict) else None

    def clear_pending_clarify(self, session_id: str) -> None:
        self._pending.pop(session_id, None)


class _AuthRuntime(_FakeRuntime):
    def __init__(self, store: _AuthStore) -> None:
        super().__init__()
        self.store = store


class _FakeAPI:
    def __init__(self, batches: list[list[dict[str, Any]]]) -> None:
        self._batches = list(batches)
        self.get_updates_calls: list[dict[str, Any]] = []
        self.sent_payloads: list[dict[str, Any]] = []
        self.chat_actions: list[dict[str, Any]] = []
        self.deleted_webhook = False
        self.callback_answers: list[str] = []

    def get_me(self) -> dict[str, Any]:
        return {"id": 99, "username": "mybot"}

    def delete_webhook(self, *, drop_pending_updates: bool = False) -> dict[str, Any]:
        self.deleted_webhook = True
        return {"ok": True, "drop_pending_updates": drop_pending_updates}

    def get_updates(
        self,
        *,
        offset: int | None,
        timeout: int,
        limit: int,
        allowed_updates: list[str],
    ) -> list[dict[str, Any]]:
        self.get_updates_calls.append(
            {
                "offset": offset,
                "timeout": timeout,
                "limit": limit,
                "allowed_updates": list(allowed_updates),
            }
        )
        if not self._batches:
            return []
        return self._batches.pop(0)

    def send_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.sent_payloads.append(payload)
        return {
            "message_id": 100 + len(self.sent_payloads),
            "chat": {"id": payload["chat_id"]},
            "message_thread_id": payload.get("message_thread_id"),
        }

    def send_chat_action(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.chat_actions.append(dict(payload))
        return {"ok": True}

    def edit_message_text(self, payload: dict[str, Any]) -> dict[str, Any]:
        return payload

    def answer_callback_query(self, callback_query_id: str) -> dict[str, Any]:
        self.callback_answers.append(callback_query_id)
        return {"ok": True}


class _AuditCollector:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def emit(
        self, event_type: str, *, details: dict[str, object], **kwargs: object
    ) -> None:
        payload = dict(details)
        payload.update(kwargs)
        self.events.append((event_type, payload))


class _PairingStore:
    def __init__(self, pairings: list[dict[str, Any]]) -> None:
        self.pairings = pairings

    def list_pairings(
        self, *, channel: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        selected = [
            pairing
            for pairing in self.pairings
            if channel is None or pairing.get("channel") == channel
        ]
        return selected[:limit]


def _message_update(update_id: int, text: str = "hello") -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id * 10,
            "text": text,
            "chat": {"id": -1001, "type": "supergroup"},
            "from": {"id": 111, "username": "alice", "first_name": "Alice"},
        },
    }


def _private_message_update(
    update_id: int, text: str, *, user_id: int = 111
) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id * 10,
            "text": text,
            "chat": {"id": user_id, "type": "private"},
            "from": {"id": user_id, "username": "alice", "first_name": "Alice"},
        },
    }


def _callback_update(update_id: int, data: str) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "callback_query": {
            "id": f"cq-{update_id}",
            "data": data,
            "from": {"id": 111, "username": "alice"},
            "message": {
                "message_id": update_id * 10,
                "chat": {"id": -1001, "type": "supergroup"},
            },
        },
    }


def _runner(
    api: _FakeAPI,
    db_path: Path,
    *,
    sleep_fn=lambda _s: None,
    **cfg_overrides: Any,
) -> tuple[TelegramPollingRunner, _FakeRuntime]:
    polling = cfg_overrides.pop(
        "polling",
        PollingConfig(
            timeout_seconds=1,
            limit=100,
            persist_offset=True,
            drop_pending_on_start=False,
        ),
    )
    access = cfg_overrides.pop(
        "access",
        AccessConfig(group_policy="allow", mention_only_in_groups=False),
    )
    delivery_cfg = cfg_overrides.pop(
        "delivery",
        DeliveryConfig(parse_mode="plain", chunk_limit=500),
    )
    reply = cfg_overrides.pop("reply", ReplyConfig(mode="reply_to_user"))
    actions = cfg_overrides.pop(
        "actions",
        ActionsConfig(
            send_message=True, edit_message=True, reactions=False, inline_buttons=True
        ),
    )
    audit_logger = cfg_overrides.pop("audit_logger", None)
    runtime = cfg_overrides.pop("runtime", None)

    cfg = TelegramChannelConfig(
        enabled=True,
        bot_token="x",
        allowed_updates=["message", "callback_query"],
        polling=polling,
        access=access,
        delivery=delivery_cfg,
        reply=reply,
        actions=actions,
        **cfg_overrides,
    )
    if runtime is None:
        runtime = _FakeRuntime()
    delivery = TelegramDeliveryService(
        api=api,  # type: ignore[arg-type]
        delivery_config=cfg.delivery,
        reply_config=cfg.reply,
        sleep_fn=lambda _s: None,
    )
    store = TelegramPollStateStore(str(db_path))
    runner = TelegramPollingRunner(
        config=cfg,
        api=api,  # type: ignore[arg-type]
        runtime=runtime,
        delivery=delivery,
        state_store=store,
        audit_logger=audit_logger,
        sleep_fn=sleep_fn,
    )
    return runner, runtime


def _runner_with_runtime(
    api: _FakeAPI,
    db_path: Path,
    runtime: Any,
    *,
    sleep_fn=lambda _s: None,
    **cfg_overrides: Any,
) -> TelegramPollingRunner:
    polling = cfg_overrides.pop(
        "polling",
        PollingConfig(
            timeout_seconds=1,
            limit=100,
            persist_offset=True,
            drop_pending_on_start=False,
        ),
    )
    access = cfg_overrides.pop(
        "access",
        AccessConfig(group_policy="allow", mention_only_in_groups=False),
    )
    delivery_cfg = cfg_overrides.pop(
        "delivery",
        DeliveryConfig(parse_mode="plain", chunk_limit=500),
    )
    reply = cfg_overrides.pop("reply", ReplyConfig(mode="reply_to_user"))
    actions = cfg_overrides.pop(
        "actions",
        ActionsConfig(
            send_message=True, edit_message=True, reactions=False, inline_buttons=True
        ),
    )
    audit_logger = cfg_overrides.pop("audit_logger", None)
    cfg = TelegramChannelConfig(
        enabled=True,
        bot_token="x",
        allowed_updates=["message", "callback_query"],
        polling=polling,
        access=access,
        delivery=delivery_cfg,
        reply=reply,
        actions=actions,
        **cfg_overrides,
    )
    delivery = TelegramDeliveryService(
        api=api,  # type: ignore[arg-type]
        delivery_config=cfg.delivery,
        reply_config=cfg.reply,
        sleep_fn=lambda _s: None,
    )
    store = TelegramPollStateStore(str(db_path))
    return TelegramPollingRunner(
        config=cfg,
        api=api,  # type: ignore[arg-type]
        runtime=runtime,
        delivery=delivery,
        state_store=store,
        audit_logger=audit_logger,
        sleep_fn=sleep_fn,
    )


def test_polling_persists_offset_and_processes_updates(tmp_path: Path) -> None:
    api = _FakeAPI(
        [
            [
                _message_update(1, "hello"),
                _message_update(2, "/new"),
            ]
        ]
    )
    runner, runtime = _runner(api, tmp_path / "state.db")

    processed = runner.run_once()

    assert processed == 2
    assert runtime.inputs == ["hello", "/session new"]
    assert len(api.sent_payloads) == 2

    store = TelegramPollStateStore(str(tmp_path / "state.db"))
    assert store.get_last_update_id("telegram-bot:99") == 2
    store.close()


def test_polling_sends_typing_action_while_dispatching(tmp_path: Path) -> None:
    api = _FakeAPI([[_message_update(1, "hello")]])
    runner, runtime = _runner(api, tmp_path / "state.db")

    processed = runner.run_once()

    assert processed == 1
    assert runtime.inputs == ["hello"]
    assert api.chat_actions == [{"chat_id": -1001, "action": "typing"}]
    assert not any(
        "Still working on it" in payload["text"] for payload in api.sent_payloads
    )


def test_polling_sends_bounded_progress_notice_for_long_turn(tmp_path: Path) -> None:
    class _SlowRuntime(_FakeRuntime):
        def handle_inbound(self, inbound: Any) -> dict[str, Any]:
            threading.Event().wait(0.15)
            return super().handle_inbound(inbound)

    api = _FakeAPI([[_message_update(1, "slow request")]])
    runner, runtime = _runner(
        api,
        tmp_path / "state.db",
        runtime=_SlowRuntime(),
    )
    runner._chat_action_interval_seconds = 0.05  # noqa: SLF001
    runner._progress_notice_after_seconds = 0.05  # noqa: SLF001

    processed = runner.run_once()

    assert processed == 1
    assert runtime.inputs == ["slow request"]
    progress_messages = [
        payload["text"]
        for payload in api.sent_payloads
        if "Still working on it" in payload["text"]
    ]
    assert progress_messages == [
        "Still working on it. OpenMinion will reply here when the turn finishes."
    ]


def test_runner_online_notice_sends_once_to_active_pairings(tmp_path: Path) -> None:
    api = _FakeAPI([])
    audit = _AuditCollector()
    runner, _runtime = _runner(api, tmp_path / "state.db", audit_logger=audit)
    runner._store = _PairingStore(  # noqa: SLF001
        [
            {
                "channel": "telegram",
                "chat_id": "7105273251",
                "status": "active",
            }
        ]
    )

    runner._send_runner_online_notice()  # noqa: SLF001
    runner._send_runner_online_notice()  # noqa: SLF001

    assert len(api.sent_payloads) == 1
    assert api.sent_payloads[0]["chat_id"] == 7105273251
    assert "runner is online" in api.sent_payloads[0]["text"]
    assert "/status" in api.sent_payloads[0]["text"]
    assert [event[0] for event in audit.events] == [
        "cp.telegram.runner.online_notice_sent"
    ]
    assert audit.events[0][1]["paired_chat_count"] == 1


def test_runner_online_notice_skips_when_no_active_pairings(tmp_path: Path) -> None:
    api = _FakeAPI([])
    runner, _runtime = _runner(api, tmp_path / "state.db")
    runner._store = _PairingStore([])  # noqa: SLF001

    runner._send_runner_online_notice()  # noqa: SLF001

    assert api.sent_payloads == []


def test_polling_resumes_from_persisted_offset(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    store = TelegramPollStateStore(str(db_path))
    store.set_last_update_id("telegram-bot:99", 9)
    store.close()

    api = _FakeAPI([[]])
    runner, _ = _runner(api, db_path)

    runner.run_once()

    assert api.get_updates_calls[-1]["offset"] == 10


def test_polling_drop_pending_on_start_uses_negative_offset(tmp_path: Path) -> None:
    api = _FakeAPI(
        [
            [_message_update(50, "old")],  # initialize drop pending probe
            [],  # run_once poll
        ]
    )
    runner, _ = _runner(
        api,
        tmp_path / "state.db",
        polling=PollingConfig(
            timeout_seconds=1,
            limit=100,
            persist_offset=True,
            drop_pending_on_start=True,
        ),
    )

    runner.run_once()

    assert api.get_updates_calls[0]["offset"] == -1
    assert api.get_updates_calls[1]["offset"] == 51


def test_polling_answers_callback_query(tmp_path: Path) -> None:
    api = _FakeAPI([[_callback_update(5, "clicked")]])
    runner, runtime = _runner(api, tmp_path / "state.db")

    runner.run_once()

    assert runtime.inputs == ["clicked"]
    assert api.callback_answers == ["cq-5"]


def test_polling_renders_clarify_prompt_and_persists_pending(tmp_path: Path) -> None:
    api = _FakeAPI([[_private_message_update(1, "what's weather today?", user_id=111)]])
    runtime = _ClarifyRuntime()
    runner = _runner_with_runtime(
        api,
        tmp_path / "state.db",
        runtime,
        access=AccessConfig(
            dm_policy="allow", group_policy="allow", mention_only_in_groups=False
        ),
    )

    runner.run_once()

    assert len(runtime.calls) == 1
    assert any("/clarify clar-1" in payload["text"] for payload in api.sent_payloads)
    session_id = runtime.store.resolve_session(
        "telegram:111", session_scope_key(111, None)
    )
    pending = runtime.store.get_pending_clarify(session_id)
    assert pending is not None
    assert pending["clarify_id"] == "clar-1"
    assert pending["trace_id"] == "trace-clar-1"


def test_polling_routes_clarify_answer_with_trace_and_correlation(
    tmp_path: Path,
) -> None:
    api = _FakeAPI(
        [
            [_private_message_update(1, "what's weather today?", user_id=111)],
            [_private_message_update(2, "/clarify clar-1 q1 San Diego", user_id=111)],
        ]
    )
    runtime = _ClarifyRuntime()
    runner = _runner_with_runtime(
        api,
        tmp_path / "state.db",
        runtime,
        access=AccessConfig(
            dm_policy="allow", group_policy="allow", mention_only_in_groups=False
        ),
    )

    runner.run_once()
    runner.run_once()

    assert len(runtime.calls) == 2
    second_meta = runtime.calls[1].meta
    second_metadata = runtime.calls[1].metadata
    assert second_meta["trace_id"] == "trace-clar-1"
    assert second_metadata["trace_id"] == "trace-clar-1"
    assert second_meta == second_metadata
    clarify_answer = second_meta["clarify_answer"]
    assert clarify_answer["clarify_id"] == "clar-1"
    assert clarify_answer["question_id"] == "q1"
    assert clarify_answer["answer"] == "San Diego"
    session_id = runtime.store.resolve_session(
        "telegram:111", session_scope_key(111, None)
    )
    assert runtime.store.get_pending_clarify(session_id) is None


def test_polling_routes_unknown_clarify_id_to_runtime(tmp_path: Path) -> None:
    api = _FakeAPI(
        [
            [_private_message_update(1, "what's weather today?", user_id=111)],
            [_private_message_update(2, "/clarify wrong-id q1 San Diego", user_id=111)],
        ]
    )
    runtime = _ClarifyRuntime()
    runner = _runner_with_runtime(
        api,
        tmp_path / "state.db",
        runtime,
        access=AccessConfig(
            dm_policy="allow", group_policy="allow", mention_only_in_groups=False
        ),
    )

    runner.run_once()
    runner.run_once()

    assert len(runtime.calls) == 2
    second_meta = runtime.calls[1].meta
    second_metadata = runtime.calls[1].metadata
    assert second_meta["trace_id"] == "trace-clar-1"
    assert second_metadata["trace_id"] == "trace-clar-1"
    assert second_meta == second_metadata
    clarify_answer = second_meta["clarify_answer"]
    assert clarify_answer["clarify_id"] == "wrong-id"
    assert clarify_answer["question_id"] == "q1"
    assert clarify_answer["answer"] == "San Diego"
    assert not any(
        "Unknown clarify id" in payload["text"] for payload in api.sent_payloads
    )


def test_polling_prefers_controlplane_pending_clarify_store(tmp_path: Path) -> None:
    api = _FakeAPI([[_private_message_update(1, "what's weather today?", user_id=111)]])
    runtime = _ClarifyRuntime()
    runner = _runner_with_runtime(
        api,
        tmp_path / "state.db",
        runtime,
        access=AccessConfig(
            dm_policy="allow", group_policy="allow", mention_only_in_groups=False
        ),
    )

    runner.run_once()

    session_id = runtime.store.resolve_session(
        "telegram:111", session_scope_key(111, None)
    )
    pending = runtime.store.get_pending_clarify(session_id)
    assert pending is not None
    assert pending["clarify_id"] == "clar-1"


def test_pairing_start_token_binds_and_allows_followup_dm(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    store = TelegramPollStateStore(str(db_path))
    issued = store.issue_pair_token(
        token="pairToken_123",
        token_ttl_seconds=600,
        scopes=["chat.interact"],
        expected_user_id=111,
        expected_chat_id=111,
        hash_pepper=None,
    )
    store.close()

    api = _FakeAPI(
        [
            [_private_message_update(1, f"/start {issued.token}", user_id=111)],
            [_private_message_update(2, "hello after pair", user_id=111)],
        ]
    )
    runtime = _FakeRuntime()
    runtime.store = _PairingAuthStore()  # type: ignore[attr-defined]
    runner, runtime = _runner(
        api,
        db_path,
        access=AccessConfig(dm_policy="allowlist", allow_from_user_ids=[]),
        pairing=PairingConfig(enabled=True),
        runtime=runtime,
    )

    first = runner.run_once()
    second = runner.run_once()

    assert first == 1
    assert second == 1
    assert runtime.inputs == ["hello after pair"]
    assert any(payload["text"] == "Paired ✅" for payload in api.sent_payloads)


def test_unpaired_dm_gets_pairing_hint_and_is_not_routed(tmp_path: Path) -> None:
    api = _FakeAPI([[_private_message_update(1, "hello", user_id=111)]])
    runner, runtime = _runner(
        api,
        tmp_path / "state.db",
        access=AccessConfig(dm_policy="allowlist", allow_from_user_ids=[]),
        pairing=PairingConfig(enabled=True),
    )

    runner.run_once()

    assert runtime.inputs == []
    assert any("Pairing required" in payload["text"] for payload in api.sent_payloads)


def test_access_decisions_emit_reason_coded_audit_events(tmp_path: Path) -> None:
    deny_api = _FakeAPI([[_private_message_update(1, "hello", user_id=111)]])
    deny_audit = _AuditCollector()
    deny_runner, _ = _runner(
        deny_api,
        tmp_path / "deny-state.db",
        access=AccessConfig(dm_policy="allowlist", allow_from_user_ids=[]),
        pairing=PairingConfig(enabled=False),
        audit_logger=deny_audit,
    )
    deny_runner.run_once()
    assert any(
        event == "cp.access.deny" and data.get("reason") == "dm_allowlist_miss"
        for event, data in deny_audit.events
    )

    allow_api = _FakeAPI([[_private_message_update(1, "hello", user_id=111)]])
    allow_audit = _AuditCollector()
    allow_runner, _runtime = _runner(
        allow_api,
        tmp_path / "allow-state.db",
        access=AccessConfig(
            dm_policy="allow", group_policy="allow", mention_only_in_groups=False
        ),
        pairing=PairingConfig(enabled=False),
        audit_logger=allow_audit,
    )
    allow_runner.run_once()
    assert any(
        event == "cp.access.allow" and data.get("reason") == "ok"
        for event, data in allow_audit.events
    )
    assert any(
        event == "cp.route.runtime_dispatch"
        and data.get("reason") == "runtime_dispatch"
        for event, data in allow_audit.events
    )
    assert any(
        event == "cp.delivery.sent" and data.get("reason") == "delivery_ok"
        for event, data in allow_audit.events
    )


def test_runtime_failure_emits_typed_parity_fact_and_recovers_next_update(
    tmp_path: Path,
) -> None:
    api = _FakeAPI(
        [
            [
                _private_message_update(1, "fail-first", user_id=111),
                _private_message_update(2, "recover-second", user_id=111),
            ]
        ]
    )
    audit = _AuditCollector()
    runtime = _BrokenThenRecoveringRuntime()
    runner = _runner_with_runtime(
        api,
        tmp_path / "state.db",
        runtime,
        access=AccessConfig(
            dm_policy="allow", group_policy="allow", mention_only_in_groups=False
        ),
        audit_logger=audit,
    )

    processed = runner.run_once()

    assert processed == 2
    assert runtime.inputs == ["fail-first", "recover-second"]
    assert any(
        event == "cp.route.runtime_failed"
        and data.get("reason") == "runtime_dispatch_failed"
        and data.get("error_code") == "runtime_dispatch_failed"
        and data.get("error_type") == "RuntimeError"
        for event, data in audit.events
    )
    assert any(
        event == "cp.delivery.sent" and data.get("reason") == "delivery_ok"
        for event, data in audit.events
    )
    assert [payload["text"] for payload in api.sent_payloads] == ["echo:recover-second"]

    # The failed update is consumed structurally; it should not replay on the
    # next poll cycle just because the runtime raised once.
    assert runner.run_once() == 0


def test_scope_authorizer_blocks_unpaired_command_before_runtime(
    tmp_path: Path,
) -> None:
    api = _FakeAPI([[_private_message_update(1, "/help", user_id=111)]])
    auth_store = _AuthStore(pairing=None)
    runtime = _AuthRuntime(auth_store)
    runner = _runner_with_runtime(
        api,
        tmp_path / "state.db",
        runtime,
        access=AccessConfig(
            dm_policy="allow", group_policy="allow", mention_only_in_groups=False
        ),
        pairing=PairingConfig(enabled=False),
    )

    runner.run_once()

    assert runtime.inputs == []
    assert auth_store.lookups == [("telegram", "111")]
    assert any("not paired" in payload["text"] for payload in api.sent_payloads)


def test_scope_authorizer_blocks_command_with_missing_scopes(tmp_path: Path) -> None:
    api = _FakeAPI([[_private_message_update(1, "/new", user_id=111)]])
    auth_store = _AuthStore(
        pairing={
            "pairing_id": "pair-1",
            "scopes": ["chat.interact"],
        }
    )
    runtime = _AuthRuntime(auth_store)
    runner = _runner_with_runtime(
        api,
        tmp_path / "state.db",
        runtime,
        access=AccessConfig(
            dm_policy="allow", group_policy="allow", mention_only_in_groups=False
        ),
        pairing=PairingConfig(enabled=False),
    )

    runner.run_once()

    assert runtime.inputs == []
    assert auth_store.lookups == [("telegram", "111")]
    assert any("Permission denied" in payload["text"] for payload in api.sent_payloads)


def test_pair_local_command_reports_status_for_paired_chat(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    store = TelegramPollStateStore(str(db_path))
    issued = store.issue_pair_token(
        token="pairToken_pair_cmd",
        token_ttl_seconds=600,
        scopes=["chat.interact"],
        expected_user_id=111,
        expected_chat_id=111,
        hash_pepper=None,
    )
    store.close()

    api = _FakeAPI(
        [
            [_private_message_update(1, f"/start {issued.token}", user_id=111)],
            [_private_message_update(2, "/pair", user_id=111)],
        ]
    )
    runtime = _FakeRuntime()
    runtime.store = _PairingAuthStore()  # type: ignore[attr-defined]
    runner, runtime = _runner(
        api,
        db_path,
        access=AccessConfig(dm_policy="allowlist", allow_from_user_ids=[]),
        pairing=PairingConfig(enabled=True),
        runtime=runtime,
    )

    runner.run_once()
    runner.run_once()

    assert runtime.inputs == []
    assert any(payload["text"].startswith("Paired ✅") for payload in api.sent_payloads)


def test_diag_local_command_returns_adapter_status(tmp_path: Path) -> None:
    api = _FakeAPI([[_private_message_update(1, "/diag", user_id=111)]])
    runner, runtime = _runner(
        api,
        tmp_path / "state.db",
        access=AccessConfig(
            dm_policy="allow", group_policy="allow", mention_only_in_groups=False
        ),
        pairing=PairingConfig(enabled=False),
    )

    runner.run_once()

    assert runtime.inputs == []
    assert any(
        "telegram adapter diag" in payload["text"] for payload in api.sent_payloads
    )


def test_run_forever_uses_configured_backoff_on_errors(tmp_path: Path) -> None:
    class _FlakyAPI(_FakeAPI):
        def __init__(self) -> None:
            super().__init__([[]])
            self._failed = False

        def get_updates(
            self,
            *,
            offset: int | None,
            timeout: int,
            limit: int,
            allowed_updates: list[str],
        ) -> list[dict[str, Any]]:
            if not self._failed:
                self._failed = True
                raise RuntimeError("temporary transport error")
            return super().get_updates(
                offset=offset,
                timeout=timeout,
                limit=limit,
                allowed_updates=allowed_updates,
            )

    api = _FlakyAPI()
    sleep_calls: list[float] = []
    stop = threading.Event()

    def _sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        stop.set()

    runner, _ = _runner(
        api,
        tmp_path / "state.db",
        sleep_fn=_sleep,
        access=AccessConfig(
            dm_policy="allow", group_policy="allow", mention_only_in_groups=False
        ),
        polling=PollingConfig(
            timeout_seconds=1,
            limit=100,
            backoff_seconds=[2, 4, 8],
            persist_offset=True,
            drop_pending_on_start=False,
        ),
    )

    runner.run_forever(stop_event=stop)
    assert sleep_calls == [2.0]
