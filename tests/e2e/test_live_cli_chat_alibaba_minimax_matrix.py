from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from tests.helpers.live_cli_chat_alibaba import (
    RAW_TOOL_MARKUP_RE,
    default_agent_id,
    extract_assistant_messages,
    extract_last_debug_payload,
    format_prompt,
    is_unknown_tool_flake,
    parse_tool_results,
    run_cli_session,
)

pytestmark = pytest.mark.e2e


@dataclass(frozen=True)
class _Scenario:
    id: str
    prompt_template: str
    expected_tools: tuple[str, ...]
    forbidden_body_tokens: tuple[str, ...] = ("no action taken",)
    retry_on_unknown_tool: bool = False
    fallback_prompt_templates: tuple[str, ...] = ()


_SCENARIOS: tuple[_Scenario, ...] = (
    _Scenario(
        id="time_now",
        prompt_template="what time is it in UTC right now?",
        expected_tools=("time", "time.now"),
    ),
    _Scenario(
        id="file_list_dir",
        prompt_template="list files in the current directory",
        expected_tools=("file.list_dir", "file_list_dir", "list_files"),
    ),
    _Scenario(
        id="file_read",
        prompt_template="read {framework_root}/README.md and return the first sentence only",
        expected_tools=("file.read", "read_file"),
        forbidden_body_tokens=("denied by policy", "no action taken"),
        fallback_prompt_templates=(
            "use file.read on {framework_root}/README.md and reply with the first sentence only",
            'tool file.read {{"path":"{framework_root}/README.md","max_chars":220}}',
        ),
    ),
    _Scenario(
        id="weather_now",
        prompt_template="what's the weather in San Francisco right now?",
        expected_tools=("weather", "weather.openmeteo.current"),
    ),
    _Scenario(
        id="search_news",
        prompt_template="check latest news on iran and summarize briefly",
        expected_tools=("web.search", "tavily.web.search", "search.tavily.search"),
        forbidden_body_tokens=("no action taken",),
        retry_on_unknown_tool=True,
        fallback_prompt_templates=(
            "use web search to check the latest news on Iran and summarize briefly",
        ),
    ),
    _Scenario(
        id="fetch_example",
        prompt_template="fetch https://example.com and summarize briefly",
        expected_tools=("web.fetch", "fetch.get"),
        retry_on_unknown_tool=True,
        fallback_prompt_templates=(
            "use web fetch to read https://example.com and summarize it briefly",
        ),
    ),
)


def _matches_expected_tool(
    *, executed_tool_names: set[str], expected_tools: tuple[str, ...]
) -> bool:
    for executed_name in executed_tool_names:
        if executed_name in expected_tools:
            return True
        if any(executed_name.endswith(f".{name}") for name in expected_tools):
            return True
    return False


def _run_scenario(scenario: _Scenario):
    agent_id = default_agent_id()
    prompt_templates = (scenario.prompt_template, *scenario.fallback_prompt_templates)
    last_result = None
    for attempt_index, prompt_template in enumerate(prompt_templates):
        result = run_cli_session(
            session_id_prefix=f"live-cli-alibaba-{scenario.id}",
            user_input=f"{format_prompt(prompt_template)}\n/debug\n/exit\n",
            attempt_suffix="" if attempt_index == 0 else f"retry{attempt_index}",
        )
        last_result = result
        debug_payload = extract_last_debug_payload(result.transcript)
        last_turn = debug_payload.get("last_turn")
        metadata = last_turn.get("metadata") if isinstance(last_turn, dict) else None
        tool_results = (
            parse_tool_results(metadata.get("tool_results"))
            if isinstance(metadata, dict)
            else []
        )
        assistant_messages = extract_assistant_messages(
            transcript=result.transcript,
            session_id=result.session_id,
            agent_id=agent_id,
        )
        unknown_tool_flake = bool(
            scenario.retry_on_unknown_tool
            and assistant_messages
            and is_unknown_tool_flake(assistant_messages[-1])
        )
        if tool_results and not unknown_tool_flake:
            return result
    assert last_result is not None
    return last_result


@pytest.mark.e2e
@pytest.mark.parametrize(
    "scenario", _SCENARIOS, ids=[scenario.id for scenario in _SCENARIOS]
)
def test_live_cli_chat_alibaba_minimax_matrix(scenario: _Scenario) -> None:
    result = _run_scenario(scenario)
    agent_id = default_agent_id()
    session_id = result.session_id
    transcript = result.transcript
    transcript_path = result.transcript_path

    assert f"chat ready agent={agent_id}" in transcript, (
        f"missing chat ready marker for scenario={scenario.id}\n"
        f"transcript={transcript_path}"
    )
    assert f"[{session_id}|{agent_id}] {agent_id}:" in transcript, (
        f"missing assistant response marker for scenario={scenario.id}\n"
        f"transcript={transcript_path}"
    )
    assert not RAW_TOOL_MARKUP_RE.search(transcript), (
        f"raw tool markup leaked for scenario={scenario.id}\n"
        f"transcript={transcript_path}"
    )

    debug_payload = extract_last_debug_payload(transcript)
    last_turn = debug_payload.get("last_turn")
    assert isinstance(last_turn, dict), (
        f"missing last_turn debug payload for scenario={scenario.id}\n"
        f"transcript={transcript_path}"
    )

    metadata = last_turn.get("metadata")
    assert isinstance(metadata, dict), (
        f"missing metadata in last_turn debug payload for scenario={scenario.id}\n"
        f"transcript={transcript_path}"
    )

    body_preview = str(last_turn.get("body_preview", "") or "")
    lowered_body = body_preview.lower()
    for token in scenario.forbidden_body_tokens:
        assert token not in lowered_body, (
            f"unexpected response token for scenario={scenario.id}: {token}\n"
            f"body_preview={body_preview}\n"
            f"transcript={transcript_path}"
        )

    tool_results = parse_tool_results(metadata.get("tool_results"))
    assert tool_results, (
        f"no tool results recorded for scenario={scenario.id}\n"
        f"metadata={json.dumps(metadata, indent=2, sort_keys=True)}\n"
        f"transcript={transcript_path}"
    )

    executed_tool_names = {
        str(item.get("tool_name", "")).strip()
        for item in tool_results
        if str(item.get("tool_name", "")).strip()
    }
    assert executed_tool_names, (
        f"tool results missing tool_name for scenario={scenario.id}\n"
        f"tool_results={json.dumps(tool_results, indent=2, sort_keys=True)}\n"
        f"transcript={transcript_path}"
    )
    assert _matches_expected_tool(
        executed_tool_names=executed_tool_names,
        expected_tools=scenario.expected_tools,
    ), (
        f"unexpected tool execution for scenario={scenario.id}\n"
        f"expected_any={scenario.expected_tools}\n"
        f"executed={sorted(executed_tool_names)}\n"
        f"tool_results={json.dumps(tool_results, indent=2, sort_keys=True)}\n"
        f"transcript={transcript_path}"
    )

    tool_execution_count = int(
        str(metadata.get("tool_execution_count", "0")).strip() or "0"
    )
    assert tool_execution_count >= 1, (
        f"tool_execution_count was not recorded for scenario={scenario.id}\n"
        f"metadata={json.dumps(metadata, indent=2, sort_keys=True)}\n"
        f"transcript={transcript_path}"
    )
