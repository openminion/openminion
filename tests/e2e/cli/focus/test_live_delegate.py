from __future__ import annotations

import sqlite3
import time

import pytest

from tests.e2e.cli.focus.conftest import require_live_focus
from tests.e2e.cli.focus.harness import FocusProbe
from tests.e2e.cli.focus.harness.artifacts import artifact_root, write_transcript

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(360)]


def _wait_for_a2a_delegate_success(
    root, scenario: str, *, timeout: float = 60.0
) -> None:
    audit_dir = root / "data" / scenario / "a2a" / "audit"
    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        db_paths = sorted(audit_dir.glob("*.db"))
        for db_path in db_paths:
            try:
                with sqlite3.connect(db_path) as conn:
                    count = conn.execute(
                        """
                        SELECT COUNT(*)
                        FROM audit_records
                        WHERE type = 'result'
                          AND method = 'delegate'
                          AND status = 'SUCCESS'
                        """
                    ).fetchone()[0]
            except sqlite3.Error as exc:
                last_error = str(exc)
                continue
            if count:
                return
        time.sleep(0.1)
    raise AssertionError(
        "timed out waiting for delegated child SUCCESS audit row"
        f" under {audit_dir}; last_error={last_error}"
    )


def test_live_focus_delegate_exact_target(
    focus_probe: FocusProbe,
    tmp_path,
) -> None:
    """Prove Focus can delegate to a named configured MiniMax target."""
    require_live_focus()
    marker = "MAER exact target OK"
    target_agent = "minimax-m2-7-highspeed"
    with focus_probe.session() as session:
        focus_probe.wait_ready(session)
        transcript = focus_probe.run_slash(
            session,
            f"/delegate {target_agent} Reply exactly: {marker}",
            marker="Delegation:",
        )
        write_transcript(
            artifact_root(tmp_path),
            "maer-live-delegate-exact-target",
            transcript,
        )
    assert "Delegation:" in transcript
    assert target_agent in transcript


def test_live_focus_delegate_code_child_writes_in_scratch(
    focus_probe: FocusProbe,
    tmp_path,
) -> None:
    """Exercise a live code-bearing child turn without touching the repo."""
    require_live_focus()
    marker = "MAER_CODE_CHILD_OK"
    target_agent = "minimax-m2-7-highspeed"
    root = artifact_root(tmp_path)
    scratch_dir = root / "scratch" / "maer-live-code-child"
    scratch_dir.mkdir(parents=True, exist_ok=True)
    active_probe = focus_probe.for_workdir(
        scratch_dir,
        include_project_context=False,
    )
    with active_probe.session(rows=50, cols=160) as session:
        active_probe.wait_ready(session)
        transcript = active_probe.run_slash_turn(
            session,
            (
                f"/delegate {target_agent} In the current directory, create "
                "`maer_child_marker.txt` containing exactly "
                f"`{marker}`. Use file.write for the file, then read it back "
                f"and reply with `{marker}`."
            ),
            marker=None,
            timeout=600,
            requires_approval=True,
            max_auto_approvals=8,
            approval_reply="session",
        )
        write_transcript(root, "maer-live-code-child", transcript)
        _wait_for_a2a_delegate_success(
            root,
            "test_live_focus_delegate_code_child_writes_in_scratch",
        )

    marker_file = scratch_dir / "maer_child_marker.txt"
    assert marker_file.read_text(encoding="utf-8").strip() == marker
