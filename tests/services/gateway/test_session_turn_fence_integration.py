from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest

from openminion.modules.storage.runtime.session_store import SessionStore
from openminion.modules.storage.runtime.session_store.turn_leases import (
    RuntimeSessionTurnFenceError,
)
from openminion.modules.storage.runtime.sqlite import connect_database

from tests.services.gateway._gateway_service_support import GatewayServiceTestCase


class GatewaySessionTurnFenceIntegrationTests(GatewayServiceTestCase):
    def _install_takeover_before_send(self, *, session_id: str) -> None:
        original = self.gateway._turn_runner._renew_session_turn_lease

        def _takeover(*, session_id: str, owner: str, fence_token: int | None) -> None:
            assert fence_token is not None
            assert self.sessions.release_session_turn_lease(
                session_id,
                owner=owner,
                fence_token=fence_token,
            )
            self.sessions.acquire_session_turn_lease(
                session_id,
                owner="takeover",
                request_id="takeover",
                ttl_s=60,
            )
            original(
                session_id=session_id,
                owner=owner,
                fence_token=fence_token,
            )

        self.gateway._turn_runner._renew_session_turn_lease = _takeover

    def test_takeover_before_normal_send_aborts_delivery_and_terminal_write(
        self,
    ) -> None:
        session_id = "turn-fence-normal"
        self._install_takeover_before_send(session_id=session_id)

        with pytest.raises(RuntimeSessionTurnFenceError):
            asyncio.run(
                self.gateway.run_once(
                    channel="console",
                    target="local-user",
                    message="hello",
                    session_id=session_id,
                )
            )

        assert self.channel.sent == []
        events = self.sessions.list_events(session_id=session_id, limit=100)
        event_types = [event.event_type for event in events]
        assert "response.persisted" in event_types
        assert "response.delivered" not in event_types
        assert "run.completed" not in event_types
        assert "run.failed" not in event_types

    def test_takeover_before_replay_send_aborts_delivery(self) -> None:
        session_id = "turn-fence-replay"
        asyncio.run(
            self.gateway.run_once(
                channel="console",
                target="local-user",
                message="hello",
                session_id=session_id,
                deliver=False,
            )
        )
        self._install_takeover_before_send(session_id=session_id)

        with pytest.raises(RuntimeSessionTurnFenceError):
            asyncio.run(
                self.gateway.run_once(
                    channel="console",
                    target="local-user",
                    message="replay",
                    session_id=session_id,
                    deliver=True,
                )
            )

        assert self.channel.sent == []


@pytest.mark.parametrize("explicit", [False, True])
def test_concurrent_first_use_returns_one_session(tmp_path, explicit: bool) -> None:
    database_path = tmp_path / "state.db"
    from openminion.modules.storage.runtime.migrations import migrate_database

    migrate_database(database_path)

    def _resolve() -> str:
        connection = connect_database(database_path)
        try:
            store = SessionStore(connection)
            session = store.resolve_session(
                agent_id="main",
                channel="console",
                target="user",
                session_id="explicit-race" if explicit else None,
            )
            return session.id
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        session_ids = list(executor.map(lambda _index: _resolve(), range(2)))

    assert len(set(session_ids)) == 1
