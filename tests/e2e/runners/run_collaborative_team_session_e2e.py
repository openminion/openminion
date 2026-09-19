#!/usr/bin/env python3
"""Provider-free composed room handoff, worker, and handback proof."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Event

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
for path in (ROOT, SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from tests.helpers.runtime_roots import isolate_runtime_roots  # noqa: E402

if __name__ == "__main__":
    isolate_runtime_roots(prefix="openminion-collaborative-session-")

from openminion.base.generated_paths import resolve_generated_root  # noqa: E402
from openminion.modules.session.runtime.continuation import (  # noqa: E402
    SessionContinuationService,
)
from openminion.modules.session.schemas import (  # noqa: E402
    ContinuationError,
    RoomHandoffResultV1,
)
from openminion.modules.session.storage.sqlite_store import (  # noqa: E402
    SQLiteSessionStore,
)
from tests.cli.interactive.test_openminion_runtime import (  # noqa: E402
    _make_bound_room_runtime,
)


def main() -> int:
    with TemporaryDirectory(prefix="openminion-room-e2e-") as temp_dir:
        database_path = Path(temp_dir) / "sessions.db"
        store = SQLiteSessionStore(database_path)
        rt, focus, _actor = _make_bound_room_runtime()
        rt.session_continuation_store = store
        focus.room_invite_agent("beta")
        store.create_session(session_id=focus.session_id, initial_agent_id="alpha")
        store.append_event(
            focus.session_id,
            event_type="task_plan.declared",
            payload={
                "plan": {
                    "plan_id": "plan-e2e",
                    "objective": "Review one bounded change.",
                    "steps": [
                        {
                            "step_id": "review-step",
                            "description": "Review the bounded change.",
                            "status": "pending",
                        }
                    ],
                }
            },
        )

        service = SessionContinuationService(store)
        try:
            service.preview(focus.session_id, target_agent_id="beta")
        except ContinuationError as exc:
            generic_rejection = exc.code
        else:
            raise AssertionError("generic cross-agent continuation unexpectedly passed")

        preview = focus.preview_room_handoff(
            target_agent_id="beta",
            task_step_id="review-step",
        )
        target_session_id = str(preview["binding"]["target_session_id"])
        assert store.get_session(target_session_id) is None
        applied = focus.apply_room_handoff(preview)
        note_id = focus.create_room_peer_note("beta", "Check the exact diff.")
        assert len(store.get_recent_turns(target_session_id, 10)) == 1

        calls: list[dict[str, object]] = []

        def run_turn(**kwargs):  # noqa: ANN003, ANN202
            calls.append(dict(kwargs))
            return {
                "body": "Review passed.",
                "metadata": {
                    "delegation_result_summary": json.dumps(
                        {
                            "summary": "Review passed.",
                            "artifacts_produced": ["artifact:e2e-review"],
                            "status": "complete",
                        }
                    )
                },
            }

        rt.run_turn = run_turn  # type: ignore[attr-defined]
        result = asyncio.run(focus.start_room_task("review-step", cancel_event=Event()))
        handback = result["room_handback"]
        assert len(calls) == 1
        assert calls[0]["payload"]["session_id"] == target_session_id
        assert handback["result"]["status"] == "completed"
        report = focus.room_tasks_report()
        assert "owner: beta" in report and "handback: completed" in report

        persisted = RoomHandoffResultV1.model_validate(handback["result"])
        first_event_id = str(handback["event_id"])
        store.close()
        reopened = SQLiteSessionStore(database_path)
        repeated = SessionContinuationService(reopened).accept_room_handback(persisted)
        assert repeated.status == "already_accepted"
        assert repeated.event_id == first_event_id

        artifact = {
            "artifact_version": "collaborative-team-session-e2e.v1",
            "proof_mode": "provider_free",
            "room_session_id": focus.session_id,
            "target_session_id": target_session_id,
            "packet_id": applied["packet_id"],
            "peer_note_id": note_id,
            "handback_event_id": first_event_id,
            "generic_cross_agent_rejection": generic_rejection,
            "restart_status": repeated.status,
            "worker_calls": len(calls),
        }
        artifact_dir = resolve_generated_root(home_root=ROOT) / "collaboration-e2e"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = artifact_dir / "collaborative-team-session-e2e.json"
        artifact_path.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
        print(json.dumps({"artifact": str(artifact_path), **artifact}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
