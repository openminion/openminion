from __future__ import annotations

import json
from pathlib import Path
import uuid

import pytest

from openminion.cli.config import load_cli_config
from openminion.modules.session.storage.sqlite_store import SQLiteSessionStore
from openminion.modules.skill.config import from_base_config as skill_from_base_config
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
from tests.helpers.live_skill_targets import SkillLiveTarget
from tests.helpers.live_skill_targets import skill_simple_targets
from tests.helpers.live_skill_targets import validate_skill_live_target

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(600)]

_TARGETS: tuple[SkillLiveTarget, ...] = skill_simple_targets()

_LINEAR_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "skill"
    / "fixtures"
    / "external_catalog"
    / "openai"
    / "linear"
    / "SKILL.md"
)


def _ingest_linear_skill(*, config_path: Path, agent_id: str, data_root: Path) -> str:
    base_config = load_cli_config(
        config_path,
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
    ctl = Skill(config=skill_cfg, home_root=runtime_home_root())
    try:
        skill_id, _version_hash, warnings = ctl.ingest_file(
            _LINEAR_FIXTURE,
            scope="agent",
            agent_id=agent_id,
        )
        assert not any(item.startswith("lint.error:") for item in warnings)
        return skill_id
    finally:
        ctl.close()


def _run_skill_cli_smoke(
    *, config_path: Path, agent_id: str, data_root: Path
) -> tuple[str, Path, str]:
    result = run_cli_session(
        session_id_prefix=f"live-skill-support-{agent_id}",
        agent_id=agent_id,
        config_path=config_path,
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


def test_skill_matrix_bundle_agents_resolve_from_canonical_registry() -> None:
    bundle_targets = [
        target
        for target in skill_simple_targets()
        if target.target_id.startswith("bundle:")
    ]
    assert bundle_targets, "canonical skill registry must publish bundle targets"
    for target in bundle_targets:
        validate_skill_live_target(target)


@pytest.mark.e2e
@pytest.mark.parametrize(
    "target", _TARGETS, ids=[target.target_id for target in _TARGETS]
)
def test_live_skill_model_matrix(target: SkillLiveTarget) -> None:
    require_live_flag()
    validate_skill_live_target(target)
    if not target.config_path.exists():
        pytest.skip(f"missing config file: {target.config_path}")

    agent_id = target.agent_id
    skill_artifacts = artifact_dir() / "skill-support"
    skill_artifacts.mkdir(parents=True, exist_ok=True)
    data_root = (
        skill_artifacts / "data-roots" / f"{target.target_id}-{uuid.uuid4().hex[:8]}"
    )
    data_root.mkdir(parents=True, exist_ok=True)

    skill_id = _ingest_linear_skill(
        config_path=target.config_path,
        agent_id=agent_id,
        data_root=data_root,
    )
    session_id, transcript_path, transcript = _run_skill_cli_smoke(
        config_path=target.config_path,
        agent_id=agent_id,
        data_root=data_root,
    )

    debug_payload = extract_last_debug_payload(transcript)
    last_turn = debug_payload.get("last_turn")
    assert isinstance(last_turn, dict), (
        f"missing /debug last_turn payload\ntranscript={transcript_path}"
    )
    skip_if_completion_contract_failed(
        last_turn=last_turn,
        transcript_path=transcript_path,
        context="live skill support target",
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
        agent_id=agent_id,
    )
    if not assistant_messages and has_completion_contract_failure(last_turn):
        pytest.skip(
            "live skill support target produced a fail-closed completion-contract "
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

    events_path = artifact_dir() / "skill-support" / f"{session_id}-events.json"
    events_path.write_text(
        json.dumps(events, indent=2, sort_keys=True), encoding="utf-8"
    )
