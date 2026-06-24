from __future__ import annotations

import json

import pytest

from tests.helpers.live_cli_chat_alibaba import (
    RAW_TOOL_MARKUP_RE,
    extract_all_debug_payloads,
    extract_assistant_messages,
    framework_root,
    parse_tool_results,
    run_cli_session,
)
from tests.helpers.live_e2e_profiles import resolve_live_config_path

pytestmark = pytest.mark.e2e

_OFFICIAL_CONFIG = resolve_live_config_path(
    "per-agent-minimax-official.json",
    framework_root(),
)
_OFFICIAL_AGENT_IDS = ("minimax-m2-5", "minimax-m2-7")


def _executed_tool_names(tool_results: list[dict]) -> set[str]:
    return {
        str(item.get("tool_name", "")).strip()
        for item in tool_results
        if str(item.get("tool_name", "")).strip()
    }


@pytest.mark.e2e
@pytest.mark.parametrize("agent_id", _OFFICIAL_AGENT_IDS)
@pytest.mark.timeout(180)
def test_live_cli_chat_minimax_official_memory_tool_interleave(agent_id: str) -> None:
    result = run_cli_session(
        session_id_prefix=f"live-cli-official-memory-tool-interleave-{agent_id}",
        user_input="\n".join(
            (
                "remember: my favorite shell is zsh",
                "/debug",
                "what time is it in UTC right now?",
                "/debug",
                "what shell did i say is my favorite? answer with only the shell name.",
                "/debug",
                "/exit",
            )
        )
        + "\n",
        agent_id=agent_id,
        config_path=_OFFICIAL_CONFIG,
    )

    transcript = result.transcript
    transcript_path = result.transcript_path

    assert f"chat ready agent={agent_id}" in transcript, (
        f"missing chat ready marker\ntranscript={transcript_path}"
    )
    assert not RAW_TOOL_MARKUP_RE.search(transcript), (
        f"raw tool markup leaked\ntranscript={transcript_path}"
    )

    assistant_messages = extract_assistant_messages(
        transcript=transcript,
        session_id=result.session_id,
        agent_id=agent_id,
    )
    assert len(assistant_messages) >= 3, (
        f"expected at least 3 assistant turns, got {len(assistant_messages)}\n"
        f"transcript={transcript_path}"
    )

    debug_payloads = extract_all_debug_payloads(transcript)
    assert len(debug_payloads) >= 3, (
        f"expected at least 3 /debug payloads, got {len(debug_payloads)}\n"
        f"transcript={transcript_path}"
    )

    tool_turn_payload = debug_payloads[1]
    tool_turn = tool_turn_payload.get("last_turn")
    assert isinstance(tool_turn, dict), (
        f"second debug payload missing last_turn\n"
        f"payload={json.dumps(tool_turn_payload, indent=2, sort_keys=True)}\n"
        f"transcript={transcript_path}"
    )
    tool_metadata = tool_turn.get("metadata")
    assert isinstance(tool_metadata, dict), (
        f"second debug payload missing metadata\n"
        f"last_turn={json.dumps(tool_turn, indent=2, sort_keys=True)}\n"
        f"transcript={transcript_path}"
    )
    tool_results = parse_tool_results(tool_metadata.get("tool_results"))
    assert tool_results, (
        f"tool-backed turn did not record tool_results\n"
        f"metadata={json.dumps(tool_metadata, indent=2, sort_keys=True)}\n"
        f"transcript={transcript_path}"
    )
    executed_tool_names = _executed_tool_names(tool_results)
    assert executed_tool_names, (
        f"tool-backed turn missing tool_name\n"
        f"tool_results={json.dumps(tool_results, indent=2, sort_keys=True)}\n"
        f"transcript={transcript_path}"
    )
    assert (
        "time" in executed_tool_names
        or "time.now" in executed_tool_names
        or any(name.endswith(".time.now") for name in executed_tool_names)
    ), (
        f"unexpected tool execution during tool-backed turn\n"
        f"executed={sorted(executed_tool_names)}\n"
        f"tool_results={json.dumps(tool_results, indent=2, sort_keys=True)}\n"
        f"transcript={transcript_path}"
    )
    tool_execution_count = int(
        str(tool_metadata.get("tool_execution_count", "0")).strip() or "0"
    )
    assert tool_execution_count >= 1, (
        f"tool_execution_count missing for tool-backed turn\n"
        f"metadata={json.dumps(tool_metadata, indent=2, sort_keys=True)}\n"
        f"transcript={transcript_path}"
    )

    recall_payload = debug_payloads[2]
    recall_turn = recall_payload.get("last_turn")
    assert isinstance(recall_turn, dict), (
        f"third debug payload missing last_turn\n"
        f"payload={json.dumps(recall_payload, indent=2, sort_keys=True)}\n"
        f"transcript={transcript_path}"
    )
    recall_body = (
        assistant_messages[-1] or str(recall_turn.get("body_preview", "") or "")
    ).lower()
    assert "zsh" in recall_body, (
        f"memory recall did not survive tool-backed turn\n"
        f"assistant_messages={json.dumps(assistant_messages, indent=2)}\n"
        f"recall_turn={json.dumps(recall_turn, indent=2, sort_keys=True)}\n"
        f"transcript={transcript_path}"
    )
