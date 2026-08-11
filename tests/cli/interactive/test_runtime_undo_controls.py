from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from openminion.cli.interactive.runtime.controls import RuntimeControlsMixin
from openminion.modules.memory.interfaces import RecordOrder


class _MessageStore:
    def __init__(self, messages: list[object]) -> None:
        self.messages_by_session: dict[str, list[object]] = {"session-1": messages}
        self.appended: list[dict[str, Any]] = []

    def list_messages(self, *, session_id: str, limit: int = 100) -> list[object]:
        del limit
        return list(self.messages_by_session.get(session_id, []))

    def append_message(self, **kwargs: Any) -> object:
        self.appended.append(dict(kwargs))
        return SimpleNamespace(**kwargs)


class _UndoRuntime(RuntimeControlsMixin):
    def __init__(self, messages: list[object]) -> None:
        self._rt = SimpleNamespace(sessions=_MessageStore(messages))
        self._session_id = "session-1"
        self.bound: list[str] = []

    @property
    def is_bound(self) -> bool:
        return bool(self._session_id)

    @property
    def session_id(self) -> str:
        return self._session_id

    def create_new_session(self) -> str:
        self._session_id = "session-2"
        self._rt.sessions.messages_by_session.setdefault("session-2", [])
        return self._session_id

    def bind_session(self, session_id: str) -> None:
        self.bound.append(session_id)
        self._session_id = session_id


class _MemoryQueryProvider:
    def __init__(self) -> None:
        self.record_options: object | None = None
        self.candidate_args: dict[str, object] = {}

    def list_records(self, options: object) -> list[object]:
        self.record_options = options
        return [SimpleNamespace(id="m1", title="Remembered repo preference")]

    def list_candidates(
        self, *, session_id: str | None = None, limit: int | None = None
    ) -> list[object]:
        self.candidate_args = {"session_id": session_id, "limit": limit}
        return [SimpleNamespace(candidate_id="c1")]


class _MemoryRuntime(RuntimeControlsMixin):
    _memory_provider = None

    def __init__(self, provider: _MemoryQueryProvider) -> None:
        self._rt = SimpleNamespace(memory_queries=provider)
        self._session_id = "session-1"
        self._agent_id = "agent-1"

    @property
    def is_bound(self) -> bool:
        return True

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def agent_id(self) -> str:
        return self._agent_id


def _message(role: str, body: str) -> object:
    return SimpleNamespace(
        role=role,
        body=body,
        conversation_id="conversation-1",
        thread_id="thread-1",
        attach_id="",
        metadata={},
    )


def test_undo_empty_current_message_store_reports_no_action() -> None:
    runtime = _UndoRuntime([])

    assert runtime.undo_last_turn() == {
        "ok": False,
        "message": "(no undoable action)",
    }


def test_undo_rewinds_current_inbound_outbound_messages() -> None:
    runtime = _UndoRuntime(
        [
            _message("inbound", "turn one"),
            _message("outbound", "reply one"),
            _message("inbound", "turn two"),
            _message("outbound", "reply two"),
        ]
    )

    result = runtime.undo_last_turn()

    assert result["ok"] is True
    assert "rewound latest turn" in str(result["message"])
    assert runtime.bound == ["session-2"]
    assert [
        (item["role"], item["body"]) for item in runtime._rt.sessions.appended
    ] == [
        ("user", "turn one"),
        ("assistant", "reply one"),
    ]


def test_memory_report_uses_api_runtime_memory_queries() -> None:
    provider = _MemoryQueryProvider()
    runtime = _MemoryRuntime(provider)

    report = runtime.memory_report()

    assert "records     1" in report
    assert "candidates  1" in report
    assert "Remembered repo preference" in report
    assert getattr(provider.record_options, "scopes") == [
        "session:session-1",
        "agent:agent-1",
        "global:system",
    ]
    assert getattr(provider.record_options, "limit") == 200
    assert getattr(provider.record_options, "order_by") == RecordOrder.UPDATED_AT_DESC
    assert provider.candidate_args == {"session_id": "session-1", "limit": 50}
