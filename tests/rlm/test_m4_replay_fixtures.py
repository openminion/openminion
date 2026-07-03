from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from openminion.modules.brain.loop.recursive.schemas import MetaDirective, RLMBudgets
from openminion.modules.brain.loop.recursive.service import RLMService


_FIXTURES_DIR = Path(__file__).parent / "fixtures"


class _DetSess:
    def __init__(self) -> None:
        self._latest: dict[str, Any] | None = None
        self._version = 0
        self.events: list[dict[str, Any]] = []

    def get_latest_working_state(self, _sid: str) -> dict[str, Any] | None:
        return self._latest

    def put_working_state(self, sid: str, *, state_ref=None, state_inline=None) -> int:
        self._version += 1
        self._latest = {
            "session_id": sid,
            "version": self._version,
            "state_inline": state_inline or {},
        }
        return self._version

    def append_event(
        self,
        session_id: str | None = None,
        type: str | None = None,
        payload: dict | None = None,
        **kwargs,
    ) -> str:
        eid = f"ev-{len(self.events) + 1}"
        self.events.append(
            {
                "event_id": eid,
                "session_id": session_id or kwargs.get("session_id", ""),
                "type": kwargs.get("event_type") or type,
                "payload": payload or {},
                "agent_id": kwargs.get("agent_id") or kwargs.get("actor_id"),
                "artifact_refs": kwargs.get("artifact_refs") or [],
                "memory_refs": kwargs.get("memory_refs") or [],
                "status": kwargs.get("status"),
            }
        )
        return eid

    def list_events(self, sid, *, event_type=None, **_kwargs) -> list:
        evts = [e for e in self.events if e["session_id"] == sid]
        if event_type:
            evts = [e for e in evts if e["type"] == event_type]
        return evts

    def get_slice(self, *_, **__) -> dict[str, Any]:
        return {
            "recent_turns": [],
            "open_tasks": [],
            "active_state": {},
            "recent_tool_events": [],
        }


class _DetCtx:
    def build_pack(self, request: Any) -> dict[str, Any]:
        q = (
            request.get("query")
            if isinstance(request, dict)
            else getattr(request, "query", "")
        )
        return {
            "messages": [
                {"role": "system", "content": "system"},
                {"role": "user", "content": str(q)},
            ]
        }


class _DetLLM:
    def __init__(self, payloads: list[dict[str, Any]]) -> None:
        self.payloads = payloads
        self._call_count = 0

    def call_for_agent(
        self, agent_id, purpose, request, agent_policy
    ) -> dict[str, Any]:
        idx = min(self._call_count, len(self.payloads) - 1)
        pl = self.payloads[idx]
        self._call_count += 1
        return {"status": "success", "text": json.dumps(pl), "json_output": pl}


REPLAY_FIXTURES: list[dict[str, Any]] = [
    {
        "id": "replay-001",
        "description": "Single-tick final answer with evidence",
        "input": {
            "session_id": "replay-sess-001",
            "agent_id": "agt-replay",
            "purpose": "act",
            "query": "What is the deploy window?",
            "budgets": {"max_ticks": 3},
            "llm_payloads": [
                {
                    "final": True,
                    "answer": "Deploy window is 02:00–04:00 UTC.",
                    "evidence_refs": ["artifact://sha256/abc123"],
                    "episode_note": "Confirmed from deploy handbook.",
                }
            ],
        },
        "expect": {
            "stop_reason": "model_marked_final",
            "ticks_used": 1,
            "final_text_contains": "Deploy window",
            "event_types": ["rlm.tick.started", "rlm.tick.completed"],
        },
    },
    {
        "id": "replay-002",
        "description": "Two-tick loop: first tick not final, second is final",
        "input": {
            "session_id": "replay-sess-002",
            "agent_id": "agt-replay",
            "purpose": "plan",
            "query": "Summarise the migration risks",
            "budgets": {"max_ticks": 4},
            "llm_payloads": [
                {
                    "final": False,
                    "answer": "Need more context on rollback.",
                    "next_query": "rollback risk details",
                    "wm_update": {"key_decisions": ["Check rollback steps first"]},
                },
                {
                    "final": True,
                    "answer": "Migration risks: schema drift, connection pool saturation.",
                    "evidence_refs": ["mem://fact-risk-1"],
                },
            ],
        },
        "expect": {
            "stop_reason": "model_marked_final",
            "ticks_used": 2,
            "final_text_contains": "Migration risks",
            "event_types": ["rlm.tick.started", "rlm.tick.completed"],
        },
    },
    {
        "id": "replay-003",
        "description": "max_ticks_override=1 stops after one tick even if model not final",
        "input": {
            "session_id": "replay-sess-003",
            "agent_id": "agt-replay",
            "purpose": "act",
            "query": "Keep going forever",
            "budgets": {"max_ticks": 8},
            "meta_directive": {"max_ticks_override": 1},
            "llm_payloads": [
                {"final": False, "answer": "Not done yet.", "next_query": "continue"},
            ],
        },
        "expect": {
            "stop_reason": "max_ticks_reached",
            "ticks_used": 1,
            "event_types": ["rlm.tick.started", "rlm.tick.completed"],
        },
    },
    {
        "id": "replay-004",
        "description": "Bad retrieval streak (max=1) stops loop with continuation",
        "input": {
            "session_id": "replay-sess-004",
            "agent_id": "agt-replay",
            "purpose": "act",
            "query": "obscure topic with no indexed content whatsoever",
            "budgets": {"max_ticks": 5},
            "meta_directive": {"max_bad_retrieval_streak": 1},
            "llm_payloads": [
                {"final": False, "answer": "still looking", "next_query": "more"},
            ],
        },
        "expect": {
            "stop_reason": "retrieval_quality_bad_streak",
            "needs_more_ticks": True,
            "event_types": ["rlm.tick.started", "rlm.tick.completed"],
        },
    },
    {
        "id": "replay-005",
        "description": "Memory write intent is staged during writeback",
        "input": {
            "session_id": "replay-sess-005",
            "agent_id": "agt-replay",
            "purpose": "act",
            "query": "Learn from this deployment run",
            "budgets": {"max_ticks": 2},
            "llm_payloads": [
                {
                    "final": True,
                    "answer": "Lesson: always warm-cache before deploy.",
                    "memory_write_intents": [
                        {
                            "intent_type": "lesson",
                            "title": "Warm cache before deploy",
                            "content": {
                                "body": "Always warm the cache 30min before cutover."
                            },
                            "salience": 0.8,
                            "tags": ["ops", "cache"],
                        }
                    ],
                }
            ],
        },
        "expect": {
            "stop_reason": "model_marked_final",
            "ticks_used": 1,
            "final_text_contains": "Lesson",
            "event_types": ["rlm.tick.started", "rlm.tick.completed"],
        },
    },
]


def _run_fixture(fixture: dict[str, Any]) -> tuple[Any, _DetSess]:
    inp = fixture["input"]
    session = _DetSess()
    llm = _DetLLM(inp["llm_payloads"])
    svc = RLMService(sessctl=session, contextctl=_DetCtx(), llmctl=llm)

    kwargs: dict[str, Any] = {
        "session_id": inp["session_id"],
        "agent_id": inp["agent_id"],
        "purpose": inp["purpose"],
        "query": inp["query"],
    }
    if "budgets" in inp:
        kwargs["budgets"] = RLMBudgets.model_validate(inp["budgets"])
    if "meta_directive" in inp:
        kwargs["meta_directive"] = MetaDirective.model_validate(inp["meta_directive"])

    resp = svc.generate(**kwargs)
    return resp, session


@pytest.mark.parametrize(
    "fixture", REPLAY_FIXTURES, ids=[f["id"] for f in REPLAY_FIXTURES]
)
def test_replay_fixture(fixture: dict[str, Any]) -> None:
    resp, session = _run_fixture(fixture)
    expect = fixture["expect"]

    assert resp.telemetry.stop_reason == expect["stop_reason"], (
        f"[{fixture['id']}] stop_reason mismatch: "
        f"got {resp.telemetry.stop_reason!r}, expected {expect['stop_reason']!r}"
    )

    if "ticks_used" in expect:
        assert resp.telemetry.ticks_used == expect["ticks_used"], (
            f"[{fixture['id']}] ticks_used: got {resp.telemetry.ticks_used}, expected {expect['ticks_used']}"
        )

    if "final_text_contains" in expect:
        assert expect["final_text_contains"] in resp.final_text, (
            f"[{fixture['id']}] final_text missing '{expect['final_text_contains']}': got {resp.final_text!r}"
        )

    if "needs_more_ticks" in expect:
        assert resp.continuation is not None
        assert resp.continuation.needs_more_ticks is expect["needs_more_ticks"]

    emitted_types = {e["type"] for e in session.events}
    for event_type in expect.get("event_types", []):
        assert event_type in emitted_types, (
            f"[{fixture['id']}] expected event '{event_type}' not emitted. Got: {sorted(emitted_types)}"
        )
