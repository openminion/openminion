from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

from openminion.modules.storage.runtime.session_store import SessionStore
from openminion.modules.storage.runtime.session_store.turn_leases import (
    RuntimeSessionTurnBusyError,
    RuntimeSessionTurnFenceError,
)
from openminion.modules.storage.runtime.sqlite import connect_database

from tests.services.gateway._gateway_service_support import GatewayServiceTestCase


class GatewaySessionTurnFenceIntegrationTests(GatewayServiceTestCase):
    def _install_takeover_before_send(
        self,
        *,
        session_id: str,
    ) -> tuple[Callable[..., None], dict[str, int]]:
        original = self.gateway._turn_runner._renew_session_turn_lease
        takeover_state: dict[str, int] = {}

        def _takeover(*, session_id: str, owner: str, fence_token: int | None) -> None:
            assert fence_token is not None
            assert self.sessions.release_session_turn_lease(
                session_id,
                owner=owner,
                fence_token=fence_token,
            )
            takeover = self.sessions.acquire_session_turn_lease(
                session_id,
                owner="takeover",
                request_id="takeover",
                ttl_s=60,
            )
            takeover_state["fence_token"] = takeover.fence_token
            original(
                session_id=session_id,
                owner=owner,
                fence_token=fence_token,
            )

        self.gateway._turn_runner._renew_session_turn_lease = _takeover
        return original, takeover_state

    def test_takeover_before_normal_send_aborts_delivery_and_terminal_write(
        self,
    ) -> None:
        session_id = "turn-fence-normal"
        original_renew, takeover_state = self._install_takeover_before_send(
            session_id=session_id
        )

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

        assert self.sessions.release_session_turn_lease(
            session_id,
            owner="takeover",
            fence_token=takeover_state["fence_token"],
        )
        self.gateway._turn_runner._renew_session_turn_lease = original_renew
        response = asyncio.run(
            self.gateway.run_once(
                channel="console",
                target="local-user",
                message="replacement",
                session_id=session_id,
            )
        )
        assert response.metadata["invocation_id"]
        assert len(self.channel.sent) == 1
        replacement_types = {
            event.event_type
            for event in self.sessions.list_events(session_id=session_id, limit=100)
        }
        assert "response.delivered" in replacement_types
        assert "run.completed" in replacement_types

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

    def test_busy_explicit_session_is_not_resumed_or_rewritten_before_lease(
        self,
    ) -> None:
        session_id = "turn-fence-existing-explicit"
        self.sessions.resolve_session(
            agent_id="main",
            channel="console",
            target="local-user",
            session_id=session_id,
            metadata={"original": "true"},
        )
        self.sessions.set_session_status(session_id=session_id, status="closed")

        def _reject_lease(*_args, **_kwargs):  # noqa: ANN002, ANN003
            current = self.sessions.get_session(session_id)
            assert current is not None
            assert current.status == "closed"
            assert current.metadata == {"original": "true"}
            raise RuntimeSessionTurnBusyError(session_id, retry_after_s=1)

        with (
            patch.object(
                self.sessions,
                "acquire_session_turn_lease",
                side_effect=_reject_lease,
            ),
            pytest.raises(RuntimeSessionTurnBusyError),
        ):
            asyncio.run(
                self.gateway.run_once(
                    channel="console",
                    target="local-user",
                    message="hello",
                    session_id=session_id,
                    inbound_metadata={"replacement": "blocked"},
                )
            )

        current = self.sessions.get_session(session_id)
        assert current is not None
        assert current.status == "closed"
        assert current.metadata == {"original": "true"}

    def test_busy_implicit_session_metadata_is_not_rewritten_before_lease(
        self,
    ) -> None:
        session = self.sessions.resolve_session(
            agent_id="main",
            channel="console",
            target="local-user",
            metadata={"original": "true"},
        )

        def _reject_lease(*_args, **_kwargs):  # noqa: ANN002, ANN003
            current = self.sessions.get_session(session.id)
            assert current is not None
            assert current.metadata == {"original": "true"}
            raise RuntimeSessionTurnBusyError(session.id, retry_after_s=1)

        with (
            patch.object(
                self.sessions,
                "acquire_session_turn_lease",
                side_effect=_reject_lease,
            ),
            pytest.raises(RuntimeSessionTurnBusyError),
        ):
            asyncio.run(
                self.gateway.run_once(
                    channel="console",
                    target="local-user",
                    message="hello",
                    inbound_metadata={"replacement": "blocked"},
                )
            )

        current = self.sessions.get_session(session.id)
        assert current is not None
        assert current.metadata == {"original": "true"}

    def test_interactive_loop_uses_fenced_delivery_path(self) -> None:
        session = self.sessions.resolve_session(
            agent_id="main",
            channel="console",
            target="local-user",
        )
        self._install_takeover_before_send(session_id=session.id)

        with (
            patch("builtins.input", return_value="hello"),
            pytest.raises(RuntimeSessionTurnFenceError),
        ):
            asyncio.run(
                self.gateway.run_loop(
                    channel="console",
                    target="local-user",
                    show_progress=False,
                )
            )

        assert self.channel.sent == []

    def test_run_state_fence_failures_are_never_swallowed(self) -> None:
        session_id = "turn-fence-run-state"
        self.sessions.resolve_session(
            agent_id="main",
            channel="console",
            target="local-user",
            session_id=session_id,
        )
        stale = self.sessions.acquire_session_turn_lease(
            session_id,
            owner="stale",
            request_id="stale",
            ttl_s=60,
        )
        assert self.sessions.release_session_turn_lease(
            session_id,
            owner=stale.owner,
            fence_token=stale.fence_token,
        )
        self.sessions.acquire_session_turn_lease(
            session_id,
            owner="winner",
            request_id="winner",
            ttl_s=60,
        )

        for state in ("queued", "running", "failed"):
            with pytest.raises(RuntimeSessionTurnFenceError):
                self.gateway._emit_run_state(
                    session_id=session_id,
                    run_id=f"run-{state}",
                    state=state,
                    current_step=f"turn.{state}",
                    session_turn_fence_token=stale.fence_token,
                )

        run_ids = {
            str(event.payload.get("run_id", ""))
            for event in self.sessions.list_events(session_id=session_id, limit=100)
        }
        assert not run_ids.intersection({"run-queued", "run-running", "run-failed"})

    def test_post_lease_setup_failure_releases_the_lease(self) -> None:
        session = self.sessions.resolve_session(
            agent_id="main",
            channel="console",
            target="post-lease-failure",
        )

        with (
            patch.object(
                self.sessions,
                "update_session_metadata",
                side_effect=RuntimeError("metadata unavailable"),
            ),
            pytest.raises(RuntimeError, match="metadata unavailable"),
        ):
            asyncio.run(
                self.gateway.run_once(
                    channel="console",
                    target="post-lease-failure",
                    message="hello",
                    inbound_metadata={"attach_id": "attach"},
                )
            )

        lease = self.sessions.acquire_session_turn_lease(
            session.id,
            owner="after-failure",
            request_id="after-failure",
        )
        assert lease.fence_token > 0


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
