from __future__ import annotations

import multiprocessing as mp
import time
from pathlib import Path
from queue import Empty

from openminion.modules.session.storage import SQLiteSessionStore


def _hold_session_turn_lease(
    db_path: str,
    session_id: str,
    acquired: mp.Event,
    results: mp.Queue,
) -> None:
    store = SQLiteSessionStore(Path(db_path))
    try:
        lease = store.acquire_session_turn_lease(
            session_id,
            owner="process-a",
            request_id="req-a",
            ttl_s=30,
        )
        acquired.set()
        time.sleep(0.5)
        turn_id = store.append_turn(
            session_id,
            "user",
            "process-a write",
            session_turn_fence_token=lease.fence_token,
        )
        store.release_session_turn_lease(
            session_id,
            owner=lease.owner,
            fence_token=lease.fence_token,
        )
        results.put(("holder", "ok", turn_id))
    except Exception as exc:  # pragma: no cover - child process diagnostic
        results.put(("holder", getattr(exc, "code", type(exc).__name__), str(exc)))
    finally:
        store.close()


def _try_competing_session_turn_lease(
    db_path: str,
    session_id: str,
    acquired: mp.Event,
    results: mp.Queue,
) -> None:
    store = SQLiteSessionStore(Path(db_path))
    try:
        if not acquired.wait(timeout=5):
            results.put(("competitor", "timeout", "holder did not acquire"))
            return
        store.acquire_session_turn_lease(
            session_id,
            owner="process-b",
            request_id="req-b",
            ttl_s=30,
        )
        results.put(("competitor", "unexpected-acquire", ""))
    except Exception as exc:  # pragma: no cover - child process diagnostic
        results.put(("competitor", getattr(exc, "code", type(exc).__name__), str(exc)))
    finally:
        store.close()


def test_two_process_sqlite_session_turn_lease_rejects_parallel_writers(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "session-turn-lease-process.db"
    store = SQLiteSessionStore(db_path)
    try:
        session_id = store.create_session(initial_agent_id="agent.main")
    finally:
        store.close()

    ctx = mp.get_context("spawn")
    acquired = ctx.Event()
    results = ctx.Queue()
    holder = ctx.Process(
        target=_hold_session_turn_lease,
        args=(str(db_path), session_id, acquired, results),
    )
    competitor = ctx.Process(
        target=_try_competing_session_turn_lease,
        args=(str(db_path), session_id, acquired, results),
    )

    holder.start()
    competitor.start()
    holder.join(timeout=10)
    competitor.join(timeout=10)
    try:
        if holder.is_alive():
            holder.terminate()
        if competitor.is_alive():
            competitor.terminate()
    finally:
        holder.join(timeout=2)
        competitor.join(timeout=2)

    assert holder.exitcode == 0
    assert competitor.exitcode == 0
    collected = []
    for _ in range(2):
        try:
            collected.append(results.get(timeout=2))
        except Empty:  # pragma: no cover - diagnostic assertion below
            break
    by_actor = {actor: (status, detail) for actor, status, detail in collected}
    assert by_actor["holder"][0] == "ok"
    assert by_actor["competitor"][0] == "SESSION_TURN_BUSY"

    reopened = SQLiteSessionStore(db_path)
    try:
        turns = reopened.get_recent_turns(session_id, limit_messages=5)
    finally:
        reopened.close()
    assert [turn["text"] for turn in turns] == ["process-a write"]
