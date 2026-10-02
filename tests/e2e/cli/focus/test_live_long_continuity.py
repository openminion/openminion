from __future__ import annotations

import json
import sqlite3

import pytest

from openminion.modules.storage.runtime.session_store import SessionStore
from tests.e2e.cli.focus.conftest import require_live_focus
from tests.e2e.cli.focus.harness import FocusProbe, FocusScenario
from tests.e2e.cli.focus.harness.assertions import assert_exact_reply, visible_text
from tests.e2e.cli.focus.harness.artifacts import artifact_root, write_transcript

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(600)]


def _set_small_summary_window(probe: FocusProbe) -> None:
    config = json.loads(probe.config_path.read_text(encoding="utf-8"))
    runtime = config.setdefault("runtime", {})
    runtime["session_keep_recent_messages"] = 2
    runtime["session_max_compact_per_turn"] = 100
    runtime["session_summary_max_chars"] = 256
    probe.config_path.write_text(json.dumps(config), encoding="utf-8")


def _compaction_event_count(probe: FocusProbe) -> int:
    database_path = probe.data_root / "state" / "openminion.db"
    connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        store = SessionStore(connection)
        return len(
            store.list_events(
                session_id=probe.session_id,
                event_type_prefix="session.context.compaction",
                limit=100,
            )
        )
    finally:
        connection.close()


def test_live_focus_recalls_early_marker_after_repeated_compaction(
    focus_probe: FocusProbe,
    tmp_path,
) -> None:
    require_live_focus()
    marker = "LCMC-FOCUS-ANCHOR-7F3A"
    probe = focus_probe.for_workdir(
        focus_probe.workdir,
        include_project_context=False,
    )
    _set_small_summary_window(probe)
    transcripts: list[str] = []

    prompts = (
        (
            f"Keep this exact session marker available: {marker}. "
            "Reply with exactly: ACK-ONE",
            "ACK-ONE",
        ),
        ("Continue this session and reply with exactly: ACK-TWO", "ACK-TWO"),
        ("Continue this session and reply with exactly: ACK-THREE", "ACK-THREE"),
        ("Continue this session and reply with exactly: ACK-FOUR", "ACK-FOUR"),
    )

    with probe.session() as session:
        probe.wait_ready(session)
        for index, (prompt, expected) in enumerate(prompts, start=1):
            transcript = probe.run_turn(
                session,
                FocusScenario(
                    scenario_id=f"long_continuity_{index}",
                    prompt=prompt,
                    expected_markers=(expected,),
                    timeout=240,
                    include_project_context=False,
                ),
            )
            assert_exact_reply(transcript, prompt, expected)
            transcripts.append(transcript)
            if index in {2, 4}:
                compacted = probe.run_slash(
                    session,
                    "/compact",
                    marker="(/compact:",
                )
                assert "compacted" in visible_text(compacted)
                transcripts.append(compacted)

        recall_prompt = "What is the exact session marker? Reply with only the marker."
        recalled = probe.run_turn(
            session,
            FocusScenario(
                scenario_id="long_continuity_recall",
                prompt=recall_prompt,
                expected_markers=(marker,),
                timeout=300,
                include_project_context=False,
            ),
        )
        assert_exact_reply(recalled, recall_prompt, marker)
        transcripts.append(recalled)
        context = probe.run_slash(session, "/context", marker="Context usage:")
        context_text = visible_text(context)
        assert "Context usage:" in context_text
        assert "compacted" in context_text
        transcripts.append(context)

    assert _compaction_event_count(probe) >= 2
    write_transcript(
        artifact_root(tmp_path),
        "live-long-continuity",
        "\n".join(transcripts),
    )
