from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pytest

from tests.helpers.live_cli_chat_alibaba import (
    RAW_TOOL_MARKUP_RE,
    artifact_dir,
    extract_assistant_messages,
    extract_last_debug_payload,
    framework_root,
    parse_tool_results,
    require_live_flag,
    openminion_root,
    run_cli_session,
    transcript_has_assistant_output,
    transcript_has_cli_ready,
)
from tests.helpers.live_e2e_profiles import resolve_live_config_path

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(420)]


@dataclass(frozen=True)
class _Target:
    target_id: str
    config_path: Path
    agent_id: str


@dataclass(frozen=True)
class _Scenario:
    id: str
    expected_tools: tuple[str, ...] = ()
    expected_outcome: Literal[
        "tool_execution",
        "non_success_without_execution",
    ] = "tool_execution"
    require_artifact_path: bool = False


_OFFICIAL_CONFIG = resolve_live_config_path(
    "per-agent-minimax-official.json",
    framework_root(),
)

_TARGETS: tuple[_Target, ...] = (
    _Target(
        target_id="minimax-m2-5",
        config_path=_OFFICIAL_CONFIG,
        agent_id="minimax-m2-5",
    ),
    _Target(
        target_id="minimax-m2-7",
        config_path=_OFFICIAL_CONFIG,
        agent_id="minimax-m2-7",
    ),
)

_SCENARIOS: tuple[_Scenario, ...] = (
    _Scenario(id="time_now", expected_tools=("time", "time.now")),
    _Scenario(id="location_get", expected_tools=("location", "location.lookup")),
    _Scenario(
        id="weather_now", expected_tools=("weather", "weather.openmeteo.current")
    ),
    _Scenario(
        id="search_news",
        expected_tools=("web.search", "search.serpapi.search", "web_search"),
    ),
    _Scenario(id="fetch_get", expected_tools=("web.fetch", "fetch.get")),
    _Scenario(
        id="file_list_dir",
        expected_tools=("file.list_dir", "file_list_dir", "list_files"),
    ),
    _Scenario(
        id="file_find",
        expected_tools=("file.find", "file_find", "find_files"),
    ),
    _Scenario(
        id="file_read",
        expected_tools=("file.read", "file_read", "read_file"),
    ),
    _Scenario(
        id="file_write",
        expected_outcome="non_success_without_execution",
        require_artifact_path=True,
    ),
    _Scenario(id="exec_run", expected_tools=("exec.run",)),
)


def _scenario_prompt(
    *, target: _Target, scenario: _Scenario
) -> tuple[str, Path | None]:
    repo_root = openminion_root()
    readme_path = repo_root / "README.md"
    write_path = (
        artifact_dir()
        / "official-tool-writes"
        / target.target_id
        / f"{scenario.id}-{target.agent_id}.txt"
    )
    write_path.parent.mkdir(parents=True, exist_ok=True)
    if write_path.exists():
        write_path.unlink()

    prompts: dict[str, str] = {
        "time_now": 'tool time {"timezone":"UTC"}',
        "location_get": "tool location {}",
        "weather_now": 'tool weather {"location":"san francisco"}',
        "search_news": 'tool web.search {"query":"latest news on iran"}',
        "fetch_get": 'tool web.fetch {"url":"https://example.com"}',
        "file_list_dir": f'tool file.list_dir {{"path":"{repo_root}"}}',
        "file_find": f'tool file.find {{"path":"{repo_root}","pattern":"README*"}}',
        "file_read": (
            f"use file.read on {readme_path} and reply with the first sentence only"
        ),
        "file_write": (
            f'tool file.write {{"path":"{write_path}","content":"official minimax write smoke\\n"}}'
        ),
        "exec_run": 'tool exec.run {"command":"pwd"}',
    }
    return prompts[scenario.id], write_path if scenario.require_artifact_path else None


def _matches_expected_tool(
    *, executed_tool_names: set[str], expected_tools: tuple[str, ...]
) -> bool:
    for executed_name in executed_tool_names:
        if executed_name in expected_tools:
            return True
        if any(executed_name.endswith(f".{name}") for name in expected_tools):
            return True
    return False


def _is_truthful_no_execution_outcome(
    *,
    transcript: str,
    body_preview: str,
    assistant_messages: list[str],
    tool_results: list[dict[str, object]],
) -> bool:
    denied_results = [
        item
        for item in tool_results
        if str(item.get("error_code", "") or "").strip() == "POLICY_DENIED"
        or str(item.get("content", "") or "").strip().lower()
        == "tool execution denied by operator"
    ]
    if tool_results and len(denied_results) != len(tool_results):
        return False
    accepted_needles = (
        "denied by policy: operation requires explicit confirmation",
        "policy confirmation required.",
        "the requested tool was not executed, so i cannot truthfully claim it succeeded.",
        "repeated identical tool calls detected without reaching a final answer.",
        "tool execution denied by operator",
    )
    surfaces = [transcript, body_preview, *assistant_messages]
    lowered_surfaces = [str(surface).lower() for surface in surfaces]
    return any(
        needle in surface for needle in accepted_needles for surface in lowered_surfaces
    )


def test_confirmation_required_scenarios_are_classified_explicitly() -> None:
    scenarios = {scenario.id: scenario for scenario in _SCENARIOS}
    assert scenarios["file_write"].expected_outcome == "non_success_without_execution"
    assert scenarios["exec_run"].expected_outcome == "tool_execution"
    assert scenarios["time_now"].expected_outcome == "tool_execution"


def test_truthful_no_execution_helper_requires_no_tool_results() -> None:
    assert _is_truthful_no_execution_outcome(
        transcript="Denied by policy: operation requires explicit confirmation",
        body_preview="Denied by policy: operation requires explicit confirmation",
        assistant_messages=[
            "Denied by policy: operation requires explicit confirmation"
        ],
        tool_results=[],
    )
    assert _is_truthful_no_execution_outcome(
        transcript="Policy confirmation required.",
        body_preview="Policy confirmation required.\nexec.run (command=pwd)",
        assistant_messages=[
            "Policy confirmation required.\nReply exactly yes to confirm or exactly no to cancel."
        ],
        tool_results=[],
    )
    assert _is_truthful_no_execution_outcome(
        transcript="The requested tool was not executed, so I cannot truthfully claim it succeeded.",
        body_preview="The requested tool was not executed, so I cannot truthfully claim it succeeded.",
        assistant_messages=[
            "The requested tool was not executed, so I cannot truthfully claim it succeeded."
        ],
        tool_results=[],
    )
    assert not _is_truthful_no_execution_outcome(
        transcript="Denied by policy: operation requires explicit confirmation",
        body_preview="Denied by policy: operation requires explicit confirmation",
        assistant_messages=[
            "Denied by policy: operation requires explicit confirmation"
        ],
        tool_results=[{"tool_name": "file.write"}],
    )
    assert _is_truthful_no_execution_outcome(
        transcript="Tool execution denied by operator",
        body_preview=(
            "[act:coding] repeated identical tool calls detected without reaching "
            "a final answer."
        ),
        assistant_messages=[
            "[act:coding] repeated identical tool calls detected without reaching "
            "a final answer."
        ],
        tool_results=[
            {
                "tool_name": "file.write",
                "ok": False,
                "error_code": "POLICY_DENIED",
            }
        ],
    )


@pytest.mark.e2e
@pytest.mark.parametrize(
    "target", _TARGETS, ids=[target.target_id for target in _TARGETS]
)
@pytest.mark.parametrize(
    "scenario", _SCENARIOS, ids=[scenario.id for scenario in _SCENARIOS]
)
def test_live_cli_chat_minimax_official_tool_matrix(
    target: _Target,
    scenario: _Scenario,
) -> None:
    require_live_flag()
    if not target.config_path.exists():
        pytest.skip(f"missing config file: {target.config_path}")

    prompt, artifact_path = _scenario_prompt(target=target, scenario=scenario)
    result = run_cli_session(
        session_id_prefix=f"live-cli-official-{target.target_id}-{scenario.id}",
        user_input=f"{prompt}\n/debug\n/exit\n",
        agent_id=target.agent_id,
        config_path=target.config_path,
        matrix_type="skill_simple",
    )

    transcript = result.transcript
    transcript_path = result.transcript_path
    assert transcript_has_cli_ready(transcript=transcript, agent_id=target.agent_id), (
        f"missing chat ready marker for target={target.target_id} scenario={scenario.id}\n"
        f"transcript={transcript_path}"
    )
    assert transcript_has_assistant_output(
        transcript=transcript,
        session_id=result.session_id,
        agent_id=target.agent_id,
    ), (
        f"missing assistant response marker for target={target.target_id} scenario={scenario.id}\n"
        f"transcript={transcript_path}"
    )
    assert not RAW_TOOL_MARKUP_RE.search(transcript), (
        f"raw tool markup leaked for target={target.target_id} scenario={scenario.id}\n"
        f"transcript={transcript_path}"
    )

    debug_payload = extract_last_debug_payload(transcript)
    last_turn = debug_payload.get("last_turn")
    assert isinstance(last_turn, dict), (
        f"missing last_turn debug payload for target={target.target_id} scenario={scenario.id}\n"
        f"transcript={transcript_path}"
    )

    metadata = last_turn.get("metadata")
    assert isinstance(metadata, dict), (
        f"missing metadata in last_turn debug payload for target={target.target_id} scenario={scenario.id}\n"
        f"transcript={transcript_path}"
    )

    assistant_messages = extract_assistant_messages(
        transcript=transcript,
        session_id=result.session_id,
        agent_id=target.agent_id,
    )
    assert assistant_messages, (
        f"missing assistant messages for target={target.target_id} scenario={scenario.id}\n"
        f"transcript={transcript_path}"
    )

    tool_results = parse_tool_results(metadata.get("tool_results"))

    if scenario.expected_outcome == "non_success_without_execution":
        assert _is_truthful_no_execution_outcome(
            transcript=transcript,
            body_preview=str(last_turn.get("body_preview", "") or ""),
            assistant_messages=assistant_messages,
            tool_results=tool_results,
        ), (
            f"expected truthful no-execution outcome for target={target.target_id} "
            f"scenario={scenario.id}\n"
            f"metadata={json.dumps(metadata, indent=2, sort_keys=True)}\n"
            f"transcript={transcript_path}"
        )
        successful_tool_results = [
            item for item in tool_results if bool(item.get("ok", False))
        ]
        assert not successful_tool_results, (
            f"no-execution scenario should not report successful tool execution for "
            f"target={target.target_id} scenario={scenario.id}\n"
            f"metadata={json.dumps(metadata, indent=2, sort_keys=True)}\n"
            f"transcript={transcript_path}"
        )
        if artifact_path is not None:
            assert not artifact_path.exists(), (
                f"no-execution write should not create artifact for "
                f"target={target.target_id} scenario={scenario.id}\n"
                f"path={artifact_path}\ntranscript={transcript_path}"
            )
        return

    assert tool_results, (
        f"no tool results recorded for target={target.target_id} scenario={scenario.id}\n"
        f"metadata={json.dumps(metadata, indent=2, sort_keys=True)}\n"
        f"transcript={transcript_path}"
    )
    executed_tool_names = {
        str(item.get("tool_name", "")).strip()
        for item in tool_results
        if str(item.get("tool_name", "")).strip()
    }
    assert executed_tool_names, (
        f"tool results missing tool_name for target={target.target_id} scenario={scenario.id}\n"
        f"tool_results={json.dumps(tool_results, indent=2, sort_keys=True)}\n"
        f"transcript={transcript_path}"
    )
    assert _matches_expected_tool(
        executed_tool_names=executed_tool_names,
        expected_tools=scenario.expected_tools,
    ), (
        f"unexpected tool execution for target={target.target_id} scenario={scenario.id}\n"
        f"expected_any={scenario.expected_tools}\n"
        f"executed={sorted(executed_tool_names)}\n"
        f"tool_results={json.dumps(tool_results, indent=2, sort_keys=True)}\n"
        f"transcript={transcript_path}"
    )

    tool_execution_count = int(
        str(metadata.get("tool_execution_count", "0")).strip() or "0"
    )
    assert tool_execution_count >= 1, (
        f"tool_execution_count missing for target={target.target_id} scenario={scenario.id}\n"
        f"metadata={json.dumps(metadata, indent=2, sort_keys=True)}\n"
        f"transcript={transcript_path}"
    )

    if scenario.id == "exec_run":
        repo_root = framework_root()
        assert any(
            str(item.get("content", "") or "").find("stdout:") >= 0
            and str(item.get("content", "") or "").find(str(repo_root)) >= 0
            for item in tool_results
        ), (
            "exec.run result should expose stdout in model-visible content\n"
            f"tool_results={json.dumps(tool_results, indent=2, sort_keys=True)}\n"
            f"transcript={transcript_path}"
        )

    if artifact_path is not None:
        assert artifact_path.exists(), (
            f"file write target missing for target={target.target_id} scenario={scenario.id}\n"
            f"path={artifact_path}\ntranscript={transcript_path}"
        )
        assert "official minimax write smoke" in artifact_path.read_text(
            encoding="utf-8"
        )
