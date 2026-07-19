from __future__ import annotations

from pathlib import Path


from openminion.base.types import Message
from openminion.modules.llm.providers.base import ProviderHistoryMessage
from openminion.services.brain.post_execution import followup as followup_module
from openminion.services.brain.post_execution.followup import (
    _build_runtime_facts_message,
    _build_tool_follow_up_history,
    _dated_evidence_lines_from_tool_results,
)


def _user_message(body: str = "what are the latest market headlines?") -> Message:
    return Message(channel="console", target="chat", body=body)


def test_cdeg02_runtime_facts_message_always_includes_current_datetime() -> None:

    message = _build_runtime_facts_message(tool_results=[])
    assert isinstance(message, ProviderHistoryMessage)
    assert message.role == "system"
    assert message.content.startswith("current_datetime=")
    # ISO 8601 form carries digits + "T" separator.
    iso_line = message.content.splitlines()[0]
    assert "T" in iso_line and ":" in iso_line


def test_cdeg02_runtime_facts_message_extracts_published_at_from_top_level_data() -> (
    None
):

    tool_results = [
        {
            "tool_name": "web.search",
            "ok": True,
            "content": "recent headlines",
            "data": {
                "source": "serpapi",
                "published_at": "2026-04-18T12:00:00Z",
                "results": [],
            },
        }
    ]
    message = _build_runtime_facts_message(tool_results=tool_results)
    assert message is not None
    assert "current_datetime=" in message.content
    assert "evidence_date=2026-04-18T12:00:00Z" in message.content


def test_cdeg02_runtime_facts_message_extracts_nested_result_dates() -> None:

    tool_results = [
        {
            "tool_name": "web.search",
            "ok": True,
            "data": {
                "source": "serpapi",
                "results": [
                    {"title": "no date"},
                    {"title": "has date", "published_at": "2026-04-10"},
                ],
            },
        }
    ]
    message = _build_runtime_facts_message(tool_results=tool_results)
    assert message is not None
    assert "evidence_date=2026-04-10" in message.content


def test_cdeg02_runtime_facts_message_emits_at_most_one_evidence_line_per_tool() -> (
    None
):

    tool_results = [
        {
            "tool_name": "web.search",
            "ok": True,
            "data": {"published_at": "2026-04-18T12:00:00Z"},
        },
        {
            "tool_name": "web.search.alt",
            "ok": True,
            "data": {"published_at": "2026-04-18T12:00:00Z"},
        },
    ]
    lines = _dated_evidence_lines_from_tool_results(tool_results)
    assert len(lines) == 2
    assert all(line.startswith("evidence_date=2026-04-18T12:00:00Z") for line in lines)


def test_cdeg02_runtime_facts_message_dedup_same_tool_same_value() -> None:

    tool_results = [
        {
            "tool_name": "web.search",
            "ok": True,
            "data": {"published_at": "2026-04-18T12:00:00Z"},
        },
        {
            "tool_name": "web.search",
            "ok": True,
            "data": {"published_at": "2026-04-18T12:00:00Z"},
        },
    ]
    lines = _dated_evidence_lines_from_tool_results(tool_results)
    assert lines == ["evidence_date=2026-04-18T12:00:00Z (tool=web.search)"]


def test_cdeg02_runtime_facts_message_no_dated_fields_emits_only_current_datetime() -> (
    None
):

    tool_results = [
        {
            "tool_name": "exec.run",
            "ok": True,
            "content": "ls output",
            "data": {"exit_code": 0, "stdout": "file.txt"},
        }
    ]
    message = _build_runtime_facts_message(tool_results=tool_results)
    assert message is not None
    lines = message.content.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("current_datetime=")


def test_cdeg02_build_tool_follow_up_history_injects_runtime_facts_message() -> None:

    tool_results = [
        {
            "tool_name": "web.search",
            "ok": True,
            "content": "market headlines from April 2026",
            "data": {
                "source": "serpapi",
                "published_at": "2026-04-18T12:00:00Z",
            },
        }
    ]
    provider_history = _build_tool_follow_up_history(
        message=_user_message(),
        history=None,
        prior_assistant_text="I'll search for that.",
        tool_results=tool_results,
    )
    system_messages = [
        msg for msg in provider_history if str(msg.role).lower() == "system"
    ]
    assert system_messages, (
        "CDEG-02: a runtime-facts system message must appear in the "
        "post-tool synthesis history."
    )
    typed_facts = next(
        (
            msg
            for msg in system_messages
            if "current_datetime=" in str(msg.content or "")
        ),
        None,
    )
    assert typed_facts is not None
    assert "evidence_date=2026-04-18T12:00:00Z" in typed_facts.content
    assistant_messages = [
        msg for msg in provider_history if str(msg.role).lower() == "assistant"
    ]
    assert assistant_messages, "expected assistant pre-tool draft context in history"
    assert "not the final answer" in str(assistant_messages[-1].content or "")
    user_messages = [msg for msg in provider_history if str(msg.role).lower() == "user"]
    assert user_messages, "expected user tool-feedback message in history"
    assert str(user_messages[-1].content or "").startswith("Tool execution results:\n")


_BANNED_IMPERATIVE_SUBSTRINGS = (
    # Imperative recency guidance — per LOSG §3.4 not-allowed and
    # LFRH-109 / CDEG spec.
    "always use the current date when reasoning",
    "use the current date when reasoning",
    "this request is time-sensitive",
    "this request is freshness-sensitive",
    "freshness-sensitive",
    "user is asking about recent",
    "user is asking for recent",
    "avoid stale information",
    "prefer recent sources",
    "the current year is",
    # Regex final-answer year rewrite hints.
    "replace_year",
    "rewrite_year",
    "final_answer_year_rewrite",
    "force_search",
    "forced_search_lane",
    "require_search_for_recent",
    # Keyword freshness classifier hints.
    "freshness_signal",
    "freshness_classifier",
    "_is_freshness_sensitive",
    "_detect_freshness",
)


def test_cdeg03_followup_source_has_no_banned_imperative_strings() -> None:

    source_path = Path(followup_module.__file__)
    source = source_path.read_text(encoding="utf-8")
    for banned in _BANNED_IMPERATIVE_SUBSTRINGS:
        assert banned.lower() not in source.lower(), (
            "CDEG-03: forbidden imperative-recency / freshness-classifier / "
            f"forced-search / year-rewrite substring {banned!r} appeared in "
            f"{source_path.name}. Under the hard LOSG rule, runtime must "
            "transport typed date facts only; the LLM owns temporal "
            "interpretation."
        )


def test_cdeg03_runtime_facts_message_is_data_bearing_not_instruction_bearing() -> None:

    tool_results = [
        {
            "tool_name": "web.search",
            "ok": True,
            "data": {"published_at": "2026-04-18T12:00:00Z"},
        }
    ]
    message = _build_runtime_facts_message(tool_results=tool_results)
    assert message is not None
    for line in message.content.splitlines():
        # Each line must be a typed key=value fact (ignoring an optional
        # parenthesized tool-name tag appended after the value).
        assert "=" in line, (
            f"CDEG-03: runtime-facts line {line!r} is not a typed key=value fact."
        )
        key, _, _value = line.partition("=")
        key = key.strip()
        # Keys must be simple snake_case identifiers, not prose verbs.
        assert key.replace("_", "").isalnum(), (
            f"CDEG-03: runtime-facts key {key!r} is not a simple typed field name."
        )


def test_cdeg03_build_tool_follow_up_history_has_no_imperative_system_messages() -> (
    None
):

    tool_results = [
        {
            "tool_name": "web.search",
            "ok": True,
            "content": "headlines",
            "data": {
                "source": "serpapi",
                "published_at": "2026-04-18T12:00:00Z",
            },
        }
    ]
    provider_history = _build_tool_follow_up_history(
        message=_user_message(),
        history=None,
        prior_assistant_text="searching",
        tool_results=tool_results,
    )
    system_messages = [
        str(msg.content or "")
        for msg in provider_history
        if str(msg.role).lower() == "system"
    ]
    all_system_text = "\n".join(system_messages).lower()
    for banned in _BANNED_IMPERATIVE_SUBSTRINGS:
        assert banned.lower() not in all_system_text, (
            f"CDEG-03: follow-up history emitted forbidden imperative "
            f"substring {banned!r} in a system message. Runtime must "
            "transport typed facts only."
        )
