from openminion.modules.controlplane.channels.slack.state import SlackStateStore


def test_state_store_deduplicates_events_and_tracks_install(tmp_path) -> None:
    store = SlackStateStore(tmp_path / "slack-state.db")
    try:
        assert store.mark_event_seen("Ev1") is True
        assert store.mark_event_seen("Ev1") is False

        store.upsert_install(team_id="T1", bot_user_id="B1", bot_token_ref="env:SLACK")

        assert store.get_install("T1")["bot_user_id"] == "B1"
    finally:
        store.close()
