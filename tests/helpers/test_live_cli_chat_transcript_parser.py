from __future__ import annotations

import json

from tests.helpers.live_cli_chat_alibaba import (
    extract_all_debug_payloads,
    extract_assistant_messages,
    extract_debug_payloads,
    extract_last_debug_payload,
)


_SESSION_ID = "test-session-01"
_AGENT_ID = "test-agent"


def test_legacy_prose_marker_path_extracts_assistant_body() -> None:
    transcript = (
        f"[chat ready] agent={_AGENT_ID} session={_SESSION_ID}\n"
        f"[{_SESSION_ID}|{_AGENT_ID}] you> hello\n"
        f"[{_SESSION_ID}|{_AGENT_ID}] {_AGENT_ID}: Hi there! How can I help today?\n"
        f"[chat] [tokens 100/20 | calls 1 llm, 0 tools (0 err) | 1.0s]\n"
        f"[{_SESSION_ID}|{_AGENT_ID}] you> /exit\n"
    )

    result = extract_assistant_messages(
        transcript=transcript, session_id=_SESSION_ID, agent_id=_AGENT_ID
    )

    assert result == ["Hi there! How can I help today?"]


def test_structured_debug_path_extracts_body_preview_when_prose_absent() -> None:
    transcript = (
        "(openminion chat is a compatibility alias; use bare `openminion` "
        "for interactive use.)\n"
        f"[chat ready] agent={_AGENT_ID} session={_SESSION_ID}\n"
        f"[{_SESSION_ID}|{_AGENT_ID}] you> what's the weather?\n"
        "{\n"
        '  "last_turn": {\n'
        '    "body_preview": "test-agent: I can check the weather but need a location first. Which city?",\n'
        '    "run_state": "completed",\n'
        '    "turn_duration_ms": 1234\n'
        "  },\n"
        '  "run_state": "completed"\n'
        "}\n"
        f"[{_SESSION_ID}|{_AGENT_ID}] you> /exit\n"
    )

    result = extract_assistant_messages(
        transcript=transcript, session_id=_SESSION_ID, agent_id=_AGENT_ID
    )

    # The namespace prefix ``"test-agent: "`` is stripped so the caller
    # sees the same shape as the prose-marker path.
    assert result == ["I can check the weather but need a location first. Which city?"]


def test_i09_extract_debug_payloads_selector_keeps_compat_wrappers() -> None:
    transcript = (
        "noise\n"
        + json.dumps({"last_turn": {"body_preview": "first"}, "ordinal": 1})
        + "\nmore noise\n"
        + json.dumps({"last_turn": {"body_preview": "second"}, "ordinal": 2})
    )

    all_payloads = extract_debug_payloads(transcript, which="all")
    assert isinstance(all_payloads, list)
    assert [payload["ordinal"] for payload in all_payloads] == [1, 2]

    first = extract_debug_payloads(transcript, which="first")
    last = extract_debug_payloads(transcript, which="last")
    assert isinstance(first, dict)
    assert isinstance(last, dict)
    assert first["ordinal"] == 1
    assert last["ordinal"] == 2

    assert extract_all_debug_payloads(transcript) == all_payloads
    assert extract_last_debug_payload(transcript) == last


def test_i09_extract_debug_payloads_unknown_selector_rejected() -> None:
    transcript = json.dumps({"last_turn": {"body_preview": "x"}})

    try:
        extract_debug_payloads(transcript, which="middle")  # type: ignore[arg-type]
    except ValueError as exc:
        assert "unknown debug payload selector" in str(exc)
    else:
        raise AssertionError("expected unknown selector to raise ValueError")


def test_empty_or_malformed_transcript_returns_empty_list() -> None:
    for transcript in (
        "",
        "no markers at all",
        '{"last_turn": "not-a-dict"}',
        '{"last_turn": {"body_preview": ""}}',
        '{"last_turn": {"body_preview": "   "}}',
        '{"last_turn": {"body_preview": null}}',
        '{"last_turn": {}}',  # missing body_preview key
        '{"last_turn": {',  # truncated JSON
    ):
        result = extract_assistant_messages(
            transcript=transcript, session_id=_SESSION_ID, agent_id=_AGENT_ID
        )
        assert result == [], f"expected empty list for transcript={transcript!r}"


def test_both_shapes_present_prefers_prose_marker_for_back_compat() -> None:
    transcript = (
        f"[chat ready] agent={_AGENT_ID} session={_SESSION_ID}\n"
        f"[{_SESSION_ID}|{_AGENT_ID}] you> hello\n"
        f"[{_SESSION_ID}|{_AGENT_ID}] {_AGENT_ID}: Hi from prose marker.\n"
        "[chat] [tokens 100/20 | calls 1 llm, 0 tools (0 err) | 1.0s]\n"
        "{\n"
        '  "last_turn": {\n'
        '    "body_preview": "test-agent: Hi from structured body_preview.",\n'
        '    "run_state": "completed"\n'
        "  }\n"
        "}\n"
        f"[{_SESSION_ID}|{_AGENT_ID}] you> /exit\n"
    )

    result = extract_assistant_messages(
        transcript=transcript, session_id=_SESSION_ID, agent_id=_AGENT_ID
    )

    # Prose-marker path wins; structured path is fallback only.
    assert result == ["Hi from prose marker."]


def test_runtime_control_prose_falls_back_to_structured_answer() -> None:
    transcript = (
        f"[chat ready] agent={_AGENT_ID} session={_SESSION_ID}\n"
        f"[{_SESSION_ID}|{_AGENT_ID}] you> run test\n"
        f"[{_SESSION_ID}|{_AGENT_ID}] {_AGENT_ID}: Policy confirmation required.\n"
        "exec.run (command=python -m pytest -q tests)\n"
        "Reply exactly yes to confirm or exactly no to cancel.\n"
        "[chat] [tokens 100/20 | calls 1 llm, 1 tools (0 err) | 1.0s]\n"
        + json.dumps(
            {
                "last_turn": {
                    "body": (
                        "test-agent: SOURCES\n- source\n\nCHANGES\n- change\n\n"
                        "TESTS\n- pass"
                    ),
                    "body_preview": "test-agent: SOURCES\n- source",
                    "metadata": {"respond_kind": "assistant"},
                    "run_state": "completed",
                }
            }
        )
        + f"\n[{_SESSION_ID}|{_AGENT_ID}] you> /exit\n"
    )

    result = extract_assistant_messages(
        transcript=transcript,
        session_id=_SESSION_ID,
        agent_id=_AGENT_ID,
        include_policy_confirmation_prompt=False,
    )

    assert result == ["SOURCES\n- source\n\nCHANGES\n- change\n\nTESTS\n- pass"]


def test_policy_confirmation_prompt_is_visible_by_default_from_structured_debug() -> (
    None
):
    transcript = json.dumps(
        {
            "last_turn": {
                "body": (
                    "test-agent: Policy confirmation required.\n"
                    "file.write (...)\nReply exactly yes"
                ),
                "body_preview": "test-agent: Policy confirmation required.",
                "metadata": {"respond_kind": "policy_confirmation_prompt"},
                "run_state": "completed",
            }
        }
    )

    result = extract_assistant_messages(
        transcript=transcript, session_id=_SESSION_ID, agent_id=_AGENT_ID
    )

    assert result == [
        "Policy confirmation required.\nfile.write (...)\nReply exactly yes"
    ]


def test_policy_confirmation_prompt_can_be_excluded_from_structured_debug() -> None:
    transcript = json.dumps(
        {
            "last_turn": {
                "body": (
                    "test-agent: Policy confirmation required.\n"
                    "file.write (...)\nReply exactly yes"
                ),
                "body_preview": "test-agent: Policy confirmation required.",
                "metadata": {"respond_kind": "policy_confirmation_prompt"},
                "run_state": "completed",
            }
        }
    )

    result = extract_assistant_messages(
        transcript=transcript,
        session_id=_SESSION_ID,
        agent_id=_AGENT_ID,
        include_policy_confirmation_prompt=False,
    )

    assert result == []


def test_policy_confirmation_prompt_is_visible_by_default_from_prose() -> None:
    transcript = (
        f"[chat ready] agent={_AGENT_ID} session={_SESSION_ID}\n"
        f"[{_SESSION_ID}|{_AGENT_ID}] you> run pwd\n"
        f"[{_SESSION_ID}|{_AGENT_ID}] {_AGENT_ID}: Policy confirmation required.\n"
        "exec.run (command=pwd)\n"
        "Reply exactly yes to confirm or exactly no to cancel.\n"
        "[chat] [tokens 100/20 | calls 1 llm, 1 tools (0 err) | 1.0s]\n"
    )

    result = extract_assistant_messages(
        transcript=transcript, session_id=_SESSION_ID, agent_id=_AGENT_ID
    )

    assert result == [
        "Policy confirmation required.\n"
        "exec.run (command=pwd)\n"
        "Reply exactly yes to confirm or exactly no to cancel."
    ]


def test_structured_path_handles_nested_braces_in_body_preview() -> None:
    transcript = (
        "{\n"
        '  "last_turn": {\n'
        '    "body_preview": "test-agent: Here is JSON: {\\"key\\": \\"value\\"}",\n'
        '    "run_state": "completed"\n'
        "  }\n"
        "}\n"
    )

    result = extract_assistant_messages(
        transcript=transcript, session_id=_SESSION_ID, agent_id=_AGENT_ID
    )

    assert result == ['Here is JSON: {"key": "value"}']


def test_omcti_03_structured_path_prefers_full_body_over_truncated_preview() -> None:
    full_body = (
        "test-agent: **PLAN**\n\n1. Search the docs.\n2. Compare both tools.\n\n"
        "**TABLE**\n\n| Tool | Behavior |\n|---|---|\n| uv | fast |\n| pipx | isolated |\n\n"
        "**UNCERTAINTIES**\n\nNone — official docs are authoritative.\n"
    )
    preview = full_body[:200]
    transcript = json.dumps(
        {
            "last_turn": {
                "body": full_body,
                "body_preview": preview,
                "run_state": "completed",
            }
        }
    )

    result = extract_assistant_messages(
        transcript=transcript, session_id=_SESSION_ID, agent_id=_AGENT_ID
    )

    assert len(result) == 1
    # Full body, not the 200-char preview.
    assert "**PLAN**" in result[0]
    assert "**TABLE**" in result[0]
    assert "**UNCERTAINTIES**" in result[0]
    # And the agent-id namespace prefix is stripped (same as for preview).
    assert result[0].startswith("**PLAN**")


def test_omcti_03_structured_path_falls_back_to_preview_when_body_absent() -> None:
    transcript = (
        "{\n"
        '  "last_turn": {\n'
        '    "body_preview": "test-agent: Pre-OMCTI-03 preview-only shape.",\n'
        '    "run_state": "completed"\n'
        "  }\n"
        "}\n"
    )

    result = extract_assistant_messages(
        transcript=transcript, session_id=_SESSION_ID, agent_id=_AGENT_ID
    )

    assert result == ["Pre-OMCTI-03 preview-only shape."]


def test_omcti_03_empty_body_falls_back_to_preview() -> None:
    transcript = (
        "{\n"
        '  "last_turn": {\n'
        '    "body": "   ",\n'
        '    "body_preview": "test-agent: Real preview content.",\n'
        '    "run_state": "completed"\n'
        "  }\n"
        "}\n"
    )

    result = extract_assistant_messages(
        transcript=transcript, session_id=_SESSION_ID, agent_id=_AGENT_ID
    )

    assert result == ["Real preview content."]


def test_structured_path_keeps_body_when_namespace_prefix_absent() -> None:
    transcript = (
        "{\n"
        '  "last_turn": {\n'
        '    "body_preview": "No namespace prefix here.",\n'
        '    "run_state": "completed"\n'
        "  }\n"
        "}\n"
    )

    result = extract_assistant_messages(
        transcript=transcript, session_id=_SESSION_ID, agent_id=_AGENT_ID
    )

    # No agent-id prefix to strip; body returned verbatim.
    assert result == ["No namespace prefix here."]
