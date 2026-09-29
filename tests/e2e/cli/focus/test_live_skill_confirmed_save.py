from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import time

import pytest

from openminion.cli.config import load_cli_config
from openminion.modules.brain.paths import resolve_brain_sessions_db_path
from openminion.modules.session.storage.sqlite_store import SQLiteSessionStore
from openminion.modules.skill.config import from_base_config as skill_from_base_config
from openminion.modules.skill.runtime.skill import Skill
from tests.e2e.cli.focus.conftest import require_live_focus
from tests.e2e.cli.focus.harness import FocusProbe, FocusScenario
from tests.e2e.cli.focus.harness.artifacts import artifact_root, write_transcript
from tests.e2e.cli.focus.harness.assertions import (
    final_answer_text,
    read_focus_evidence,
)
from tests.e2e.cli.focus.harness.probe import active_approval_visible

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(600)]

_OWNED_TREE_PATHS = (
    "src/openminion/modules/skill/interfaces.py",
    "src/openminion/modules/skill/runtime/skill/catalog.py",
    "src/openminion/modules/skill/runtime/skill/ingest.py",
    "src/openminion/modules/tool/runtime/policy_defaults.py",
    "src/openminion/tools/skill/interfaces.py",
    "src/openminion/tools/skill/plugin.py",
    "src/openminion/tools/skill/registrar.py",
    "src/openminion/tools/skill/schemas.py",
    "tests/e2e/cli/focus/test_live_skill_confirmed_save.py",
    "tests/tools/skill/test_confirmed_save_flow.py",
    "tests/tools/skill/test_inspect_patterns.py",
    "tests/tools/skill/test_llm_skill_tools.py",
)

_SAVE_PROMPT = (
    "We just completed a reusable inspect-change-verify workflow. Use skill.ingest "
    "exactly once to save a current-agent skill named `Focus Confirmed Save` with "
    "a Procedure that inspects the target then makes the smallest safe change, and "
    "a Verification section that runs the focused check. Do not call any other tool. "
    "After saving, report skill_id, version_hash, admission_state, and "
    "active_version_hash."
)


def _skill_config(probe: FocusProbe):
    env = probe.environment()
    home_root = Path(env["OPENMINION_HOME"])
    base_config = load_cli_config(
        probe.config_path,
        home_root=home_root,
        data_root=probe.data_root,
    )
    config = skill_from_base_config(
        base_config=base_config,
        home_root=home_root,
        data_root=probe.data_root,
    )
    config.wal = False
    return config


def _version_rows(probe: FocusProbe) -> list[dict[str, str | None]]:
    config = _skill_config(probe)
    database = Path(config.sqlite_path).expanduser()
    if not database.exists():
        return []
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT sv.skill_id, sv.version_hash, sv.source_artifact_ref,
                   sva.content_fingerprint, sva.state,
                   s.active_version_hash
            FROM skill_versions sv
            JOIN skill_version_admissions sva
              ON sva.skill_id = sv.skill_id
             AND sva.version_hash = sv.version_hash
            JOIN skills s ON s.skill_id = sv.skill_id
            ORDER BY sv.created_at
            """
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def _admit(probe: FocusProbe, *, skill_id: str, version_hash: str) -> dict:
    completed = subprocess.run(
        [
            str(probe.python_bin),
            "-m",
            "openminion",
            "skill",
            "admit",
            "--skill-id",
            skill_id,
            "--version-hash",
            version_hash,
            "--expected-active-version-hash",
            "none",
            "--target-status",
            "verified",
            "--reason",
            "Focus confirmed-save E2E admission",
            "--verification-check",
            "Focus confirmed-save content review",
            "--verification-result",
            "passed",
            "--verification-evidence-ref",
            "test://focus-confirmed-save",
            "--config",
            str(probe.config_path),
        ],
        cwd=probe.openminion_root,
        env={**probe.environment()},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["active_version_hash"] == version_hash
    return payload


def _selected_skill_events(probe: FocusProbe) -> list[dict]:
    _events, _messages, brain_session_id = read_focus_evidence(
        probe.environment(), probe.session_id
    )
    store = SQLiteSessionStore(
        resolve_brain_sessions_db_path(
            storage_path=probe.data_root / "state" / "openminion.db"
        )
    )
    try:
        events = store.list_events(brain_session_id, limit=200)
    finally:
        store.close()
    return [event for event in events if event.get("type") == "skill.selected"]


def _write_summary(root: Path, name: str, payload: dict) -> None:
    (root / f"{name}.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
    )


def _normalized_step(value: str) -> str:
    without_number = re.sub(r"^\s*\**\d+(?:[.)]\s*|\s+)", "", value)
    return re.sub(r"[*_`]", "", without_number).strip().lower()


@pytest.mark.parametrize(
    "rendered",
    ("1 Inspect the target", "1. Inspect the target", "1) Inspect the target"),
)
def test_normalized_step_accepts_common_numbered_list_markers(rendered: str) -> None:
    assert _normalized_step(rendered) == "inspect the target"


def _tree_evidence(probe: FocusProbe) -> dict[str, str]:
    digest = sha256()
    for relative_path in _OWNED_TREE_PATHS:
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update((probe.openminion_root / relative_path).read_bytes())
        digest.update(b"\0")
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=probe.openminion_root,
        text=True,
        stdout=subprocess.PIPE,
        check=True,
    ).stdout.strip()
    return {"source_commit": commit, "owned_tree_sha256": digest.hexdigest()}


def test_live_confirmed_save_and_fresh_session_reuse(
    focus_probe: FocusProbe,
    tmp_path: Path,
) -> None:
    require_live_focus()
    root = artifact_root(tmp_path)
    scenario = FocusScenario(
        scenario_id="confirmed-save",
        prompt=_SAVE_PROMPT,
        expected_markers=("pending",),
        timeout=420,
        requires_approval=True,
        max_auto_approvals=1,
        approval_reply="yes",
        include_project_context=False,
    )
    save_probe = focus_probe.for_workdir(
        focus_probe.openminion_root, include_project_context=False
    )
    with save_probe.session(rows=52, cols=170) as session:
        save_probe.wait_ready(session)
        try:
            save_transcript = save_probe.run_turn(session, scenario)
        finally:
            write_transcript(root, "confirmed-save", session.visible_transcript)

    assert "openminion skill admit" in save_transcript

    rows = _version_rows(save_probe)
    assert len(rows) == 1, rows
    staged = rows[0]
    assert staged["state"] == "pending"
    assert staged["active_version_hash"] is None
    skill_id = str(staged["skill_id"])
    version_hash = str(staged["version_hash"])

    skill = Skill(_skill_config(save_probe))
    try:
        package = skill.get_skill(skill_id, version_hash)
        digest = package.source_artifact_ref.rsplit("/", 1)[-1]
        source = Path(skill._blob_store.path_for(digest)).read_bytes()
        assert sha256(source).hexdigest() == digest
        assert staged["content_fingerprint"] == package.to_content_fingerprint()
        assert skill.catalog_summaries(save_probe.agent_id) == []
        snippet, _snippet_hash = skill.render_snippet(
            skill_id=skill_id,
            version_hash=version_hash,
            purpose="act",
            max_tokens=500,
        )
        step_match = re.search(r"(?m)^\s*1\.\s+(.+)$", snippet)
        assert step_match is not None, snippet
        expected_step = step_match.group(1).strip()
    finally:
        skill.close()

    admitted = _admit(save_probe, skill_id=skill_id, version_hash=version_hash)
    reuse_probe = save_probe.for_session(f"{save_probe.session_id}-reuse")
    reuse_prompt = (
        f"Use the exact named skill `{skill_id}` for this request. Load it, then "
        "reply with only its first numbered procedure step. Do not modify files."
    )
    with reuse_probe.session(rows=52, cols=170) as session:
        reuse_probe.wait_ready(session)
        try:
            reuse_transcript = reuse_probe.run_turn(
                session,
                FocusScenario(
                    scenario_id="confirmed-save-reuse",
                    prompt=reuse_prompt,
                    timeout=360,
                    include_project_context=False,
                ),
            )
        finally:
            write_transcript(root, "confirmed-save-reuse", session.visible_transcript)

    reuse_answer = final_answer_text(reuse_transcript, reuse_prompt)
    expected_normalized = _normalized_step(expected_step)
    answer_normalized = _normalized_step(reuse_answer)
    assert answer_normalized == expected_normalized or answer_normalized.startswith(
        f"{expected_normalized} "
    ), reuse_answer
    assert "does not exist" not in reuse_answer.lower()

    selected = _selected_skill_events(reuse_probe)
    assert selected, "fresh session did not emit skill.selected"
    payload = selected[-1].get("payload", selected[-1])
    skill_ref = payload.get("skill_ref", {})
    assert skill_ref.get("id") == skill_id
    assert skill_ref.get("version") == version_hash
    _write_summary(
        root,
        "confirmed-save-summary",
        {
            "skill_id": skill_id,
            "version_hash": version_hash,
            "raw_source_sha256": digest,
            "content_fingerprint": staged["content_fingerprint"],
            "pending_state": staged["state"],
            "admission": admitted,
            "selected_skill_ref": skill_ref,
            **_tree_evidence(save_probe),
        },
    )


def test_live_confirmed_save_denied(
    focus_probe: FocusProbe,
    tmp_path: Path,
) -> None:
    require_live_focus()
    root = artifact_root(tmp_path)
    probe = focus_probe.for_workdir(
        focus_probe.openminion_root, include_project_context=False
    )
    with probe.session(rows=52, cols=170) as session:
        probe.wait_ready(session)
        try:
            probe.run_turn(
                session,
                FocusScenario(
                    scenario_id="confirmed-save-denied",
                    prompt=_SAVE_PROMPT,
                    expected_markers=("denied|not saved|was not saved",),
                    timeout=360,
                    requires_approval=True,
                    max_auto_approvals=2,
                    approval_reply="no",
                    include_project_context=False,
                ),
            )
        finally:
            write_transcript(root, "confirmed-save-denied", session.visible_transcript)

    assert _version_rows(probe) == []


def test_live_confirmed_save_unanswered(
    focus_probe: FocusProbe,
    tmp_path: Path,
) -> None:
    require_live_focus()
    root = artifact_root(tmp_path)
    probe = focus_probe.for_workdir(
        focus_probe.openminion_root, include_project_context=False
    )
    with probe.session(rows=52, cols=170) as session:
        probe.wait_ready(session)
        FocusProbe._submit_composer_line(session, _SAVE_PROMPT)
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            if active_approval_visible(session.screen_text):
                break
            time.sleep(0.1)
        else:
            raise AssertionError(
                "Focus never presented the required skill.ingest approval\n"
                f"{session.screen_text[-2000:]}"
            )
        write_transcript(root, "confirmed-save-unanswered", session.visible_transcript)

    assert _version_rows(probe) == []


def test_live_confirmed_save_simple_turn_does_not_write(
    focus_probe: FocusProbe,
    tmp_path: Path,
) -> None:
    require_live_focus()
    root = artifact_root(tmp_path)
    prompt = "Reply with exactly: ordinary turn complete"
    probe = focus_probe.for_workdir(
        focus_probe.openminion_root, include_project_context=False
    )
    with probe.session(rows=42, cols=140) as session:
        probe.wait_ready(session)
        try:
            probe.run_turn(
                session,
                FocusScenario(
                    scenario_id="confirmed-save-simple-turn",
                    prompt=prompt,
                    expected_markers=("ordinary turn complete",),
                    timeout=240,
                    include_project_context=False,
                ),
            )
        finally:
            write_transcript(
                root, "confirmed-save-simple-turn", session.visible_transcript
            )

    assert _version_rows(probe) == []
