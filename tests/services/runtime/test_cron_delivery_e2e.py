from __future__ import annotations

from types import SimpleNamespace

from openminion.modules.storage.runtime.migrations import migrate_database
from openminion.modules.storage.runtime.session_store import SessionStore
from openminion.modules.storage.runtime.sqlite import connect_database
from openminion.services.runtime.cron.delivery import CronDeliveryBridge


def test_cron_announce_delivery_writes_session_message_and_event(tmp_path) -> None:
    db_path = tmp_path / "state" / "openminion.db"
    migrate_database(db_path)
    connection = connect_database(db_path)
    sessions = SessionStore(connection)
    session = sessions.resolve_session(
        agent_id="agent-default",
        channel="telegram",
        target="controlplane",
        session_id="cron-session",
    )
    bridge = CronDeliveryBridge(runtime=SimpleNamespace(sessions=sessions))

    bridge.deliver(
        "announce",
        "last",
        {
            "job_id": "cron-weather",
            "payload": {
                "_openminion_origin": {
                    "session_id": session.id,
                    "channel": "telegram",
                    "conversation_id": "chat-1",
                    "thread_id": "topic-1",
                }
            },
        },
        {"run_id": "run-1", "due_at": "2026-08-07T00:00:00+00:00"},
        {"summary": "Scheduled weather check complete."},
    )

    turns = sessions.list_messages(session_id=session.id)
    events = sessions.list_events(session_id=session.id, event_type_prefix="cron.")

    assert turns[-1].role == "outbound"
    assert turns[-1].body == "Scheduled weather check complete."
    assert turns[-1].metadata["cron_job_id"] == "cron-weather"
    assert turns[-1].metadata["origin_channel"] == "telegram"
    assert events[-1].payload["summary"] == "Scheduled weather check complete."
    assert events[-1].payload["cron_run_id"] == "run-1"


def test_cron_announce_without_route_is_best_effort(tmp_path) -> None:
    db_path = tmp_path / "state" / "openminion.db"
    migrate_database(db_path)
    connection = connect_database(db_path)
    sessions = SessionStore(connection)
    session = sessions.resolve_session(
        agent_id="agent-default",
        channel="telegram",
        target="controlplane",
        session_id="cron-session",
    )
    bridge = CronDeliveryBridge(runtime=SimpleNamespace(sessions=sessions))

    bridge.deliver(
        "announce",
        "last",
        {"job_id": "cron-unroutable", "payload": {}},
        {"run_id": "run-2"},
        {"summary": "No origin route."},
    )

    assert sessions.list_messages(session_id=session.id) == []
    assert sessions.list_events(session_id=session.id, event_type_prefix="cron.") == []
