from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path

from openminion.modules.brain.runtime.verification.policy import VerifierResult
from openminion.modules.brain.schemas import (
    Deliverable,
    Goal,
    SuccessCriterion,
)
from openminion.modules.storage.runtime.migrations import migrate_database
from openminion.modules.storage.runtime.session_store import SessionStore
from openminion.modules.storage.runtime.sqlite import connect_database
from openminion.services.gateway.turn import GatewayTurnRunner
from openminion.services.runtime.run_status import (
    RUN_CHECKPOINT_EVENT_TYPE,
    RUN_STATE_COMPLETED,
    RUN_TERMINAL_COMPLETED,
    Run,
)
from openminion.services.runtime.verifier_binding import (
    TERMINAL_STATE_PROVENANCE_FIELD,
    TERMINAL_STATE_PROVENANCE_TYPED,
    append_run_state_event,
)


def _make_runner(
    sessions: SessionStore,
    *,
    typed_terminal_resolver=None,
) -> GatewayTurnRunner:

    def emit_run_state(
        *,
        session_id: str,
        run_id: str,
        state: str,
        current_step: str,
        payload=None,
    ) -> None:
        append_run_state_event(
            sessions,
            session_id=session_id,
            run_id=run_id,
            state=state,
            current_step=current_step,
            payload=payload,
        )

    return GatewayTurnRunner(
        agent=None,  # type: ignore[arg-type]
        agent_memory=None,
        channels=None,  # type: ignore[arg-type]
        logger=logging.getLogger("alvb-test"),
        sessions=sessions,
        session_context=None,  # type: ignore[arg-type]
        security=None,  # type: ignore[arg-type]
        agent_id="agent-1",
        history_limit=10,
        memory_capsule_strategy="none",
        memory_capsule_cache={},
        memory_dynamic_retrieval_enabled=False,
        emit_run_state=emit_run_state,
        typed_terminal_resolver=typed_terminal_resolver,
    )


def _goal() -> Goal:
    return Goal(
        goal_id="g-alvb",
        description="alvb-test-goal",
        success_criteria=[
            SuccessCriterion(
                criterion_id="c1",
                description="x",
                structural_check="artifact_present",
            ),
        ],
        deliverables=[
            Deliverable(
                deliverable_id="d1",
                description="x",
                verification_hint="artifact_presence",
            ),
        ],
    )


def _passing(target_id: str) -> VerifierResult:
    return VerifierResult(
        family="structural",
        goal_id="g-alvb",
        run_id="r-alvb",
        target_id=target_id,
        passed=True,
        reasons=[],
    )


class TypedTerminalDispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        db_path = Path(self._tmp.name) / "state" / "openminion.db"
        migrate_database(db_path)
        self._connection = connect_database(db_path)
        self.sessions = SessionStore(self._connection)
        self.session = self.sessions.resolve_session(
            agent_id="agent-1",
            channel="console",
            target="alvb-terminal-dispatch",
        )

    def tearDown(self) -> None:
        self._connection.close()
        self._tmp.cleanup()

    def _terminal_events(self) -> list[str]:
        events = self.sessions.list_events(
            session_id=self.session.id,
            limit=50,
            newest_first=False,
        )
        return [e.event_type for e in events]

    def test_resolver_absent_falls_through_to_legacy_emission(self) -> None:
        runner = _make_runner(self.sessions, typed_terminal_resolver=None)
        runner._emit_terminal_run_state(
            session_id=self.session.id,
            run_id="r-alvb",
            legacy_state=RUN_STATE_COMPLETED,
            current_step="turn.completed",
            payload={"channel": "console"},
        )
        types = self._terminal_events()
        self.assertIn(f"run.{RUN_STATE_COMPLETED}", types)
        self.assertNotIn(RUN_CHECKPOINT_EVENT_TYPE, types)

    def test_resolver_returns_typed_tuple_routes_through_bind(self) -> None:
        goal = _goal()

        def resolver(*, run_id: str, session_id: str, legacy_state: str):
            self.assertEqual(run_id, "r-alvb")
            self.assertEqual(session_id, self.session.id)
            self.assertEqual(legacy_state, RUN_STATE_COMPLETED)
            run = Run(
                run_id=run_id,
                session_id=session_id,
                goal_id="g-alvb",
                state="running",
            )
            return (run, goal, [_passing("c1"), _passing("d1")], [])

        runner = _make_runner(self.sessions, typed_terminal_resolver=resolver)
        runner._emit_terminal_run_state(
            session_id=self.session.id,
            run_id="r-alvb",
            legacy_state=RUN_STATE_COMPLETED,
            current_step="turn.completed",
            payload={"channel": "console"},
        )

        types = self._terminal_events()
        self.assertIn(RUN_CHECKPOINT_EVENT_TYPE, types)
        self.assertIn(f"run.{RUN_STATE_COMPLETED}", types)
        cp_idx = types.index(RUN_CHECKPOINT_EVENT_TYPE)
        run_idx = types.index(f"run.{RUN_STATE_COMPLETED}")
        self.assertLess(cp_idx, run_idx)

        events = self.sessions.list_events(
            session_id=self.session.id,
            limit=50,
            newest_first=False,
        )
        run_event = events[run_idx]
        self.assertEqual(
            run_event.payload[TERMINAL_STATE_PROVENANCE_FIELD],
            TERMINAL_STATE_PROVENANCE_TYPED,
        )
        self.assertEqual(run_event.payload["terminal_state"], RUN_TERMINAL_COMPLETED)

    def test_resolver_returns_none_falls_through_to_legacy(self) -> None:
        def resolver(*, run_id: str, session_id: str, legacy_state: str):
            return None

        runner = _make_runner(self.sessions, typed_terminal_resolver=resolver)
        runner._emit_terminal_run_state(
            session_id=self.session.id,
            run_id="r-alvb",
            legacy_state=RUN_STATE_COMPLETED,
            current_step="turn.completed",
            payload={},
        )
        types = self._terminal_events()
        self.assertIn(f"run.{RUN_STATE_COMPLETED}", types)
        self.assertNotIn(RUN_CHECKPOINT_EVENT_TYPE, types)

    def test_resolver_raising_falls_through_to_legacy(self) -> None:
        def resolver(*, run_id: str, session_id: str, legacy_state: str):
            raise RuntimeError("synthetic resolver failure")

        runner = _make_runner(self.sessions, typed_terminal_resolver=resolver)
        runner._emit_terminal_run_state(
            session_id=self.session.id,
            run_id="r-alvb",
            legacy_state=RUN_STATE_COMPLETED,
            current_step="turn.completed",
            payload={},
        )
        types = self._terminal_events()
        self.assertIn(f"run.{RUN_STATE_COMPLETED}", types)
        self.assertNotIn(RUN_CHECKPOINT_EVENT_TYPE, types)

    def test_resolver_returns_non_run_falls_through_to_legacy(self) -> None:
        def resolver(*, run_id: str, session_id: str, legacy_state: str):
            return ("not-a-run", _goal(), [], [])  # malformed 4-tuple

        runner = _make_runner(self.sessions, typed_terminal_resolver=resolver)
        runner._emit_terminal_run_state(
            session_id=self.session.id,
            run_id="r-alvb",
            legacy_state=RUN_STATE_COMPLETED,
            current_step="turn.completed",
            payload={},
        )
        types = self._terminal_events()
        self.assertIn(f"run.{RUN_STATE_COMPLETED}", types)
        self.assertNotIn(RUN_CHECKPOINT_EVENT_TYPE, types)


class ChokePointStructuralPropertyTests(unittest.TestCase):
    def test_terminal_seams_route_through_choke_point(self) -> None:
        from openminion.services.gateway import turn as turn_module

        source = Path(turn_module.__file__).read_text()
        terminal_seam_signatures = (
            "legacy_state=RUN_STATE_COMPLETED",
            "legacy_state=RUN_STATE_FAILED",
        )
        for signature in terminal_seam_signatures:
            self.assertIn(
                signature,
                source,
                f"Expected choke-point signature {signature!r} in turn/__init__.py",
            )
