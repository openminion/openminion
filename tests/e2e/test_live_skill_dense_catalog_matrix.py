from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import uuid

import pytest

from openminion.base.generated_paths import resolve_generated_root
from openminion.modules.session.storage.sqlite_store import SQLiteSessionStore
from openminion.modules.brain.paths import resolve_brain_sessions_db_path
from tests.helpers.live_cli_chat_alibaba import (
    extract_assistant_messages,
    extract_last_debug_payload,
    framework_root,
    has_completion_contract_failure,
    openminion_root,
    python_bin,
    require_live_flag,
    skip_if_completion_contract_failed,
    timeout_seconds,
)
from tests.helpers.live_skill_targets import SkillLiveTarget
from tests.helpers.live_skill_targets import dense_skill_artifact_dirname
from tests.helpers.live_skill_targets import resolve_dense_skill_target_set
from tests.helpers.live_skill_targets import validate_skill_live_target

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(1800)]


@dataclass(frozen=True)
class _DenseSkillScenario:
    skill_id: str
    fixture_path: Path
    prompt: str


_FIXTURES_ROOT = (
    Path(__file__).resolve().parents[1] / "skill" / "fixtures" / "external_catalog"
)
_TARGET_SET_NAME, _TARGETS = resolve_dense_skill_target_set()


def _representative_dense_scenarios() -> tuple[_DenseSkillScenario, ...]:
    return (
        _DenseSkillScenario(
            skill_id="claude-api",
            fixture_path=_FIXTURES_ROOT / "anthropic" / "claude-api" / "SKILL.md",
            prompt=(
                "Use the claude-api skill to plan a small Claude "
                "Messages API integration for a Python app. Give the first four "
                "adapted steps, one verification check, and one guardrail."
            ),
        ),
        _DenseSkillScenario(
            skill_id="news_digest",
            fixture_path=_FIXTURES_ROOT / "anthropic" / "news_digest" / "SKILL.md",
            prompt=(
                "Use the news_digest skill for a Slack-ready digest of "
                "the most important AI policy news from this week. Give the first "
                "four adapted steps, one verification check, and one guardrail."
            ),
        ),
        _DenseSkillScenario(
            skill_id="mcp_builder",
            fixture_path=_FIXTURES_ROOT / "anthropic" / "mcp_builder" / "SKILL.md",
            prompt=(
                "Use the mcp_builder skill to build an MCP server "
                "for PostgreSQL query tools. Give the first four adapted steps, "
                "one verification check, and one guardrail."
            ),
        ),
        _DenseSkillScenario(
            skill_id="web_artifacts_builder",
            fixture_path=(
                _FIXTURES_ROOT / "anthropic" / "web_artifacts_builder" / "SKILL.md"
            ),
            prompt=(
                "Use the web_artifacts_builder skill for an interactive "
                "web artifact for uptime incident analysis. Give the first four "
                "adapted steps, one verification check, and one guardrail."
            ),
        ),
        _DenseSkillScenario(
            skill_id="webapp-testing",
            fixture_path=_FIXTURES_ROOT / "anthropic" / "webapp-testing" / "SKILL.md",
            prompt=(
                "Use the webapp-testing skill for a browser-focused test "
                "plan for a new signup flow. Give the first four adapted steps, "
                "one verification check, and one guardrail."
            ),
        ),
        _DenseSkillScenario(
            skill_id="github_pr",
            fixture_path=_FIXTURES_ROOT / "openai" / "github_pr" / "SKILL.md",
            prompt=(
                "Use the github_pr skill for a pull-request review and "
                "merge plan for a risky Python refactor. Give the first four "
                "adapted steps, one verification check, and one guardrail."
            ),
        ),
        _DenseSkillScenario(
            skill_id="data_export",
            fixture_path=_FIXTURES_ROOT / "openai" / "data_export" / "SKILL.md",
            prompt=(
                "Use the data_export skill to export customer usage "
                "data to CSV safely. Give the first four adapted steps, one "
                "verification check, and one guardrail."
            ),
        ),
        _DenseSkillScenario(
            skill_id="figma_code_connect_components",
            fixture_path=(
                _FIXTURES_ROOT / "openai" / "figma_code_connect_components" / "SKILL.md"
            ),
            prompt=(
                "Use the figma_code_connect_components skill to map "
                "Figma components to our React design system. Give the first four "
                "adapted steps, one verification check, and one guardrail."
            ),
        ),
        _DenseSkillScenario(
            skill_id="figma_generate_design",
            fixture_path=_FIXTURES_ROOT
            / "openai"
            / "figma_generate_design"
            / "SKILL.md",
            prompt=(
                "Use the figma_generate_design skill for an initial "
                "dashboard design from a product brief. Give the first four adapted "
                "steps, one verification check, and one guardrail."
            ),
        ),
        _DenseSkillScenario(
            skill_id="playwright",
            fixture_path=_FIXTURES_ROOT / "openai" / "playwright" / "SKILL.md",
            prompt=(
                "Use the playwright skill for browser tests on a "
                "checkout flow. Give the first four adapted steps, one "
                "verification check, and one guardrail."
            ),
        ),
    )


def _positive_skill_fixture_paths() -> tuple[Path, ...]:
    return tuple(
        path
        for path in sorted(_FIXTURES_ROOT.glob("*/*/SKILL.md"))
        if path.parent.name != "template-negative"
    )


def _generic_dense_skill_prompt(*, skill_id: str) -> str:
    return (
        f"Use the {skill_id} skill and adapt it to one realistic task that fits "
        "the skill purpose. Give the first four adapted steps, one verification "
        "check, and one guardrail."
    )


def _no_magic_phrase_dense_skill_prompt(*, skill_id: str) -> str:
    humanized = skill_id.replace("_", " ").replace("-", " ")
    return (
        f"I need to handle a {humanized} task. What's the standard workflow? "
        "Give the first four steps, one verification check, and one guardrail."
    )


def _official_dense_scenarios() -> tuple[_DenseSkillScenario, ...]:
    return tuple(
        _DenseSkillScenario(
            skill_id=path.parent.name,
            fixture_path=path,
            prompt=_generic_dense_skill_prompt(skill_id=path.parent.name),
        )
        for path in _positive_skill_fixture_paths()
    )


def _official_dense_scenarios_no_magic_phrase() -> tuple[_DenseSkillScenario, ...]:
    return tuple(
        _DenseSkillScenario(
            skill_id=path.parent.name,
            fixture_path=path,
            prompt=_no_magic_phrase_dense_skill_prompt(skill_id=path.parent.name),
        )
        for path in _positive_skill_fixture_paths()
    )


_SCENARIOS: tuple[_DenseSkillScenario, ...] = (
    _official_dense_scenarios()
    if _TARGET_SET_NAME == "official"
    else _representative_dense_scenarios()
)
_MISSING_SKILL_PROMPT = (
    "Use the totally_missing_skill skill and give the first four adapted "
    "steps, one verification check, and one guardrail."
)


def _artifact_root() -> Path:
    root = resolve_generated_root(
        home_root=framework_root()
    ) / dense_skill_artifact_dirname(_TARGET_SET_NAME)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _run_env(*, data_root: Path, trace_root: Path | None = None) -> dict[str, str]:
    env = dict(os.environ)
    for key in (
        "OPENMINION_CONFIG",
        "OPENMINION_DATA_ROOT",
        "OPENMINION_IDENTITY_DB",
        "OPENMINION_IDENTITY_ROOT",
        "OPENMINION_TRACE_REQUESTS_DIR",
    ):
        env.pop(key, None)

    env["OPENMINION_HOME"] = str(framework_root())
    env["OPENMINION_DATA_ROOT"] = str(data_root)
    if trace_root is not None:
        env["OPENMINION_TRACE_REQUESTS"] = "1"
        env["OPENMINION_TRACE_REQUESTS_DIR"] = str(trace_root)
    current_pythonpath = str(env.get("PYTHONPATH", "")).strip()
    src_root = str(openminion_root() / "src")
    env["PYTHONPATH"] = (
        src_root
        if not current_pythonpath
        else f"{src_root}{os.pathsep}{current_pythonpath}"
    )
    return env


def _run_skill_ingest(
    *,
    target: SkillLiveTarget,
    data_root: Path,
    fixture_path: Path,
    transcript_dir: Path,
) -> dict[str, object]:
    transcript_path = transcript_dir / f"ingest-{fixture_path.parent.name}.json"
    completed = subprocess.run(
        [
            str(python_bin()),
            "-m",
            "openminion",
            "skill",
            "ingest",
            "--file",
            str(fixture_path),
            "--scope",
            "agent",
            "--agent-id",
            target.agent_id,
            "--config",
            str(target.config_path),
        ],
        cwd=str(openminion_root()),
        env=_run_env(data_root=data_root),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout_seconds("skill_dense"),
        check=False,
    )
    transcript = completed.stdout or ""
    transcript_path.write_text(transcript, encoding="utf-8")
    assert completed.returncode == 0, (
        f"skill ingest failed for agent={target.agent_id} fixture={fixture_path}\n"
        f"transcript={transcript_path}\n{transcript}"
    )
    payload = json.loads(transcript)
    assert payload.get("ok") is True, (
        f"ingest payload was not ok for agent={target.agent_id}\n"
        f"transcript={transcript_path}\n{transcript}"
    )
    return dict(payload)


def _run_skill_chat(
    *,
    target: SkillLiveTarget,
    data_root: Path,
    trace_root: Path,
    transcript_dir: Path,
    prompt: str,
    slug: str,
) -> tuple[str, Path, str]:
    session_id = f"live-skill-dense-{target.agent_id}-{slug}-{uuid.uuid4().hex[:8]}"
    transcript_path = transcript_dir / f"{session_id}.txt"
    completed = subprocess.run(
        [
            str(python_bin()),
            "-m",
            "openminion",
            "--config",
            str(target.config_path),
            "--agent",
            target.agent_id,
            "--session",
            session_id,
            "--verbosity",
            "quiet",
            "--progress",
            "off",
        ],
        cwd=str(openminion_root()),
        env=_run_env(data_root=data_root, trace_root=trace_root / session_id),
        input=f"{prompt}\n/debug\n/exit\n",
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout_seconds("skill_dense"),
        check=False,
    )
    transcript = completed.stdout or ""
    transcript_path.write_text(transcript, encoding="utf-8")
    assert completed.returncode == 0, (
        f"cli chat failed for session={session_id} exit={completed.returncode}\n"
        f"transcript={transcript_path}\n{transcript}"
    )
    return session_id, transcript_path, transcript


def _event_session_id(*, session_id: str, transcript: str) -> str:
    debug_payload = extract_last_debug_payload(transcript)
    last_turn = debug_payload.get("last_turn")
    assert isinstance(last_turn, dict), "missing /debug last_turn payload"
    metadata = (
        dict(last_turn.get("metadata", {}))
        if isinstance(last_turn.get("metadata"), dict)
        else {}
    )
    conversation_id = str(metadata.get("conversation_id", "")).strip()
    return f"{session_id}::conv:{conversation_id}" if conversation_id else session_id


def _load_last_turn(*, transcript: str, transcript_path: Path) -> dict:
    debug_payload = extract_last_debug_payload(transcript)
    last_turn = debug_payload.get("last_turn")
    assert isinstance(last_turn, dict), (
        f"missing /debug last_turn payload\ntranscript={transcript_path}"
    )
    return last_turn


def _load_events(*, data_root: Path, event_session_id: str) -> list[dict]:
    brain_store_path = resolve_brain_sessions_db_path(
        storage_path=data_root / "state" / "openminion.db"
    )
    store = SQLiteSessionStore(brain_store_path)
    try:
        return store.list_events(event_session_id, limit=400)
    finally:
        store.close()


@pytest.mark.e2e
@pytest.mark.parametrize(
    "target", _TARGETS, ids=[target.target_id for target in _TARGETS]
)
def test_live_skill_dense_catalog_named_selection_matrix(
    target: SkillLiveTarget,
) -> None:
    require_live_flag()
    validate_skill_live_target(target)
    if not target.config_path.exists():
        pytest.skip(f"missing config file: {target.config_path}")

    artifact_root = _artifact_root()
    run_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"{target.target_id}-{run_stamp}-{uuid.uuid4().hex[:6]}"
    data_root = artifact_root / "data-roots" / run_id
    trace_root = artifact_root / "traces"
    transcript_dir = artifact_root / "transcripts"
    data_root.mkdir(parents=True, exist_ok=True)
    trace_root.mkdir(parents=True, exist_ok=True)
    transcript_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []
    summary_path = artifact_root / f"{run_id}.json"

    try:
        ingested_ids: dict[str, str] = {}
        for scenario in _SCENARIOS:
            payload = _run_skill_ingest(
                target=target,
                data_root=data_root,
                fixture_path=scenario.fixture_path,
                transcript_dir=transcript_dir,
            )
            warnings = [str(item).strip() for item in payload.get("warnings", [])]
            assert not any(item.startswith("lint.error:") for item in warnings)
            ingested_ids[scenario.skill_id] = str(payload.get("skill_id", "")).strip()

        for scenario in _SCENARIOS:
            session_id, transcript_path, transcript = _run_skill_chat(
                target=target,
                data_root=data_root,
                trace_root=trace_root,
                transcript_dir=transcript_dir,
                prompt=scenario.prompt,
                slug=scenario.skill_id,
            )
            last_turn = _load_last_turn(
                transcript=transcript,
                transcript_path=transcript_path,
            )
            skip_if_completion_contract_failed(
                last_turn=last_turn,
                transcript_path=transcript_path,
                context=(
                    "live dense skill target "
                    f"{target.target_id} scenario {scenario.skill_id}"
                ),
            )
            assistant_messages = extract_assistant_messages(
                transcript=transcript,
                session_id=session_id,
                agent_id=target.agent_id,
            )
            if not assistant_messages and has_completion_contract_failure(last_turn):
                pytest.skip(
                    "live dense skill target produced a fail-closed "
                    "completion-contract outcome instead of assistant output: "
                    f"{transcript_path}"
                )
            assert assistant_messages, (
                f"missing assistant output\ntranscript={transcript_path}"
            )

            event_session_id = _event_session_id(
                session_id=session_id,
                transcript=transcript,
            )
            events = _load_events(
                data_root=data_root, event_session_id=event_session_id
            )
            events_path = transcript_dir / f"{session_id}-events.json"
            events_path.write_text(
                json.dumps(events, indent=2, sort_keys=True), encoding="utf-8"
            )

            selected_events = [
                event
                for event in events
                if str(event.get("type", "")) == "skill.selected"
            ]
            expected_skill_id = ingested_ids[scenario.skill_id]
            if not selected_events:
                failures.append(
                    {
                        "scenario": scenario.skill_id,
                        "expected_skill_id": expected_skill_id,
                        "selected_skill_id": None,
                        "selected_skill_ids": [],
                        "reason": "missing_skill_selected",
                        "transcript": str(transcript_path),
                        "events": str(events_path),
                    }
                )
                results.append(
                    {
                        "scenario": scenario.skill_id,
                        "expected_skill_id": expected_skill_id,
                        "selected_skill_id": None,
                        "selected_skill_ids": [],
                        "result": "fail",
                        "reason": "missing_skill_selected",
                        "transcript": str(transcript_path),
                        "events": str(events_path),
                    }
                )
                continue

            payload = dict(selected_events[-1].get("payload", {}))
            skill_ref = (
                dict(payload.get("skill_ref", {}))
                if isinstance(payload.get("skill_ref"), dict)
                else {}
            )
            selected_skill_id = str(
                skill_ref.get("id", "") or payload.get("id", "")
            ).strip()
            selected_skill_ids = [
                str(item).strip() for item in payload.get("selected_skill_ids", [])
            ]
            if (
                selected_skill_id != expected_skill_id
                or payload.get("primary_skill_id") != expected_skill_id
                or selected_skill_ids != [expected_skill_id]
                or payload.get("selected_skill_count") != 1
            ):
                failures.append(
                    {
                        "scenario": scenario.skill_id,
                        "expected_skill_id": expected_skill_id,
                        "selected_skill_id": selected_skill_id,
                        "selected_skill_ids": selected_skill_ids,
                        "reason": "wrong_selected_skill",
                        "payload": payload,
                        "transcript": str(transcript_path),
                        "events": str(events_path),
                    }
                )
                results.append(
                    {
                        "scenario": scenario.skill_id,
                        "expected_skill_id": expected_skill_id,
                        "selected_skill_id": selected_skill_id,
                        "selected_skill_ids": selected_skill_ids,
                        "result": "fail",
                        "reason": "wrong_selected_skill",
                        "payload": payload,
                        "transcript": str(transcript_path),
                        "events": str(events_path),
                    }
                )
                continue

            results.append(
                {
                    "scenario": scenario.skill_id,
                    "expected_skill_id": expected_skill_id,
                    "selected_skill_id": selected_skill_id,
                    "selected_skill_ids": selected_skill_ids,
                    "result": "pass",
                    "transcript": str(transcript_path),
                    "events": str(events_path),
                }
            )

        session_id, transcript_path, transcript = _run_skill_chat(
            target=target,
            data_root=data_root,
            trace_root=trace_root,
            transcript_dir=transcript_dir,
            prompt=_MISSING_SKILL_PROMPT,
            slug="missing",
        )
        last_turn = _load_last_turn(
            transcript=transcript,
            transcript_path=transcript_path,
        )
        skip_if_completion_contract_failed(
            last_turn=last_turn,
            transcript_path=transcript_path,
            context=f"live dense skill target {target.target_id} missing-skill probe",
        )
        assistant_messages = extract_assistant_messages(
            transcript=transcript,
            session_id=session_id,
            agent_id=target.agent_id,
        )
        if not assistant_messages and has_completion_contract_failure(last_turn):
            pytest.skip(
                "live dense skill target produced a fail-closed "
                "completion-contract outcome instead of assistant output: "
                f"{transcript_path}"
            )
        assert assistant_messages, (
            f"missing assistant output\ntranscript={transcript_path}"
        )

        event_session_id = _event_session_id(
            session_id=session_id,
            transcript=transcript,
        )
        events = _load_events(data_root=data_root, event_session_id=event_session_id)
        events_path = transcript_dir / f"{session_id}-events.json"
        events_path.write_text(
            json.dumps(events, indent=2, sort_keys=True), encoding="utf-8"
        )
        selected_events = [
            event for event in events if str(event.get("type", "")) == "skill.selected"
        ]
        assert not selected_events, (
            f"unexpected skill.selected event for missing skill\n"
            f"transcript={transcript_path}\n"
            f"events={events_path}"
        )
        prerouting_events = [
            event
            for event in events
            if str(event.get("type", "")) == "skill.prerouting"
        ]
        assert prerouting_events, (
            f"missing skill.prerouting event for missing skill\n"
            f"transcript={transcript_path}\n"
            f"events={events_path}"
        )
        prerouting_payload = dict(prerouting_events[-1].get("payload", {}))
        assert prerouting_payload.get("fail_closed_reason") is None
        assert prerouting_payload.get("selected_skill_ids") == []
        assert prerouting_payload.get("selected_skill_count") == 0
        results.append(
            {
                "scenario": "missing_skill",
                "expected_skill_id": None,
                "selected_skill_id": None,
                "selected_skill_ids": [],
                "result": "pass",
                "fail_closed_reason": None,
                "transcript": str(transcript_path),
                "events": str(events_path),
            }
        )
    finally:
        summary_path.write_text(
            json.dumps(
                {
                    "target": target.target_id,
                    "config_path": str(target.config_path),
                    "agent_id": target.agent_id,
                    "failure_count": len(failures),
                    "pass_count": sum(
                        1 for item in results if str(item.get("result", "")) == "pass"
                    ),
                    "results": results,
                    "failures": failures,
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
    assert not failures, (
        "dense skill catalog mismatches recorded\n"
        f"summary={summary_path}\n"
        f"failures={json.dumps(failures, indent=2, sort_keys=True)}"
    )
