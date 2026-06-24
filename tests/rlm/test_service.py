from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from openminion.modules.brain.loop.recursive.schemas import (
    MetaDirective,
    RetrievalFilters,
)
from openminion.modules.brain.loop.recursive.service import RLMService


def _iso_hours_ago(hours: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


class FakeSession:
    def __init__(self) -> None:
        self._latest: dict[str, Any] | None = None
        self._version = 0
        self.events: list[dict[str, Any]] = []
        self.slice_payload: dict[str, Any] = {
            "recent_turns": [],
            "open_tasks": [],
            "active_state": {},
            "recent_tool_events": [],
        }

    def get_latest_working_state(self, session_id: str) -> dict[str, Any] | None:
        del session_id
        return self._latest

    def put_working_state(
        self,
        session_id: str,
        *,
        state_ref: str | None = None,
        state_inline: dict[str, Any] | None = None,
    ) -> int:
        del state_ref
        self._version += 1
        self._latest = {
            "session_id": session_id,
            "version": self._version,
            "ts": datetime.now(timezone.utc).isoformat(),
            "state_inline": state_inline or {},
        }
        return self._version

    def append_event(
        self,
        session_id: str,
        type: str | None = None,
        payload: dict[str, Any] | None = None,
        *,
        event_type: str | None = None,
        agent_id: str | None = None,
        trace_id: str | None = None,
        task_id: str | None = None,
        artifact_refs: list[str] | None = None,
        memory_refs: list[str] | None = None,
        status: str | None = None,
        error: dict[str, Any] | None = None,
    ) -> str:
        del trace_id, task_id, error
        event_id = f"ev-{len(self.events) + 1}"
        self.events.append(
            {
                "event_id": event_id,
                "session_id": session_id,
                "type": event_type or type,
                "payload": payload or {},
                "agent_id": agent_id,
                "artifact_refs": artifact_refs or [],
                "memory_refs": memory_refs or [],
                "status": status,
            }
        )
        return event_id

    def list_events(
        self,
        session_id: str,
        *,
        event_type: str | None = None,
        trace_id: str | None = None,
        agent_id: str | None = None,
        status: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        del trace_id, agent_id, status, limit
        events = [item for item in self.events if item["session_id"] == session_id]
        if event_type:
            events = [item for item in events if item["type"] == event_type]
        return events

    def get_slice(
        self,
        session_id: str,
        purpose: str,
        limits: dict[str, Any],
    ) -> dict[str, Any]:
        del session_id, purpose, limits
        return self.slice_payload


class FakeCtx:
    def build_pack(self, request: Any) -> dict[str, Any]:
        query = (
            request.get("query")
            if isinstance(request, dict)
            else getattr(request, "query", "")
        )
        return {
            "pack_version": "pack-v1",
            "messages": [
                {"role": "system", "content": "base system"},
                {"role": "user", "content": str(query)},
            ],
        }


class FakeLLM:
    def __init__(self, payloads: list[dict[str, Any]]) -> None:
        self.payloads = payloads
        self.calls: list[dict[str, Any]] = []

    def call_for_agent(
        self,
        agent_id: str,
        purpose: str,
        request: dict[str, Any],
        agent_policy: dict[str, Any],
    ) -> dict[str, Any]:
        del agent_id, purpose, agent_policy
        self.calls.append(request)
        idx = len(self.calls) - 1
        payload = self.payloads[min(idx, len(self.payloads) - 1)]
        return {
            "status": "success",
            "text": json.dumps(payload),
            "json_output": payload,
        }


class FakeArtifact:
    def __init__(
        self,
        artifacts: list[dict[str, Any]] | None = None,
        text_by_ref: dict[str, str] | None = None,
    ) -> None:
        self._artifacts = artifacts or []
        self._text_by_ref = text_by_ref or {}
        self.ingested: list[dict[str, Any]] = []

    def list_recent(
        self, limit: int = 50, scope_filters: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        del scope_filters
        return self._artifacts[:limit]

    def read_bytes(self, ref_or_sha: str) -> bytes:
        return self._text_by_ref.get(ref_or_sha, "").encode("utf-8")

    def ingest_bytes(
        self,
        data: bytes,
        mime: str | None = None,
        original_name: str | None = None,
        label: str | None = None,
        meta: dict[str, Any] | None = None,
        session_id: str | None = None,
        trace_id: str | None = None,
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        del trace_id
        sha = hashlib.sha256(data).hexdigest()
        ref = f"artifact://sha256/{sha}"
        self.ingested.append(
            {
                "ref": ref,
                "sha256": sha,
                "mime": mime,
                "original_name": original_name,
                "label": label,
                "meta": meta or {},
                "session_id": session_id,
                "agent_id": agent_id,
            }
        )
        return {"ref": ref, "sha256": sha}


class FakeMemory:
    def __init__(self, semantic_rows: list[dict[str, Any]] | None = None) -> None:
        self.semantic_rows = semantic_rows or []
        self.staged: list[dict[str, Any]] = []

    def retrieve(
        self,
        *,
        session_id: str,
        agent_id: str,
        query: str,
        k: int,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        del session_id, agent_id, query, filters
        return self.semantic_rows[:k]

    def query_facts(
        self,
        *,
        session_id: str,
        agent_id: str,
        query: str,
        limit: int,
        mode_name: str | None = None,
    ) -> list[dict[str, Any]]:
        del session_id, agent_id, query, mode_name
        return self.semantic_rows[:limit]

    def stage_candidate(
        self,
        *,
        scope: str,
        record_type: str,
        title: str,
        content: dict[str, Any] | str,
        tags: list[str] | None = None,
        evidence_refs: list[str] | None = None,
    ) -> str:
        record_id = f"cand-{len(self.staged) + 1}"
        self.staged.append(
            {
                "id": record_id,
                "scope": scope,
                "record_type": record_type,
                "title": title,
                "content": content,
                "tags": tags or [],
                "evidence_refs": evidence_refs or [],
            }
        )
        return record_id


class FakeSkill:
    def match(
        self,
        intent_text: str,
        step_hint: dict[str, Any] | None,
        agent_id: str,
        k: int = 3,
        status_filter: list[str] | str | None = None,
    ) -> list[dict[str, Any]]:
        del intent_text, step_hint, agent_id, k, status_filter
        return [
            {
                "skill_id": "s-1",
                "version_hash": "v1",
                "name": "Deploy",
                "score": 0.4,
                "tags": ["ops"],
            }
        ]

    def render_snippet(
        self,
        skill_id: str,
        version_hash: str | None,
        purpose: str,
        max_tokens: int,
        mode_name: str | None = None,
    ) -> tuple[str, str]:
        del version_hash, purpose, max_tokens, mode_name
        return f"Skill snippet for {skill_id}", "hash-1"


class FakeRetrieval:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []
        self.expand_rows: list[dict[str, Any]] = []

    def retrieve(
        self,
        *,
        query: str,
        purpose: str,
        scope: dict[str, Any],
        k: int,
        strategy: str,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        del query, purpose, scope, strategy, filters
        return self.rows[:k]

    def expand(
        self,
        *,
        ref: str,
        mode: str,
        k: int,
    ) -> list[dict[str, Any]]:
        del ref, mode
        return self.expand_rows[:k]


def test_generate_runs_recursive_ticks_and_writes_back() -> None:
    session = FakeSession()
    artifact = FakeArtifact()
    memory = FakeMemory(
        semantic_rows=[
            {
                "record_id": "fact-1",
                "text": "Project codename is Aurora",
                "score": 0.9,
                "tags": ["fact"],
            }
        ]
    )
    llm = FakeLLM(
        payloads=[
            {
                "final": False,
                "answer": "Need one more retrieval pass.",
                "next_query": "aurora constraints",
                "episode_note": "Tick 1 done",
                "wm_update": {"open_questions": ["What is the deploy window?"]},
            },
            {
                "final": True,
                "answer": "Final answer with evidence.",
                "episode_note": "Tick 2 done",
                "evidence_refs": ["artifact://sha256/evidence123"],
                "memory_write_intents": [
                    {
                        "intent_type": "lesson",
                        "title": "Prefer cached deploy steps",
                        "content": {"note": "Reuse skill snippet first."},
                        "tags": ["ops"],
                    }
                ],
            },
        ]
    )
    service = RLMService(
        sessctl=session,
        contextctl=FakeCtx(),
        llmctl=llm,
        artifactctl=artifact,
        memctl=memory,
        skillctl=FakeSkill(),
    )

    response = service.generate(
        session_id="sess-1",
        agent_id="agent-1",
        purpose="act",
        query="Ship release safely",
    )

    assert response.final_text == "Final answer with evidence."
    assert response.telemetry.ticks_used == 2
    assert response.telemetry.tick_reports[0].retrieval_quality in {"GOOD", "OK", "BAD"}
    assert len(artifact.ingested) == 2
    assert len(memory.staged) == 1
    assert any(
        item.ref_id == "artifact://sha256/evidence123"
        for item in response.evidence_refs
    )
    assert any(event["type"] == "rlm.tick.started" for event in session.events)
    assert any(event["type"] == "rlm.tick.completed" for event in session.events)
    latest = session.get_latest_working_state("sess-1")
    assert latest is not None
    assert isinstance(latest.get("state_inline", {}).get("wm_state"), dict)


def test_refresh_working_memory_uses_recent_turns_and_tools() -> None:
    session = FakeSession()
    session.slice_payload = {
        "recent_turns": [
            {"role": "user", "text": "Plan deployment?"},
            {"role": "assistant", "text": "We should stage first."},
        ],
        "open_tasks": [{"task_id": "t1"}],
        "active_state": {"phase": "PLAN", "cursor": 2},
        "recent_tool_events": [
            {"tool_name": "shell", "excerpt": "kubectl dry-run success"}
        ],
    }

    service = RLMService(
        sessctl=session,
        contextctl=FakeCtx(),
        llmctl=FakeLLM(payloads=[{"final": True, "answer": "ok"}]),
    )
    wm = service.refresh_working_memory("sess-2", "agent-2", "periodic")

    assert wm.current_step == "PLAN"
    assert wm.step_cursor == "2"
    assert "Plan deployment?" in wm.open_questions
    assert any("kubectl dry-run success" in item for item in wm.tool_summaries)
    assert any(event["type"] == "wm.updated" for event in session.events)


def test_retrieve_combines_semantic_and_episodic_with_filters() -> None:
    artifact_rows = [
        {
            "ref": "artifact://sha256/a1",
            "sha256": "a" * 64,
            "created_at": _iso_hours_ago(2),
            "label": "episode aurora deploy",
            "original_name": "episode.md",
            "mime": "text/markdown",
            "meta_json": {"tags": ["episode", "ops"]},
        },
        {
            "ref": "artifact://sha256/a2",
            "sha256": "b" * 64,
            "created_at": _iso_hours_ago(1),
            "label": "other topic",
            "original_name": "misc.md",
            "mime": "text/markdown",
            "meta_json": {"tags": ["misc"]},
        },
    ]
    artifact = FakeArtifact(
        artifacts=artifact_rows,
        text_by_ref={
            "artifact://sha256/a1": "Aurora deploy checklist and rollback steps",
            "artifact://sha256/a2": "Unrelated content",
        },
    )
    memory = FakeMemory(
        semantic_rows=[
            {
                "record_id": "fact-1",
                "text": "Aurora deploy window is 02:00 UTC",
                "score": 0.95,
            }
        ]
    )
    service = RLMService(
        sessctl=FakeSession(),
        contextctl=FakeCtx(),
        llmctl=FakeLLM(payloads=[{"final": True, "answer": "ok"}]),
        artifactctl=artifact,
        memctl=memory,
        skillctl=FakeSkill(),
    )

    result = service.retrieve(
        session_id="sess-3",
        agent_id="agent-3",
        query="Aurora deploy window",
        k=4,
        filters=RetrievalFilters(include_sources=["sm", "em"], tags=["ops"]),
    )

    assert result
    assert result[0].source in {"sm", "em"}
    assert any(item.source == "sm" for item in result)
    assert all("ops" in item.tags or item.source == "sm" for item in result)


def test_generate_bad_retrieval_uses_empty_augmentation_and_continuation() -> None:
    retrieval = FakeRetrieval(
        rows=[
            {
                "source": "em",
                "ref_id": "artifact://sha256/low",
                "text": "weak unrelated retrieval",
                "score": 0.05,
                "unit_kind": "chunk",
            }
        ]
    )
    llm = FakeLLM(
        payloads=[
            {
                "final": False,
                "answer": "I am not fully confident yet.",
                "next_query": "clarify scope",
                "episode_note": "retrieval uncertain",
            }
        ]
    )
    service = RLMService(
        sessctl=FakeSession(),
        contextctl=FakeCtx(),
        llmctl=llm,
        retrievectl=retrieval,
    )

    response = service.generate(
        session_id="sess-4",
        agent_id="agent-4",
        purpose="act",
        query="answer policy question",
        meta_directive=MetaDirective(max_bad_retrieval_streak=1),
    )

    assert response.telemetry.stop_reason == "retrieval_quality_bad_streak"
    assert response.continuation is not None
    assert response.continuation.needs_more_ticks is True
    assert response.telemetry.tick_reports[0].used_empty_augmentation is True
    assert response.telemetry.tick_reports[0].retrieval_quality == "BAD"


def test_expand_uses_retrievectl_when_available() -> None:
    retrieval = FakeRetrieval()
    retrieval.expand_rows = [
        {
            "source": "em",
            "ref_id": "node://n1",
            "text": "Expanded leaf 1",
            "score": 0.9,
            "unit_kind": "chunk",
            "raptor_level": "leaf",
        },
        {
            "source": "em",
            "ref_id": "node://n2",
            "text": "Expanded leaf 2",
            "score": 0.8,
            "unit_kind": "chunk",
            "raptor_level": "leaf",
        },
    ]
    service = RLMService(
        sessctl=FakeSession(),
        contextctl=FakeCtx(),
        llmctl=FakeLLM(payloads=[{"final": True, "answer": "ok"}]),
        retrievectl=retrieval,
    )

    items = service.expand(ref="node://root", mode="leaves", k=2)
    assert len(items) == 2
    assert items[0].ref_id == "node://n1"
    assert items[0].raptor_level == "leaf"
