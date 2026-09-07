from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from openminion.tools.task.args import TaskWatchArgs
from openminion.tools.task.routine.schemas import RoutinePayloadV1
from openminion.tools.task.routine.social import (
    RssAtomSourceV1,
    SocialSignalConfigV1,
    SocialSignalCursorV1,
    SocialSignalHandler,
    YouTubeFeedSourceV1,
    parse_public_feed,
)

_PUBLISHED_AT = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
_ATOM = f"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>tag:example.com,2026:item-1</id>
    <title>Release &lt;b&gt;ready&lt;/b&gt;</title>
    <link href="https://example.com/posts/1" />
    <published>{_PUBLISHED_AT}</published>
    <author><name>OpenMinion</name></author>
    <summary>&lt;script&gt;ignored&lt;/script&gt; Stable release.</summary>
  </entry>
</feed>"""

_OLD_RSS = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel><item>
  <guid>old-item</guid>
  <title>Old release</title>
  <link>https://example.com/posts/old</link>
  <pubDate>Wed, 01 Jan 2020 00:00:00 GMT</pubDate>
  <description>Outside the active lookback window.</description>
</item></channel></rss>"""


class _Context:
    def __init__(
        self,
        responses: list[Mapping[str, Any]],
        *,
        exact_provider: bool = False,
    ) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.exact_provider = exact_provider

    def invoke_tool(self, *, name: str, args: Mapping[str, Any]) -> Mapping[str, Any]:
        self.calls.append((name, dict(args)))
        return self.responses.pop(0)

    def exact_provider_enabled(self, *, family: str, provider_id: str) -> bool:
        return self.exact_provider and family == "search" and provider_id == "brave"


def _source() -> RssAtomSourceV1:
    return RssAtomSourceV1(
        source_id="openminion",
        label="OpenMinion releases",
        url="https://example.com/releases.atom",
        allowed_final_origins=["example.com"],
    )


def _routine(*, discovery: bool = False) -> RoutinePayloadV1:
    return RoutinePayloadV1(
        routine_kind="social_signal",
        config=SocialSignalConfigV1(
            sources=[_source()],
            topics=["OpenMinion release activity"],
            discovery_mode="on_direct_failure" if discovery else "disabled",
            discovery_provider_id="brave" if discovery else None,
        ),
        cursor=SocialSignalCursorV1(),
    )


def _fetch_success() -> dict[str, Any]:
    return {
        "ok": True,
        "data": {
            "status_code": 200,
            "final_url": "https://example.com/releases.atom",
            "text_preview": _ATOM,
            "validators": {"etag": '"v1"'},
        },
    }


def test_social_payload_round_trips_as_registered_routine() -> None:
    routine = _routine()
    revived = RoutinePayloadV1.model_validate(routine.model_dump(mode="json"))

    assert revived.routine_kind == "social_signal"
    assert revived.config.sources[0].source_id == "openminion"
    assert revived.cursor.sources == {}


def test_handler_authorizes_search_only_when_discovery_is_enabled() -> None:
    handler = SocialSignalHandler()

    assert handler.pre_turn_tools_for(_routine()) == ("fetch.get",)
    assert handler.pre_turn_tools_for(_routine(discovery=True)) == (
        "fetch.get",
        "search.dispatch",
    )


def test_social_config_rejects_credentials_and_x_sources() -> None:
    with pytest.raises(ValidationError):
        RssAtomSourceV1(
            source_id="bad",
            label="bad",
            url="https://user:secret@example.com/feed",
            allowed_final_origins=["example.com"],
        )
    with pytest.raises(ValidationError):
        RssAtomSourceV1(
            source_id="x",
            label="X",
            url="https://x.com/feed",
            allowed_final_origins=["x.com"],
        )
    with pytest.raises(ValidationError):
        SocialSignalConfigV1(sources=[_source()], topics=["x" * 241])


def test_youtube_source_derives_canonical_public_feed() -> None:
    source = YouTubeFeedSourceV1(
        source_id="channel",
        label="Channel",
        channel_id="UC123",
    )
    assert source.resolved_url().endswith("channel_id=UC123")


def test_parser_accepts_atom_and_sanitizes_markup() -> None:
    observations = parse_public_feed(
        _ATOM,
        source=_source(),
        observed_at="2026-09-05T13:00:00Z",
        limit=10,
    )
    assert len(observations) == 1
    assert observations[0].external_id == "tag:example.com,2026:item-1"
    assert observations[0].title == "Release ready"
    assert "<script>" not in observations[0].excerpt
    assert observations[0].canonical_url == "https://example.com/posts/1"


@pytest.mark.parametrize(
    "body",
    [
        "<!DOCTYPE feed [<!ENTITY xxe SYSTEM 'file:///etc/passwd'>]><feed/>",
        "<feed><entry></feed>",
    ],
)
def test_parser_rejects_unsafe_or_malformed_xml(body: str) -> None:
    with pytest.raises(ValueError):
        parse_public_feed(
            body,
            source=_source(),
            observed_at="2026-09-05T13:00:00Z",
            limit=10,
        )


def test_parser_rejects_excessive_depth_without_recursion_failure() -> None:
    body = "<feed>" + "<node>" * 1_100 + "</node>" * 1_100 + "</feed>"

    with pytest.raises(ValueError, match="maximum depth"):
        parse_public_feed(
            body,
            source=_source(),
            observed_at="2026-09-05T13:00:00Z",
            limit=10,
        )


def test_handler_collects_direct_facts_and_advances_proposed_cursor() -> None:
    handler = SocialSignalHandler()
    ctx = _Context([_fetch_success()])
    facts = handler.pre_turn(routine=_routine(), routine_id="job-1", ctx=ctx)

    assert [name for name, _ in ctx.calls] == ["fetch.get"]
    assert ctx.calls[0][1]["prefer_backend"] == "core-http"
    assert len(facts.observations) == 1
    assert facts.coverage == "ok"
    cursor = facts.proposed_cursor.sources["openminion"]
    assert cursor.etag == '"v1"'
    assert cursor.recent_keys == [
        f"{facts.observations[0].evidence_id}:{facts.observations[0].content_hash}"
    ]


def test_handler_applies_lookback_to_standard_rss_dates() -> None:
    response = _fetch_success()
    response["data"]["text_preview"] = _OLD_RSS

    facts = SocialSignalHandler().pre_turn(
        routine=_routine(), routine_id="job-1", ctx=_Context([response])
    )

    assert facts.observations == []
    assert facts.source_runs[0].attempted_count == 1
    assert facts.source_runs[0].accepted_count == 0


def test_handler_deduplicates_previously_committed_observation() -> None:
    handler = SocialSignalHandler()
    first = handler.pre_turn(
        routine=_routine(), routine_id="job-1", ctx=_Context([_fetch_success()])
    )
    routine = _routine().model_copy(update={"cursor": first.proposed_cursor})
    second = handler.pre_turn(
        routine=routine, routine_id="job-1", ctx=_Context([_fetch_success()])
    )
    assert second.observations == []


def test_handler_tells_model_to_leave_evidence_empty_without_observations() -> None:
    handler = SocialSignalHandler()
    facts = handler.pre_turn(
        routine=_routine(),
        routine_id="job-1",
        ctx=_Context([_fetch_success()]),
    )
    facts = facts.model_copy(update={"observations": []})

    prompt = handler.render_turn(check_instruction="Check releases.", facts=facts)

    assert "when observations is empty, both evidence lists must be empty" in prompt


def test_handler_accepts_content_revision_for_existing_external_id() -> None:
    handler = SocialSignalHandler()
    first = handler.pre_turn(
        routine=_routine(), routine_id="job-1", ctx=_Context([_fetch_success()])
    )
    revised = _fetch_success()
    revised["data"]["text_preview"] = _ATOM.replace(
        "Stable release.", "Patched release."
    )
    routine = _routine().model_copy(update={"cursor": first.proposed_cursor})

    second = handler.pre_turn(
        routine=routine, routine_id="job-1", ctx=_Context([revised])
    )

    assert len(second.observations) == 1
    assert second.observations[0].evidence_id == first.observations[0].evidence_id
    assert second.observations[0].content_hash != first.observations[0].content_hash


def test_handler_records_rate_limit_skip_without_retrying_source() -> None:
    handler = SocialSignalHandler()
    limited_until = "2099-01-01T00:00:00Z"
    first = handler.pre_turn(
        routine=_routine(),
        routine_id="job-1",
        ctx=_Context(
            [
                {
                    "ok": False,
                    "error": {
                        "code": "RATE_LIMITED",
                        "details": {"next_eligible_at": limited_until},
                    },
                }
            ]
        ),
    )
    routine = _routine().model_copy(update={"cursor": first.proposed_cursor})
    ctx = _Context([])

    second = handler.pre_turn(routine=routine, routine_id="job-1", ctx=ctx)

    assert ctx.calls == []
    assert second.source_runs[0].error_code == "RATE_LIMITED"
    assert (
        second.proposed_cursor.sources["openminion"].next_eligible_at == limited_until
    )


def test_handler_treats_not_modified_as_successful_state_only_check() -> None:
    prior = SocialSignalCursorV1.model_validate(
        {
            "sources": {
                "openminion": {
                    "etag": '"v1"',
                    "recent_keys": ["existing"],
                    "consecutive_failures": 1,
                }
            }
        }
    )
    routine = _routine().model_copy(update={"cursor": prior})
    ctx = _Context(
        [
            {
                "ok": True,
                "data": {
                    "status_code": 304,
                    "final_url": "https://example.com/releases.atom",
                },
            }
        ]
    )

    facts = SocialSignalHandler().pre_turn(routine=routine, routine_id="job-1", ctx=ctx)

    assert facts.coverage == "ok"
    assert facts.observations == []
    assert facts.proposed_cursor.sources["openminion"].recent_keys == ["existing"]
    assert ctx.calls[0][1]["headers"] == {"If-None-Match": '"v1"'}


def test_handler_validates_direct_evidence_before_requesting_artifact() -> None:
    handler = SocialSignalHandler()
    routine = _routine()
    facts = handler.pre_turn(
        routine=routine, routine_id="job-1", ctx=_Context([_fetch_success()])
    )
    evidence_id = facts.observations[0].evidence_id
    outcome = {
        "schema_version": 1,
        "condition_met": True,
        "summary": "A new release was published.",
        "evidence_ids": [evidence_id],
        "resolution_evidence_ids": [],
        "limitations": [],
        "conflicts": [],
    }

    result = handler.post_turn(
        routine=routine,
        routine_id="job-1",
        facts=facts,
        outcome_text=f"<routine_outcome>{json.dumps(outcome)}</routine_outcome>",
    )

    assert result.ok is True
    assert result.condition_value is True
    assert result.artifact_body is not None
    assert result.updated_routine.cursor.sources["openminion"].recent_keys


def test_handler_rejects_unknown_evidence_without_advancing_cursor() -> None:
    handler = SocialSignalHandler()
    routine = _routine()
    facts = handler.pre_turn(
        routine=routine, routine_id="job-1", ctx=_Context([_fetch_success()])
    )
    outcome = {
        "schema_version": 1,
        "condition_met": True,
        "summary": "Unsupported claim.",
        "evidence_ids": ["missing"],
    }
    result = handler.post_turn(
        routine=routine,
        routine_id="job-1",
        facts=facts,
        outcome_text=f"<routine_outcome>{json.dumps(outcome)}</routine_outcome>",
    )
    assert result.ok is False
    assert result.reason_code == "unsupported_evidence_reference"
    assert result.updated_routine is None


def test_discovery_uses_exact_provider_and_does_not_enter_model_facts() -> None:
    handler = SocialSignalHandler()
    ctx = _Context(
        [
            {"ok": False, "error": {"code": "UPSTREAM_ERROR", "details": {}}},
            {
                "ok": True,
                "data": {
                    "provider": "brave",
                    "results": [
                        {
                            "title": "Private provider text",
                            "url": "https://outside.example/releases.atom",
                        }
                    ],
                },
            },
        ],
        exact_provider=True,
    )
    facts = handler.pre_turn(
        routine=_routine(discovery=True), routine_id="job-1", ctx=ctx
    )
    assert [name for name, _ in ctx.calls] == ["fetch.get", "search.dispatch"]
    assert ctx.calls[1][1]["provider"] == "brave"
    assert facts.discovery_attempted is True
    assert "Private provider text" not in facts.model_dump_json()
    assert facts.observations == []


def test_discovery_upgrades_only_an_approved_feed_candidate() -> None:
    handler = SocialSignalHandler()
    ctx = _Context(
        [
            {"ok": False, "error": {"code": "UPSTREAM_ERROR", "details": {}}},
            {
                "ok": True,
                "data": {
                    "provider": "brave",
                    "results": [
                        {
                            "title": "Untrusted title",
                            "url": "https://evil.example/feed",
                        },
                        {
                            "title": "Provider-only title",
                            "url": "https://example.com/discovered.atom",
                        },
                    ],
                },
            },
            {
                "ok": True,
                "data": {
                    "status_code": 200,
                    "final_url": "https://example.com/discovered.atom",
                    "text_preview": _ATOM,
                },
            },
        ],
        exact_provider=True,
    )

    facts = handler.pre_turn(
        routine=_routine(discovery=True), routine_id="job-1", ctx=ctx
    )

    assert [name for name, _ in ctx.calls] == [
        "fetch.get",
        "search.dispatch",
        "fetch.get",
    ]
    assert facts.coverage == "ok"
    assert len(facts.observations) == 1
    dumped = facts.model_dump_json()
    assert "Provider-only title" not in dumped
    assert "discovered.atom" not in dumped


def test_discovery_refuses_runtime_provider_with_fallback_enabled() -> None:
    ctx = _Context([{"ok": False, "error": {"code": "UPSTREAM_ERROR", "details": {}}}])
    facts = SocialSignalHandler().pre_turn(
        routine=_routine(discovery=True), routine_id="job-1", ctx=ctx
    )
    assert [name for name, _ in ctx.calls] == ["fetch.get"]
    assert any("fallback disabled" in item for item in facts.limitations)


def test_social_watch_allows_continuous_read_only_mode_with_empty_model_tools() -> None:
    parsed = TaskWatchArgs.model_validate(
        {
            "description": "Watch releases",
            "check_instruction": "Alert for important release activity.",
            "interval_minutes": 15,
            "alert_condition": "An important release is published.",
            "stop_on_condition": False,
            "routine": _routine().model_dump(mode="json"),
        }
    )
    assert parsed.stop_on_condition is False


@pytest.mark.parametrize(
    ("field", "value"),
    [("write_authorized", True), ("on_condition_action", "restart it")],
)
def test_social_watch_rejects_write_capability(field: str, value: Any) -> None:
    payload = {
        "description": "Watch releases",
        "check_instruction": "Check releases.",
        "interval_minutes": 15,
        "alert_condition": "New release.",
        "stop_on_condition": False,
        "routine": _routine().model_dump(mode="json"),
        field: value,
    }
    with pytest.raises(ValidationError):
        TaskWatchArgs.model_validate(payload)
