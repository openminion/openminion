import sqlite3

from openminion.modules.controlplane.channels.slack.state import SlackStateStore


def test_state_store_initializes_missing_database_parent(tmp_path) -> None:
    db_path = tmp_path / "new-controlplane" / "slack-state.db"

    store = SlackStateStore(db_path)
    store.close()

    assert db_path.exists()
    with sqlite3.connect(str(db_path)) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert {
        "slack_seen_events",
        "slack_workspace_installs",
        "slack_socket_diagnostics",
    }.issubset(tables)


def test_state_store_deduplicates_events_and_tracks_install(tmp_path) -> None:
    store = SlackStateStore(tmp_path / "slack-state.db")
    try:
        assert store.mark_event_seen("Ev1") is True
        assert store.mark_event_seen("Ev1") is False

        store.upsert_install(team_id="T1", bot_user_id="B1", bot_token_ref="env:SLACK")

        assert store.get_install("T1")["bot_user_id"] == "B1"
    finally:
        store.close()
