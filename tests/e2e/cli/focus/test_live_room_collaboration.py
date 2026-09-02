from __future__ import annotations

import asyncio
from dataclasses import asdict
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time

import pytest

from openminion.modules.storage.runtime.session_store import SessionStore
from openminion.modules.telemetry.service import TelemetryService
from tests.e2e.cli.focus.conftest import require_live_focus
from tests.e2e.cli.focus.harness import FocusProbe
from tests.e2e.cli.focus.harness.artifacts import artifact_root, write_transcript
from tests.e2e.cli.focus.harness.probe import (
    latest_done_after_submission,
    latest_terminal_failure,
)

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(900)]

_PRIMARY = "minimax-m2-7"
_SECONDARY = "minimax-m2-7-highspeed"


def _source_status(probe: FocusProbe) -> str:
    result = subprocess.run(
        ("git", "status", "--porcelain=v1", "--untracked-files=all"),
        cwd=probe.openminion_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _create_room(probe: FocusProbe, root: Path) -> str:
    environment = {**os.environ, **probe.environment()}
    result = subprocess.run(
        (
            str(probe.python_bin),
            "-m",
            "openminion",
            "--home-root",
            environment["OPENMINION_HOME"],
            "--data-root",
            str(probe.data_root),
            "--config",
            str(probe.config_path),
            "room",
            "create",
            "--name",
            "RHAC live",
            "--human",
            "owner-local",
            "--agent",
            _PRIMARY,
            "--agent",
            _SECONDARY,
            "--channel",
            "console",
            "--target",
            "focus",
        ),
        cwd=probe.openminion_root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    (root / "room-create.txt").write_text(result.stdout, encoding="utf-8")
    room_lines = [
        line.removeprefix("room=")
        for line in result.stdout.splitlines()
        if line.startswith("room=")
    ]
    assert len(room_lines) == 1
    return room_lines[0]


def _session_snapshot(data_root: Path, session_id: str) -> dict[str, object]:
    db_path = data_root / "state" / "openminion.db"
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        store = SessionStore(connection)
        session = store.get_session(session_id)
        assert session is not None
        participants = store.list_participants(session_id)
        messages = store.list_messages(session_id=session_id, limit=1000)
        return {
            "database_path": str(db_path),
            "session": asdict(session),
            "participants": [asdict(item) for item in participants],
            "messages": [asdict(item) for item in messages],
        }
    finally:
        connection.close()


def _telemetry_snapshot(probe: FocusProbe, session_id: str) -> dict[str, object]:
    environment = probe.environment()
    service = TelemetryService(
        home_root=environment["OPENMINION_HOME"],
        env=environment,
        read_only=True,
    )
    try:
        summary = asyncio.run(service.get_session_summary(session_id))
        return {
            "database_path": str(probe.data_root / "telemetry" / "telemetry.db"),
            "summary": asdict(summary),
        }
    finally:
        service.close_sync()


def _outbound_messages(snapshot: dict[str, object]) -> list[dict[str, object]]:
    messages = snapshot["messages"]
    assert isinstance(messages, list)
    return [
        item
        for item in messages
        if isinstance(item, dict) and item.get("role") == "outbound"
    ]


def _response_body(item: dict[str, object]) -> str:
    metadata = item["metadata"]
    assert isinstance(metadata, dict)
    participant_id = str(metadata.get("participant_id", "") or "")
    body = str(item.get("body", "") or "").strip()
    return body.removeprefix(f"{participant_id}:").strip()


def _run_prompt(
    probe: FocusProbe,
    session,
    *,
    prompt: str,
) -> None:
    completion_probe = probe._submit_composer_line(session, prompt)
    event_offset = len(session.visible_transcript)
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        time.sleep(0.1)
        transcript = session.visible_transcript
        failure = latest_terminal_failure(transcript, offset=event_offset)
        if failure is not None:
            raise AssertionError(transcript[failure.start() :][-2000:])
        if latest_done_after_submission(transcript, completion_probe) is not None:
            return
    raise AssertionError(
        "timed out waiting for the current Focus turn to complete\n"
        f"{session.screen_text[-2000:]}"
    )


def _export_transcript(probe: FocusProbe, session_id: str, target: Path) -> None:
    environment = {**os.environ, **probe.environment()}
    subprocess.run(
        (
            str(probe.python_bin),
            "-m",
            "openminion",
            "--home-root",
            environment["OPENMINION_HOME"],
            "--data-root",
            str(probe.data_root),
            "--config",
            str(probe.config_path),
            "export",
            "transcript",
            "--session-id",
            session_id,
            "--format",
            "jsonl",
            "--output",
            str(target),
        ),
        cwd=probe.openminion_root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )


def test_live_room_collaboration(
    focus_probe: FocusProbe,
    tmp_path: Path,
) -> None:
    require_live_focus()
    root = artifact_root(tmp_path)
    source_before = _source_status(focus_probe)
    room_id = _create_room(focus_probe, root)
    probe = focus_probe.for_session(room_id)
    outbound_counts = [0]

    with probe.session() as session:
        probe.wait_ready(session)
        _run_prompt(
            probe,
            session,
            prompt=f"@{_PRIMARY} Reply exactly RHAC_PRIMARY_OK",
        )
        outbound_counts.append(
            len(_outbound_messages(_session_snapshot(probe.data_root, room_id)))
        )
        _run_prompt(
            probe,
            session,
            prompt=f"@{_SECONDARY} Reply exactly RHAC_SECONDARY_OK",
        )
        outbound_counts.append(
            len(_outbound_messages(_session_snapshot(probe.data_root, room_id)))
        )
        probe.run_slash(
            session,
            "/routing broadcast",
            marker="room routing: broadcast",
        )
        _run_prompt(
            probe,
            session,
            prompt="Reply exactly RHAC_BROADCAST_OK",
        )
        outbound_counts.append(
            len(_outbound_messages(_session_snapshot(probe.data_root, room_id)))
        )
        probe.run_slash(
            session,
            "/routing sequential",
            marker="room routing: sequential",
        )
        _run_prompt(
            probe,
            session,
            prompt=(
                "If the most recent assistant message contains RHAC_CHAIN_SEED, "
                "reply exactly RHAC_CHAIN_SEED RHAC_CHAIN_OK; otherwise reply "
                "exactly RHAC_CHAIN_SEED"
            ),
        )
        outbound_counts.append(
            len(_outbound_messages(_session_snapshot(probe.data_root, room_id)))
        )
        probe.run_slash(session, "/participants", marker="participants:")
        probe.run_slash(session, "/status", marker="Room: RHAC live")
        probe.run_slash(session, "/sessions", marker="RHAC live")
        write_transcript(root, "room-focus", session.transcript)

    session_snapshot = _session_snapshot(probe.data_root, room_id)
    telemetry_snapshot = _telemetry_snapshot(probe, room_id)
    _export_transcript(probe, room_id, root / "room-transcript.jsonl")
    messages = session_snapshot["messages"]
    assert isinstance(messages, list)
    inbound = [item for item in messages if item.get("role") == "inbound"]
    outbound = _outbound_messages(session_snapshot)
    response_groups = [outbound[0:1], outbound[1:2], outbound[2:4], outbound[4:6]]
    response_agents = [
        [str(item["metadata"].get("participant_id", "")) for item in group]
        for group in response_groups
    ]
    response_bodies = [
        [_response_body(item) for item in group] for group in response_groups
    ]
    telemetry_summary = telemetry_snapshot["summary"]
    assert isinstance(telemetry_summary, dict)
    telemetry_events = telemetry_summary["events"]
    assert isinstance(telemetry_events, list)
    telemetry_agents = [
        str(event.get("agent_id", "") or "")
        for event in telemetry_events
        if isinstance(event, dict)
        and event.get("event_type") == "agent.invocation.started"
    ]
    assertions = {
        "room_id": room_id,
        "inbound_count": len(inbound),
        "outbound_counts": outbound_counts,
        "routed_call_deltas": [
            later - earlier
            for earlier, later in zip(outbound_counts, outbound_counts[1:])
        ],
        "telemetry_invocation_agents": telemetry_agents,
        "response_agents": response_agents,
        "response_bodies": response_bodies,
    }

    assert len(inbound) == 4
    assert assertions["routed_call_deltas"] == [1, 1, 2, 2]
    assert response_agents == [
        [_PRIMARY],
        [_SECONDARY],
        [_PRIMARY, _SECONDARY],
        [_PRIMARY, _SECONDARY],
    ]
    assert response_bodies[0] == ["RHAC_PRIMARY_OK"]
    assert response_bodies[1] == ["RHAC_SECONDARY_OK"]
    assert response_bodies[2] == ["RHAC_BROADCAST_OK", "RHAC_BROADCAST_OK"]
    assert response_bodies[3] == [
        "RHAC_CHAIN_SEED",
        "RHAC_CHAIN_SEED RHAC_CHAIN_OK",
    ]
    assert telemetry_agents == [
        _PRIMARY,
        _SECONDARY,
        _PRIMARY,
        _SECONDARY,
        _PRIMARY,
        _SECONDARY,
    ]
    assert Path(str(session_snapshot["database_path"])).is_relative_to(probe.data_root)
    assert Path(str(telemetry_snapshot["database_path"])).is_relative_to(
        probe.data_root
    )
    assert _source_status(probe) == source_before

    (root / "room-session.json").write_text(
        json.dumps(session_snapshot, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (root / "room-telemetry.json").write_text(
        json.dumps(telemetry_snapshot, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (root / "room-assertions.json").write_text(
        json.dumps(assertions, indent=2, sort_keys=True),
        encoding="utf-8",
    )
