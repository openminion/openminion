from __future__ import annotations

import json
from pathlib import Path
import uuid

import pytest

from openminion.cli.config import load_cli_config
from openminion.modules.session.storage.sqlite_store import SQLiteSessionStore
from openminion.modules.skill.config import from_base_config as skill_from_base_config
from openminion.modules.skill.interfaces import SkillIngestAuthority
from openminion.modules.skill.runtime.skill import Skill
from openminion.modules.brain.paths import resolve_brain_sessions_db_path
from tests.helpers.live_cli_chat_alibaba import (
    artifact_dir,
    extract_assistant_messages,
    extract_last_debug_payload,
    has_completion_contract_failure,
    require_live_flag,
    runtime_home_root,
    run_cli_session,
    skip_if_completion_contract_failed,
)

pytestmark = pytest.mark.e2e


_SKILL_CONFIG_PATH = (
    Path(__file__).resolve().parents[3]
    / "test-configs"
    / "per-agent-openrouter-claude-haiku-4-5-skill-e2e.json"
)
_LINEAR_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "skill"
    / "fixtures"
    / "external_catalog"
    / "openai"
    / "linear"
    / "SKILL.md"
)


def _ingest_linear_skill(*, data_root: Path) -> tuple[str, str]:
    base_config = load_cli_config(
        _SKILL_CONFIG_PATH,
        home_root=runtime_home_root(),
        data_root=data_root,
    )
    skill_cfg = skill_from_base_config(
        base_config=base_config,
        home_root=runtime_home_root(),
        data_root=data_root,
    )
    skill_cfg.wal = False
    skill_cfg.known_tools = ["http_request"]
    ctl = Skill(
        config=skill_cfg,
        home_root=runtime_home_root(),
    )
    authority = SkillIngestAuthority.local_operator(
        surface="tests.e2e.skill_cli_smoke",
        principal_id="live-skill-e2e",
    )
    try:
        skill_id, version_hash, warnings = ctl.ingest_file(
            _LINEAR_FIXTURE,
            scope="agent",
            agent_id="hello-agent",
            authority=authority,
        )
        assert not any(item.startswith("lint.error:") for item in warnings)
        ctl.admit_skill_version(
            skill_id=skill_id,
            version_hash=version_hash,
            expected_active_version_hash=None,
            target_status="verified",
            reason="live skill smoke fixture admission",
            authority=authority,
        )
        return skill_id, version_hash
    finally:
        ctl.close()


def _run_skill_cli_smoke(*, data_root: Path) -> tuple[str, Path, str]:
    result = run_cli_session(
        session_id_prefix="live-skill-cli-smoke",
        agent_id="hello-agent",
        config_path=_SKILL_CONFIG_PATH,
        data_root_override=data_root,
        matrix_type="skill_simple",
        user_input=(
            "I need to triage a Linear issue ENG-123. "
            "Use the relevant skill and tell me the first two steps only.\n"
            "/debug\n"
            "/exit\n"
        ),
    )
    return result.session_id, result.transcript_path, result.transcript


@pytest.mark.e2e
def test_live_skill_cli_smoke() -> None:
    require_live_flag()
    if not _SKILL_CONFIG_PATH.exists():
        pytest.skip(f"missing config file: {_SKILL_CONFIG_PATH}")

    skill_artifacts = artifact_dir() / "skill"
    skill_artifacts.mkdir(parents=True, exist_ok=True)
    data_root = (
        skill_artifacts / "data-roots" / f"live-skill-cli-smoke-{uuid.uuid4().hex[:8]}"
    )
    data_root.mkdir(parents=True, exist_ok=True)

    skill_id, _version_hash = _ingest_linear_skill(data_root=data_root)
    session_id, transcript_path, transcript = _run_skill_cli_smoke(data_root=data_root)

    debug_payload = extract_last_debug_payload(transcript)
    last_turn = debug_payload.get("last_turn")
    assert isinstance(last_turn, dict), (
        f"missing /debug last_turn payload\ntranscript={transcript_path}"
    )
    skip_if_completion_contract_failed(
        last_turn=last_turn,
        transcript_path=transcript_path,
        context="live skill smoke target",
    )
    last_turn_metadata = (
        dict(last_turn.get("metadata", {}))
        if isinstance(last_turn.get("metadata"), dict)
        else {}
    )
    conversation_id = str(last_turn_metadata.get("conversation_id", "")).strip()
    event_session_id = (
        f"{session_id}::conv:{conversation_id}" if conversation_id else session_id
    )

    assistant_messages = extract_assistant_messages(
        transcript=transcript,
        session_id=session_id,
        agent_id="hello-agent",
    )
    if not assistant_messages and has_completion_contract_failure(last_turn):
        pytest.skip(
            "live skill smoke target produced a fail-closed completion-contract "
            f"outcome instead of assistant output: {transcript_path}"
        )
    assert assistant_messages, f"missing assistant output\ntranscript={transcript_path}"

    brain_store_path = resolve_brain_sessions_db_path(
        storage_path=data_root / "state" / "openminion.db"
    )
    store = SQLiteSessionStore(brain_store_path)
    try:
        events = store.list_events(event_session_id, limit=200)
    finally:
        store.close()

    event_types = [str(event.get("type", "")) for event in events]
    assert "skill.prerouting" in event_types, (
        f"missing skill.prerouting event\ntranscript={transcript_path}"
    )
    assert "skill.selected" in event_types, (
        f"missing skill.selected event\ntranscript={transcript_path}"
    )

    selected_payload = next(
        event for event in events if str(event.get("type", "")) == "skill.selected"
    )
    payload = selected_payload.get("payload")
    assert isinstance(payload, dict), (
        f"missing skill.selected payload\n{selected_payload}"
    )
    skill_ref = (
        dict(payload.get("skill_ref", {}))
        if isinstance(payload.get("skill_ref"), dict)
        else {}
    )
    selected_skill_id = str(skill_ref.get("id", "") or payload.get("id", "")).strip()
    assert selected_skill_id == skill_id
    selected_skill_ids = [
        str(item).strip() for item in payload.get("selected_skill_ids", [])
    ]
    assert selected_skill_id in selected_skill_ids

    events_path = skill_artifacts / f"{session_id}-events.json"
    events_path.write_text(
        json.dumps(events, indent=2, sort_keys=True), encoding="utf-8"
    )
