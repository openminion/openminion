from __future__ import annotations

import sqlite3
import tempfile
import time
from pathlib import Path

from openminion.base.config import OpenMinionConfig
from openminion.modules.memory.config import from_base_config
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.base import ListQueryOptions
from openminion.modules.memory.storage.memory import InMemoryMemoryStore
from openminion.modules.storage.runtime.migrations import migrate_database
from openminion.modules.storage.runtime.session_store import SessionStore
from openminion.modules.storage.runtime.sqlite import connect_database
from openminion.services.agent.memory.gateway_adapter import MemoryServiceGatewayAdapter
from openminion.services.context.session import SessionContextService
from tests._csc_fixtures import _csc_install_default_agent


def _static_summary_structurer(summary_text: str, turn_count: int) -> dict[str, object]:
    del turn_count
    lowered = summary_text.lower()
    decisions: list[str] = []
    corrections: list[str] = []
    open_questions: list[str] = []
    topic_keywords: list[str] = []
    active_threads: list[dict[str, str]] = []
    if "pytest" in lowered:
        decisions.append("Use pytest for unit coverage.")
        topic_keywords.extend(["pytest", "testing"])
    if "fixture scope" in lowered or "wrong" in lowered:
        corrections.append("Fix the database fixture scope.")
        topic_keywords.append("fixtures")
    if "what remains open" in lowered or "remains open" in lowered:
        open_questions.append("Which integration checks belong in CI?")
        active_threads.append(
            {
                "topic": "CI integration checks",
                "status": "open",
                "next_step": "Decide which integration checks belong in CI.",
            }
        )
    return {
        "summary_text": summary_text,
        "decisions": decisions,
        "open_questions": open_questions,
        "corrections": corrections,
        "topic_keywords": topic_keywords,
        "active_threads": active_threads,
    }


def _make_session_context(
    *, token_budget: int = 0, chars_per_token: float = 4.0
) -> tuple[
    tempfile.TemporaryDirectory[str],
    sqlite3.Connection,
    SessionStore,
    SessionContextService,
]:
    tempdir = tempfile.TemporaryDirectory()
    db_path = Path(tempdir.name) / "state" / "openminion.db"
    migrate_database(db_path)
    connection = connect_database(db_path)
    store = SessionStore(connection)
    service = SessionContextService(
        store,
        keep_recent_messages=1,
        max_compact_per_turn=100,
        token_budget=token_budget,
        chars_per_token=chars_per_token,
    )
    return tempdir, connection, store, service


def _make_adapter(
    *,
    agent_id: str = "phase2-agent",
    session_summary_structurer=None,
    session_summary_max_chars: int = 80,
    session_summary_structurer_timeout_seconds: float = 5.0,
    token_budget: int = 0,
    chars_per_token: float = 4.0,
) -> tuple[
    tempfile.TemporaryDirectory[str],
    sqlite3.Connection,
    SessionStore,
    SessionContextService,
    InMemoryMemoryStore,
    MemoryService,
    MemoryServiceGatewayAdapter,
]:
    tempdir, connection, store, session_context = _make_session_context(
        token_budget=token_budget,
        chars_per_token=chars_per_token,
    )
    memory_store = InMemoryMemoryStore()
    memory_service = MemoryService(store=memory_store)
    adapter = MemoryServiceGatewayAdapter(
        memory_service,
        agent_id=agent_id,
        session_context=session_context,
        session_summary_max_chars=session_summary_max_chars,
        session_handoff_max_summaries=3,
        session_summary_structurer=session_summary_structurer,
        session_summary_structurer_timeout_seconds=session_summary_structurer_timeout_seconds,
    )
    return (
        tempdir,
        connection,
        store,
        session_context,
        memory_store,
        memory_service,
        adapter,
    )


def test_phase2_config_defaults_and_override() -> None:
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    cfg = from_base_config(
        base_config=config,
        home_root=Path("/tmp/openminion-home"),
        data_root=Path("/tmp/openminion-data"),
    )
    assert cfg.retention.session_summary_max_chars == 500
    assert cfg.retention.session_summary_checkpoint_message_interval == 2
    assert cfg.retention.summary_compression_age_days == 14
    assert cfg.retrieval.session_handoff_max_summaries == 5


def test_structure_rolling_summary_uses_structurer_when_available() -> None:
    (
        tempdir,
        connection,
        store,
        session_context,
        _memory_store,
        _memory_service,
        adapter,
    ) = _make_adapter(
        session_summary_structurer=_static_summary_structurer,
        session_summary_max_chars=200,
    )
    try:
        summary = adapter._structure_rolling_summary(  # noqa: SLF001
            "We decided to use pytest for coverage. No, wrong approach for smoke tests. "
            "What remains open? We are going with integration checks for CI.",
            turn_count=10,
        )
        assert summary["turn_count"] == 10
        assert summary["decisions"] == ["Use pytest for unit coverage."]
        assert summary["corrections"] == ["Fix the database fixture scope."]
        assert summary["open_questions"] == ["Which integration checks belong in CI?"]
        assert summary["topic_keywords"] == ["pytest", "testing", "fixtures"]
        assert summary["active_threads"] == [
            {
                "topic": "CI integration checks",
                "status": "open",
                "next_step": "Decide which integration checks belong in CI.",
            }
        ]
        assert len(summary["summary_text"]) <= 200
    finally:
        connection.close()
        tempdir.cleanup()


def test_structure_rolling_summary_empty_input_returns_safe_empty_shape() -> None:
    (
        tempdir,
        connection,
        store,
        session_context,
        _memory_store,
        _memory_service,
        adapter,
    ) = _make_adapter(session_summary_structurer=_static_summary_structurer)
    try:
        summary = adapter._structure_rolling_summary("", turn_count=0)  # noqa: SLF001
        assert summary == {
            "decisions": [],
            "open_questions": [],
            "corrections": [],
            "topic_keywords": [],
            "active_threads": [],
            "outcome": "unknown",
            "turn_count": 0,
            "summary_text": "",
        }
    finally:
        connection.close()
        tempdir.cleanup()


def test_structure_rolling_summary_fails_open_when_structurer_times_out() -> None:
    def _slow_structurer(summary_text: str, turn_count: int) -> dict[str, object]:
        del turn_count
        time.sleep(0.2)
        return {
            "summary_text": f"slow::{summary_text}",
            "decisions": ["should not be used"],
        }

    (
        tempdir,
        connection,
        store,
        session_context,
        _memory_store,
        _memory_service,
        adapter,
    ) = _make_adapter(
        session_summary_structurer=_slow_structurer,
        session_summary_max_chars=200,
        session_summary_structurer_timeout_seconds=0.01,
    )
    try:
        started_at = time.monotonic()
        summary = adapter._structure_rolling_summary(  # noqa: SLF001
            "Use the safe fallback summary if the structurer stalls.",
            turn_count=4,
        )
        elapsed = time.monotonic() - started_at
        assert elapsed < 0.1
        assert summary["summary_text"] == (
            "Use the safe fallback summary if the structurer stalls."
        )
        assert summary["decisions"] == []
        assert adapter._session_summary_structurer_disabled is True  # noqa: SLF001
    finally:
        connection.close()
        tempdir.cleanup()


def test_structure_rolling_summary_skips_structurer_for_short_turns() -> None:
    calls: list[tuple[str, int]] = []

    def _recording_structurer(summary_text: str, turn_count: int) -> dict[str, object]:
        calls.append((summary_text, turn_count))
        return {
            "summary_text": "should not run",
            "decisions": ["unexpected"],
        }

    (
        tempdir,
        connection,
        store,
        session_context,
        _memory_store,
        _memory_service,
        adapter,
    ) = _make_adapter(
        session_summary_structurer=_recording_structurer,
        session_summary_max_chars=200,
    )
    try:
        summary = adapter._structure_rolling_summary(  # noqa: SLF001
            "user: What is my email? assistant: new@example.com",
            turn_count=2,
        )
        assert calls == []
        assert summary["summary_text"] == (
            "user: What is my email? assistant: new@example.com"
        )
        assert summary["decisions"] == []
    finally:
        connection.close()
        tempdir.cleanup()


def test_write_session_summary_upserts_structured_agent_record() -> None:
    (
        tempdir,
        connection,
        store,
        session_context,
        memory_store,
        memory_service,
        adapter,
    ) = _make_adapter(session_summary_structurer=_static_summary_structurer)
    try:
        session = store.resolve_session(
            agent_id="phase2-agent", channel="console", target="chat"
        )
        context = store.ensure_session_context(session_id=session.id)
        store.update_session_context(
            session_id=session.id,
            rolling_summary=(
                "Decided to use pytest for unit coverage. "
                "Actually, wrong fixture scope for the database setup."
            ),
            summary_short="pytest choice",
            compacted_message_count=6,
            version=context.version + 1,
            expected_version=context.version,
        )

        first_id = adapter.write_session_summary(session.id)
        second_id = adapter.write_session_summary(session.id)

        assert first_id is not None
        assert second_id is not None
        records = memory_service.list(
            ListQueryOptions(
                scopes=["agent:phase2-agent"],
                types=["session_summary"],
                limit=10,
            )
        )
        assert len(records) == 1
        content = records[0].content
        assert isinstance(content, dict)
        assert content["turn_count"] == 6
        assert "pytest" in content["summary_text"].lower()
        assert content["decisions"] == ["Use pytest for unit coverage."]
        assert content["corrections"] == ["Fix the database fixture scope."]
        assert records[0].key == f"session_summary:{session.id}"
    finally:
        connection.close()
        tempdir.cleanup()


def test_write_session_summary_includes_uncompacted_recent_turns() -> None:
    (
        tempdir,
        connection,
        store,
        session_context,
        _memory_store,
        memory_service,
        adapter,
    ) = _make_adapter(
        session_summary_structurer=_static_summary_structurer,
        session_summary_max_chars=400,
    )
    try:
        session = store.resolve_session(
            agent_id="phase2-agent", channel="console", target="chat"
        )
        store.append_message(
            session_id=session.id,
            role="inbound",
            body="Decided to use pytest for unit coverage.",
        )
        store.append_message(
            session_id=session.id,
            role="outbound",
            body="Sounds good, let's use pytest for unit coverage.",
        )
        store.append_message(
            session_id=session.id,
            role="inbound",
            body="Keep the integration checks in CI.",
        )
        store.append_message(
            session_id=session.id,
            role="outbound",
            body="Okay, integration checks stay in CI.",
        )
        session_context.compact_session(session_id=session.id)
        store.append_message(
            session_id=session.id,
            role="inbound",
            body="Actually, wrong fixture scope for the database setup.",
        )
        store.append_message(
            session_id=session.id,
            role="outbound",
            body="Good catch, we should fix the database fixture scope.",
        )

        summary_id = adapter.write_session_summary(session.id)

        assert summary_id is not None
        records = memory_service.list(
            ListQueryOptions(
                scopes=["agent:phase2-agent"],
                types=["session_summary"],
                limit=10,
            )
        )
        assert len(records) == 1
        content = records[0].content
        assert isinstance(content, dict)
        assert content["turn_count"] == 6
        assert content["decisions"] == ["Use pytest for unit coverage."]
        assert content["corrections"] == ["Fix the database fixture scope."]
    finally:
        connection.close()
        tempdir.cleanup()


def test_maybe_checkpoint_session_summary_writes_after_first_completed_turn() -> None:
    (
        tempdir,
        connection,
        store,
        session_context,
        _memory_store,
        memory_service,
        adapter,
    ) = _make_adapter(session_summary_structurer=_static_summary_structurer)
    try:
        session = store.resolve_session(
            agent_id="phase2-agent", channel="console", target="chat"
        )
        store.append_message(
            session_id=session.id,
            role="inbound",
            body="Decided to use pytest for unit coverage.",
        )
        store.append_message(
            session_id=session.id,
            role="outbound",
            body="Sounds good, let's use pytest for unit coverage.",
        )

        summary_id = adapter.maybe_checkpoint_session_summary(session.id)

        assert summary_id is not None
        records = memory_service.list(
            ListQueryOptions(
                scopes=["agent:phase2-agent"],
                types=["session_summary"],
                limit=10,
            )
        )
        assert len(records) == 1
        content = records[0].content
        assert isinstance(content, dict)
        assert content["turn_count"] == 2
        assert "pytest" in content["summary_text"].lower()
    finally:
        connection.close()
        tempdir.cleanup()


def test_maybe_checkpoint_session_summary_skips_off_cadence() -> None:
    (
        tempdir,
        connection,
        store,
        session_context,
        _memory_store,
        memory_service,
        adapter,
    ) = _make_adapter(session_summary_structurer=_static_summary_structurer)
    try:
        session = store.resolve_session(
            agent_id="phase2-agent", channel="console", target="chat"
        )
        store.append_message(
            session_id=session.id,
            role="inbound",
            body="Decided to use pytest for unit coverage.",
        )

        summary_id = adapter.maybe_checkpoint_session_summary(session.id)

        assert summary_id is None
        records = memory_service.list(
            ListQueryOptions(
                scopes=["agent:phase2-agent"],
                types=["session_summary"],
                limit=10,
            )
        )
        assert records == []
    finally:
        connection.close()
        tempdir.cleanup()


def test_estimate_token_pressure_uses_session_context_budget() -> None:
    tempdir, connection, store, session_context = _make_session_context(
        token_budget=10,
        chars_per_token=2.0,
    )
    try:
        session = store.resolve_session(
            agent_id="phase2-agent", channel="console", target="chat"
        )
        store.append_message(session_id=session.id, role="inbound", body="abcdefghij")
        store.append_message(session_id=session.id, role="outbound", body="klmnopqrst")

        token_count, token_budget, pressure = session_context.estimate_token_pressure(
            session_id=session.id
        )

        assert token_count == 10
        assert token_budget == 10
        assert pressure == 1.0
    finally:
        connection.close()
        tempdir.cleanup()


def test_token_pressure_checkpoint_writes_once_for_current_turn_count() -> None:
    (
        tempdir,
        connection,
        store,
        _session_context,
        _memory_store,
        memory_service,
        adapter,
    ) = _make_adapter(session_summary_structurer=_static_summary_structurer)
    try:
        session = store.resolve_session(
            agent_id="phase2-agent", channel="console", target="chat"
        )
        store.append_message(
            session_id=session.id,
            role="inbound",
            body="Decided to use pytest for unit coverage.",
        )
        store.append_message(
            session_id=session.id,
            role="outbound",
            body="Sounds good, let's use pytest for unit coverage.",
        )

        first = adapter.maybe_checkpoint_session_summary_for_token_pressure(
            session.id,
            token_count=90,
            token_budget=100,
            pressure_threshold=0.85,
        )
        second = adapter.maybe_checkpoint_session_summary_for_token_pressure(
            session.id,
            token_count=95,
            token_budget=100,
            pressure_threshold=0.85,
        )

        assert first is not None
        assert second is None
        records = memory_service.list(
            ListQueryOptions(
                scopes=["agent:phase2-agent"],
                types=["session_summary"],
                limit=10,
            )
        )
        assert len(records) == 1
        assert records[0].content["turn_count"] == 2
    finally:
        connection.close()
        tempdir.cleanup()


def test_token_pressure_checkpoint_skips_below_threshold() -> None:
    (
        tempdir,
        connection,
        store,
        _session_context,
        _memory_store,
        memory_service,
        adapter,
    ) = _make_adapter(session_summary_structurer=_static_summary_structurer)
    try:
        session = store.resolve_session(
            agent_id="phase2-agent", channel="console", target="chat"
        )
        store.append_message(
            session_id=session.id,
            role="inbound",
            body="Decided to use pytest for unit coverage.",
        )
        store.append_message(
            session_id=session.id,
            role="outbound",
            body="Sounds good, let's use pytest for unit coverage.",
        )

        summary_id = adapter.maybe_checkpoint_session_summary_for_token_pressure(
            session.id,
            token_count=50,
            token_budget=100,
            pressure_threshold=0.85,
        )

        assert summary_id is None
        records = memory_service.list(
            ListQueryOptions(
                scopes=["agent:phase2-agent"],
                types=["session_summary"],
                limit=10,
            )
        )
        assert records == []
    finally:
        connection.close()
        tempdir.cleanup()


def test_on_session_close_writes_summary_and_empty_summary_is_noop() -> None:
    (
        tempdir,
        connection,
        store,
        session_context,
        memory_store,
        memory_service,
        adapter,
    ) = _make_adapter()
    try:
        session = store.resolve_session(
            agent_id="phase2-agent", channel="console", target="chat"
        )
        store.append_message(
            session_id=session.id, role="inbound", body="We decided to use pytest."
        )
        store.append_message(
            session_id=session.id, role="outbound", body="Sounds good."
        )
        store.append_message(
            session_id=session.id, role="inbound", body="Actually, wrong fixture scope."
        )
        session_context.on_session_close(session_id=session.id)

        records = memory_service.list(
            ListQueryOptions(
                scopes=["agent:phase2-agent"],
                types=["session_summary"],
                limit=10,
            )
        )
        assert len(records) == 1

        empty_session = store.resolve_session(
            agent_id="phase2-agent", channel="console", target="empty"
        )
        session_context.on_session_close(session_id=empty_session.id)
        records_after = memory_service.list(
            ListQueryOptions(
                scopes=["agent:phase2-agent"],
                types=["session_summary"],
                limit=10,
            )
        )
        assert len(records_after) == 1
    finally:
        connection.close()
        tempdir.cleanup()


def test_on_session_close_summarizes_short_sessions_below_compaction_window() -> None:
    (
        tempdir,
        connection,
        store,
        session_context,
        memory_store,
        memory_service,
        adapter,
    ) = _make_adapter()
    try:
        short_service = SessionContextService(
            store,
            keep_recent_messages=20,
            max_compact_per_turn=100,
        )
        MemoryServiceGatewayAdapter(
            memory_service,
            agent_id="phase2-agent",
            session_context=short_service,
        )
        session = store.resolve_session(
            agent_id="phase2-agent", channel="console", target="short"
        )
        store.append_message(
            session_id=session.id, role="inbound", body="We decided to use pytest."
        )
        store.append_message(
            session_id=session.id, role="outbound", body="Okay, pytest it is."
        )
        short_service.on_session_close(session_id=session.id)

        records = memory_service.list(
            ListQueryOptions(
                scopes=["agent:phase2-agent"],
                types=["session_summary"],
                limit=10,
            )
        )
        assert len(records) == 1
        content = records[0].content
        assert isinstance(content, dict)
        assert "pytest" in content["summary_text"].lower()
    finally:
        connection.close()
        tempdir.cleanup()


def test_session_open_preamble_is_first_turn_only_and_cached_per_session() -> None:
    (
        tempdir,
        connection,
        store,
        session_context,
        memory_store,
        memory_service,
        adapter,
    ) = _make_adapter()
    try:
        for label in ("a", "b", "c"):
            session = store.resolve_session(
                agent_id="phase2-agent", channel="console", target=label
            )
            context = store.ensure_session_context(session_id=session.id)
            store.update_session_context(
                session_id=session.id,
                rolling_summary=f"Decided to use pytest in session {label}.",
                summary_short=f"pytest {label}",
                compacted_message_count=4,
                version=context.version + 1,
                expected_version=context.version,
            )
            adapter.write_session_summary(session.id)

        fresh_a = store.resolve_session(
            agent_id="phase2-agent", channel="console", target="fresh-a"
        )
        first_context, _ = adapter.build_context_with_metadata(
            session_id=fresh_a.id,
            user_message="hello",
        )
        assert "Continuing from recent sessions" in first_context

        store.append_message(session_id=fresh_a.id, role="inbound", body="hello")
        second_context, _ = adapter.build_context_with_metadata(
            session_id=fresh_a.id,
            user_message="hello again",
        )
        assert "Continuing from recent sessions" not in second_context

        fresh_b = store.resolve_session(
            agent_id="phase2-agent", channel="console", target="fresh-b"
        )
        third_context, _ = adapter.build_context_with_metadata(
            session_id=fresh_b.id,
            user_message="hello",
        )
        assert "Continuing from recent sessions" in third_context

        busy_session = store.resolve_session(
            agent_id="phase2-agent", channel="console", target="busy"
        )
        store.append_message(
            session_id=busy_session.id, role="inbound", body="already started"
        )
        busy_context, _ = adapter.build_context_with_metadata(
            session_id=busy_session.id,
            user_message="follow up",
        )
        assert "Continuing from recent sessions" not in busy_context
    finally:
        connection.close()
        tempdir.cleanup()
