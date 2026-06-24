from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from openminion.modules.memory.models import MemoryRecord
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.memory import InMemoryMemoryStore
from openminion.services.agent.memory.gateway_adapter import (
    MemoryServiceGatewayAdapter,
)


def _make_real_adapter(**kwargs) -> MemoryServiceGatewayAdapter:
    store = InMemoryMemoryStore()
    service = MemoryService(store=store)
    return MemoryServiceGatewayAdapter(service, agent_id="continuity-agent", **kwargs)


def _summary_structurer(summary_text: str, turn_count: int) -> dict[str, object]:
    del turn_count
    return {
        "outcome": "succeeded",
        "summary_text": summary_text,
        "decisions": ["Rotate the deploy key every 90 days."],
        "open_questions": ["Who owns the next rotation rehearsal?"],
        "corrections": ["The original fixture scope was too broad."],
        "topic_keywords": ["deploy", "rotation"],
        "active_threads": [
            {
                "topic": "Deploy key rotation",
                "status": "open",
                "next_step": "Assign the next rotation rehearsal owner.",
            }
        ],
    }


def test_h1_uses_search_for_long_handoff_queries() -> None:
    service = Mock()
    service.search.return_value = []
    service.list.return_value = []
    session_context = Mock()
    session_context.get_turn_count.return_value = 0
    adapter = MemoryServiceGatewayAdapter(
        service,
        agent_id="continuity-agent",
        session_context=session_context,
    )

    adapter.build_context_with_metadata(
        session_id="session-1",
        user_message="what did we discuss about deployment and rotation?",
    )

    summary_searches = [
        call.args[0]
        for call in service.search.call_args_list
        if getattr(call.args[0], "types", None) == ["session_summary"]
    ]
    assert len(summary_searches) == 1
    assert (
        summary_searches[0].query
        == "what did we discuss about deployment and rotation?"
    )
    assert not any(
        getattr(call.args[0], "types", None) == ["session_summary"]
        for call in service.list.call_args_list
    )


def test_h1_keeps_recency_fallback_for_short_handoff_queries() -> None:
    service = Mock()
    service.search.return_value = []
    service.list.return_value = []
    session_context = Mock()
    session_context.get_turn_count.return_value = 0
    adapter = MemoryServiceGatewayAdapter(
        service,
        agent_id="continuity-agent",
        session_context=session_context,
    )

    adapter.build_context_with_metadata(session_id="session-1", user_message="hi")

    summary_lists = [
        call.args[0]
        for call in service.list.call_args_list
        if getattr(call.args[0], "types", None) == ["session_summary"]
    ]
    assert len(summary_lists) == 1
    assert not any(
        getattr(call.args[0], "types", None) == ["session_summary"]
        for call in service.search.call_args_list
    )


def test_h2_summary_structuring_uses_model_authored_fields_when_available() -> None:
    adapter = _make_real_adapter(
        session_summary_max_chars=500,
        session_summary_structurer=_summary_structurer,
    )

    summary = adapter._structure_rolling_summary(  # noqa: SLF001
        (
            "We agreed to use pytest for coverage. "
            "The plan is to keep SQLite for local tests. "
            "Switching to stricter fixtures for database setup. "
            "Confirmed the Tuesday deploy rehearsal. "
            "Approved the cleanup checklist. "
            "No issues found with staging. "
            "Actually, wrong fixture scope for the database setup."
        ),
        turn_count=7,
    )

    assert summary["decisions"] == ["Rotate the deploy key every 90 days."]
    assert summary["corrections"] == ["The original fixture scope was too broad."]
    assert summary["open_questions"] == ["Who owns the next rotation rehearsal?"]
    assert summary["topic_keywords"] == ["deploy", "rotation"]
    assert summary["active_threads"] == [
        {
            "topic": "Deploy key rotation",
            "status": "open",
            "next_step": "Assign the next rotation rehearsal owner.",
        }
    ]
    assert summary["summary_text"]


def test_h3_formats_first_summary_differently_from_remaining_context() -> None:
    adapter = _make_real_adapter()
    records = [
        SimpleNamespace(
            title="Deployment rotation review",
            content={
                "summary_text": "Deploy key rotation and Tuesday deployment plan reviewed.",
                "decisions": ["Rotate the deploy key every 90 days."],
                "open_questions": ["Who owns the next rotation rehearsal?"],
                "corrections": ["The original fixture scope was too broad."],
                "topic_keywords": ["deploy", "rotation"],
                "active_threads": [
                    {
                        "topic": "Deploy key rotation",
                        "status": "open",
                        "next_step": "Assign the next rotation rehearsal owner.",
                    }
                ],
            },
        ),
        SimpleNamespace(
            title="Routine UI sync",
            content={
                "summary_text": (
                    "Routine interface cleanup and backlog grooming before the lunch "
                    "vendor comparison matrix follow-up came up."
                ),
                "decisions": [],
                "open_questions": [],
                "topic_keywords": ["ui"],
            },
        ),
        SimpleNamespace(
            title="Retro notes",
            content={
                "summary_text": (
                    "Routine retro housekeeping before the emoji poll tally needed one more pass."
                ),
                "decisions": [],
                "open_questions": [],
                "topic_keywords": ["retro"],
            },
        ),
    ]

    rendered = adapter._format_session_summaries(records, max_chars=2000)  # noqa: SLF001

    assert "## Continuing from recent sessions" in rendered
    assert "Most relevant prior session:" in rendered
    assert "Topic: deploy, rotation" in rendered
    assert "Active thread:" in rendered
    assert "Status: open" in rendered
    assert "Next step: Assign the next rotation rehearsal owner." in rendered
    assert "Key decision: Rotate the deploy key every 90 days." in rendered
    assert "Open question: Who owns the next rotation rehearsal?" in rendered
    assert "Prior decisions:" in rendered
    assert "Prior corrections:" in rendered
    assert "Open questions from earlier:" in rendered
    assert "Other recent context:" in rendered
    assert (
        "Routine UI sync — Routine interface cleanup and backlog grooming before the lunch vendor"
        in rendered
    )


def test_h3_prefers_open_active_thread_summary_for_first_turn_preamble() -> None:
    adapter = _make_real_adapter()
    records = [
        SimpleNamespace(
            title="Closed retro recap",
            content={
                "summary_text": "Retro action items were all completed.",
                "decisions": [],
                "open_questions": [],
                "corrections": [],
                "topic_keywords": ["retro"],
                "active_threads": [
                    {
                        "topic": "Retro cleanup",
                        "status": "done",
                        "next_step": "",
                    }
                ],
            },
        ),
        SimpleNamespace(
            title="Release checklist follow-up",
            content={
                "summary_text": "Release checklist still needs QA signoff before publishing.",
                "decisions": ["Publish only after QA signoff."],
                "open_questions": [],
                "corrections": [],
                "topic_keywords": ["release", "qa"],
                "active_threads": [
                    {
                        "topic": "Release checklist",
                        "status": "open",
                        "next_step": "Collect QA signoff before publishing the release notes.",
                    }
                ],
            },
        ),
    ]

    rendered = adapter._format_session_summaries(records, max_chars=2000)  # noqa: SLF001

    assert "Title: Release checklist follow-up" in rendered
    assert "Topic: Release checklist" in rendered
    assert (
        "Next step: Collect QA signoff before publishing the release notes." in rendered
    )
    assert "Closed retro recap — Retro action items were all completed." in rendered


def test_h3_prefers_open_active_thread_over_paused_for_first_turn_preamble() -> None:
    adapter = _make_real_adapter()
    records = [
        SimpleNamespace(
            title="Failed continuation follow-up",
            content={
                "outcome": "no_prior_context",
                "summary_text": (
                    "The assistant could not continue the prior itinerary and asked the user "
                    "to restate the earlier session."
                ),
                "decisions": [],
                "open_questions": [],
                "corrections": [],
                "topic_keywords": ["china", "continuation"],
                "active_threads": [
                    {
                        "topic": "China itinerary continuation",
                        "status": "paused",
                        "next_step": "User may need to restate the earlier itinerary.",
                    }
                ],
            },
        ),
        SimpleNamespace(
            title="High-level China itinerary",
            content={
                "outcome": "succeeded",
                "summary_text": (
                    "A high-level 10-day China itinerary was created and still needs the "
                    "detailed day-by-day expansion."
                ),
                "decisions": ["High-level China itinerary created."],
                "open_questions": ["Which cities should get more time?"],
                "corrections": [],
                "topic_keywords": ["china", "itinerary", "travel"],
                "active_threads": [
                    {
                        "topic": "Detailed day-by-day itinerary planning",
                        "status": "open",
                        "next_step": "Create the detailed day-by-day itinerary.",
                    }
                ],
            },
        ),
    ]

    rendered = adapter._format_session_summaries(records, max_chars=2000)  # noqa: SLF001

    assert "Title: High-level China itinerary" in rendered
    assert "Status: open" in rendered
    assert "Create the detailed day-by-day itinerary." in rendered
    assert (
        "Title: Failed continuation follow-up"
        not in rendered.split("Other recent context:")[0]
    )


def test_h3_prefers_typed_succeeded_summary_over_legacy_unknown() -> None:
    adapter = _make_real_adapter()
    records = [
        SimpleNamespace(
            title="Legacy itinerary continuation",
            content={
                "summary_text": "Legacy summary without typed outcome.",
                "decisions": [],
                "open_questions": [],
                "corrections": [],
                "topic_keywords": ["china", "legacy"],
                "active_threads": [
                    {
                        "topic": "Legacy China continuation",
                        "status": "paused",
                        "next_step": "User may need to restate prior details.",
                    }
                ],
            },
        ),
        SimpleNamespace(
            title="Typed successful itinerary",
            content={
                "outcome": "succeeded",
                "summary_text": "Typed successful itinerary summary.",
                "decisions": ["High-level China itinerary created."],
                "open_questions": [],
                "corrections": [],
                "topic_keywords": ["china", "itinerary"],
                "active_threads": [
                    {
                        "topic": "Detailed day-by-day itinerary planning",
                        "status": "open",
                        "next_step": "Create the detailed day-by-day itinerary.",
                    }
                ],
            },
        ),
    ]

    rendered = adapter._format_session_summaries(records, max_chars=2000)  # noqa: SLF001

    assert "Title: Typed successful itinerary" in rendered
    assert "Detailed day-by-day itinerary planning" in rendered
    assert (
        "Title: Legacy itinerary continuation"
        not in rendered.split("Other recent context:")[0]
    )


def test_h3_preserves_active_thread_next_step_under_tight_budget() -> None:
    adapter = _make_real_adapter()
    records = [
        SimpleNamespace(
            title=(
                "High-level 10-day China itinerary created for May 6-15, 2026 "
                "(Wed to Fri) with a long explanatory title"
            ),
            content={
                "summary_text": (
                    "User requested a 10-day China trip from San Francisco departing "
                    "Wednesday, May 6, 2026. Assistant provided a high-level itinerary "
                    "outline. User explicitly stated they still need detailed day-by-day "
                    "planning, indicating this is the next step."
                ),
                "decisions": [
                    "High-level 10-day China itinerary created for May 6-15, 2026."
                ],
                "open_questions": ["Which cities should get more time?"],
                "corrections": [],
                "topic_keywords": [
                    "China trip",
                    "10-day itinerary",
                    "San Francisco departure",
                    "May 2026 travel",
                    "travel planning",
                ],
                "active_threads": [
                    {
                        "topic": "Detailed day-by-day itinerary planning",
                        "status": "open",
                        "next_step": (
                            "Create detailed day-by-day plan based on the high-level itinerary provided."
                        ),
                    }
                ],
            },
        )
    ]

    rendered = adapter._format_session_summaries(records, max_chars=400)  # noqa: SLF001

    assert "Active thread:" in rendered
    assert "Status: open" in rendered
    assert "Detailed day-by-day itinerary planning" in rendered
    assert "Create detailed day-by-day plan" in rendered


def test_h4_triggers_summary_compression_once_per_first_turn() -> None:
    service = Mock()
    service.search.return_value = []
    service.list.return_value = []
    session_context = Mock()
    session_context.get_turn_count.return_value = 0
    adapter = MemoryServiceGatewayAdapter(
        service,
        agent_id="continuity-agent",
        session_context=session_context,
    )
    adapter.compress_old_summaries = Mock(return_value=(1, 0))  # type: ignore[method-assign]

    adapter.build_context_with_metadata(
        session_id="session-1",
        user_message="what did we discuss about deployment and rotation?",
    )
    adapter.build_context_with_metadata(
        session_id="session-1",
        user_message="what did we discuss about deployment and rotation?",
    )

    adapter.compress_old_summaries.assert_called_once_with(
        max_age_days=14,
        max_summary_chars=100,
    )


def test_h5_places_agent_memory_after_historical_session_summaries() -> None:
    service = Mock()
    service.search.side_effect = [
        [
            SimpleNamespace(
                title="Old email acknowledged",
                content={
                    "summary_text": "User previously shared old@example.com.",
                    "topic_keywords": ["email"],
                    "active_threads": [
                        {
                            "topic": "Awaiting user task request",
                            "status": "open",
                            "next_step": "",
                        }
                    ],
                },
            )
        ],
        [
            MemoryRecord(
                id="record-1",
                scope="agent:continuity-agent",
                type="fact",
                title="User email address",
                content="new@example.com",
                created_at="2026-05-01T00:00:00Z",
                updated_at="2026-05-01T00:00:00Z",
            )
        ],
        [],
    ]
    service.list.return_value = []
    session_context = Mock()
    session_context.get_turn_count.return_value = 0
    adapter = MemoryServiceGatewayAdapter(
        service,
        agent_id="continuity-agent",
        session_context=session_context,
    )

    rendered, _ = adapter.build_context_with_metadata(
        session_id="session-1",
        user_message="What is my email? Answer with only the email address.",
    )

    recent_index = rendered.index("## Continuing from recent sessions")
    agent_index = rendered.index("## Agent Memory")
    assert recent_index < agent_index
    assert "new@example.com" in rendered


def test_h6_retrieves_prior_session_summary_mid_session() -> None:
    service = Mock()
    service.search.side_effect = [
        [
            MemoryRecord(
                id="summary-1",
                scope="agent:continuity-agent",
                type="session_summary",
                title="Artifact store migration",
                content={
                    "outcome": "succeeded",
                    "summary_text": "Use Postgres for the artifact store migration.",
                    "decisions": ["Use Postgres for the artifact store."],
                    "open_questions": ["Who owns the backfill runbook?"],
                    "corrections": [],
                    "topic_keywords": ["artifact", "postgres"],
                    "active_threads": [
                        {
                            "topic": "Artifact store migration",
                            "status": "open",
                            "next_step": "Prepare the backfill runbook.",
                        }
                    ],
                },
                created_at="2026-05-01T00:00:00Z",
                updated_at="2026-05-01T00:00:00Z",
            )
        ],
        [],
    ]
    service.list.return_value = []
    session_context = Mock()
    session_context.get_turn_count.return_value = 5
    adapter = MemoryServiceGatewayAdapter(
        service,
        agent_id="continuity-agent",
        session_context=session_context,
    )

    rendered, meta = adapter.build_context_with_metadata(
        session_id="session-1",
        user_message="What did we decide about the artifact store migration again?",
    )

    assert "## Continuing from recent sessions" in rendered
    assert "Use Postgres for the artifact store." in rendered
    assert meta["prior_context_present"] == "true"


def test_h6_surfaces_current_session_summary_for_short_mid_session_callback() -> None:
    service = Mock()
    service.search.side_effect = [
        [],
        [],
    ]
    service.list.side_effect = [
        [
            MemoryRecord(
                id="summary-current",
                scope="agent:continuity-agent",
                type="session_summary",
                key="session_summary:session-1",
                title="China itinerary working plan",
                content={
                    "outcome": "succeeded",
                    "summary_text": (
                        "A detailed China itinerary is in progress with a mid-range "
                        "budget and Beijing as the first city."
                    ),
                    "decisions": [
                        "Budget range stays in the $2,000-$2,800 mid-range band."
                    ],
                    "open_questions": ["Should Shanghai replace Xi'an?"],
                    "corrections": [],
                    "topic_keywords": ["china", "budget", "beijing"],
                    "active_threads": [
                        {
                            "topic": "Detailed day-by-day itinerary planning",
                            "status": "open",
                            "next_step": "Expand the itinerary day by day.",
                        }
                    ],
                },
                tags=["session_summary", "session-1"],
                created_at="2026-05-01T00:00:00Z",
                updated_at="2026-05-01T00:00:00Z",
            )
        ]
    ]
    session_context = Mock()
    session_context.get_turn_count.return_value = 5
    adapter = MemoryServiceGatewayAdapter(
        service,
        agent_id="continuity-agent",
        session_context=session_context,
    )

    rendered, meta = adapter.build_context_with_metadata(
        session_id="session-1",
        user_message="Budget?",
    )

    assert "## Current session summary" in rendered
    assert "Current session callback context:" in rendered
    assert "## Continuing from recent sessions" not in rendered
    assert "Budget range stays in the $2,000-$2,800 mid-range band." in rendered
    assert meta["prior_context_present"] == "true"


def test_h6_current_session_rendering_prioritizes_factual_fields_under_budget() -> None:
    adapter = _make_real_adapter()
    records = [
        SimpleNamespace(
            title=(
                "China itinerary working plan with a long explanatory title that would "
                "normally consume budget before the useful callback facts render"
            ),
            content={
                "outcome": "succeeded",
                "summary_text": (
                    "The user asked for a China trip with Beijing first and a mid-range "
                    "budget, and the assistant already committed to that plan."
                ),
                "decisions": [
                    "Budget range stays in the $2,000-$2,800 mid-range band."
                ],
                "open_questions": ["Should Shanghai replace Xi'an?"],
                "corrections": [],
                "topic_keywords": [
                    "china",
                    "budget",
                    "beijing",
                    "travel planning",
                ],
                "active_threads": [
                    {
                        "topic": "Detailed day-by-day itinerary planning",
                        "status": "open",
                        "next_step": "Expand the itinerary day by day.",
                    }
                ],
            },
        )
    ]

    rendered = adapter._format_session_summaries(  # noqa: SLF001
        records,
        max_chars=260,
        current_session=True,
    )

    assert "## Current session summary" in rendered
    assert "Topic: china, budget, beijing, travel planning" in rendered
    assert (
        "Key decision: Budget range stays in the $2,000-$2,800 mid-range band."
        in rendered
    )
    assert rendered.index("Topic:") < rendered.index("Key decision:")


def test_h6_current_session_rendering_surfaces_topic_before_generic_summary() -> None:
    adapter = _make_real_adapter()
    records = [
        SimpleNamespace(
            title=(
                "User asked a basic question about Bitcoin and received an explanation "
                "describing it as a decentralized digital currency"
            ),
            content={
                "outcome": "succeeded",
                "summary_text": (
                    "User asked a basic question about Bitcoin and received an explanation "
                    "describing it as a decentralized digital currency created in 2009."
                ),
                "decisions": [],
                "open_questions": [],
                "corrections": [],
                "topic_keywords": [
                    "bitcoin",
                    "cryptocurrency",
                    "decentralized",
                    "digital currency",
                    "Satoshi Nakamoto",
                ],
                "active_threads": [],
            },
        )
    ]

    rendered = adapter._format_session_summaries(  # noqa: SLF001
        records,
        max_chars=220,
        current_session=True,
    )

    assert "## Current session summary" in rendered
    assert "Topic: bitcoin, cryptocurrency, decentralized, digital currency" in rendered
    assert "Key decision:" not in rendered
    if "Summary:" in rendered:
        assert rendered.index("Topic:") < rendered.index("Summary:")


def test_h6_suppresses_recalled_prior_session_summary_when_current_session_summary_exists() -> (
    None
):
    service = Mock()
    service.search.side_effect = [
        [
            MemoryRecord(
                id="summary-current",
                scope="agent:continuity-agent",
                type="session_summary",
                key="session_summary:session-1",
                title="Bitcoin basics",
                content={
                    "outcome": "succeeded",
                    "summary_text": "User asked what Bitcoin is and received a concise definition.",
                    "decisions": [],
                    "open_questions": [],
                    "corrections": [],
                    "topic_keywords": ["bitcoin", "cryptocurrency"],
                    "active_threads": [],
                },
                tags=["session_summary", "session-1"],
                created_at="2026-05-01T00:00:00Z",
                updated_at="2026-05-01T00:00:00Z",
            ),
            MemoryRecord(
                id="summary-old",
                scope="agent:continuity-agent",
                type="session_summary",
                key="session_summary:older-session",
                title="Old incomplete message",
                content={
                    "outcome": "unknown",
                    "summary_text": "Session involved a single incomplete user message.",
                    "decisions": [],
                    "open_questions": [],
                    "corrections": [],
                    "topic_keywords": ["incomplete", "clarification"],
                    "active_threads": [
                        {
                            "topic": "User's original incomplete message",
                            "status": "open",
                            "next_step": "Await clarification.",
                        }
                    ],
                },
                tags=["session_summary", "older-session"],
                created_at="2026-04-30T00:00:00Z",
                updated_at="2026-04-30T00:00:00Z",
            ),
        ],
        [],
    ]
    service.list.return_value = []
    session_context = Mock()
    session_context.get_turn_count.return_value = 2
    adapter = MemoryServiceGatewayAdapter(
        service,
        agent_id="continuity-agent",
        session_context=session_context,
    )

    rendered, meta = adapter.build_context_with_metadata(
        session_id="session-1",
        user_message="what's latest price?",
    )

    assert "## Current session summary" in rendered
    assert "Bitcoin basics" in rendered
    assert "## Continuing from recent sessions" not in rendered
    assert "Old incomplete message" not in rendered
    assert meta["prior_context_present"] == "true"
