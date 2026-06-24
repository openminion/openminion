from __future__ import annotations

import logging

from openminion.modules.brain.adapters.context.bridges.artifact import (
    BridgeArtifactClient,
)


class _FailingArtifactCtl:
    def search(self, **_kwargs):
        raise RuntimeError("db exploded")


def test_bridge_client_logs_on_db_error(caplog) -> None:
    bridge = BridgeArtifactClient(backing_store=object())
    bridge._artifact_ctl = _FailingArtifactCtl()

    caplog.set_level(logging.WARNING)
    result = bridge.query_digests(
        session_id="sess-1",
        agent_id="agent-1",
        query="hello",
        limit=5,
    )

    assert result == []
    assert "artifact query_digests failed: db exploded" in caplog.text
