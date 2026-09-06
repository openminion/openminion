from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from openminion.tools.skill import plugin as _skill_plugin
from openminion.tools.skill.plugin import (
    _h_skill_get,
    _h_skill_ingest,
    _h_skill_ingest_url,
    _h_skill_list,
    _h_skill_remove,
    _h_skill_propose,
)
from openminion.modules.skill.storage import SQLiteSkillStore
from openminion.tools.skill.registrar import REGISTRAR
from openminion.tools.skill.schemas import SkillGetArgs, SkillProposeArgs


@dataclass
class _Pkg:
    skill_id: str
    version_hash: str

    def to_dict(self) -> dict[str, str]:
        return {"skill_id": self.skill_id, "version_hash": self.version_hash}


class _SkillError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def test_skill_list_success_applies_limit() -> None:
    api = SimpleNamespace(
        list_skills=lambda filters=None: [
            {"skill_id": "one"},
            {"skill_id": "two"},
            {"skill_id": "three"},
        ]
    )
    ctx = SimpleNamespace(skill_api=api)
    result = _h_skill_list({"limit": 2, "scope": "agent"}, ctx)
    assert result["ok"] is True
    assert result["total"] == 3
    assert [item["skill_id"] for item in result["skills"]] == ["one", "two"]


def test_skill_list_unavailable_error() -> None:
    result = _h_skill_list({"limit": 2}, SimpleNamespace(skill_api=None))
    assert result["ok"] is False
    assert result["error"]["code"] == "SKILL_UNAVAILABLE"


def test_skill_get_success_from_to_dict_object() -> None:
    api = SimpleNamespace(
        get_skill=lambda skill_id, version_hash=None: _Pkg(skill_id, "v1")
    )
    ctx = SimpleNamespace(skill_api=api)
    result = _h_skill_get({"skill_id": "deploy"}, ctx)
    assert result["ok"] is True
    assert result["skill"]["skill_id"] == "deploy"
    assert result["skill"]["version_hash"] == "v1"


def test_skill_get_not_found_error() -> None:
    def _raise(*args, **kwargs):
        raise _SkillError("NOT_FOUND", "Skill not found")

    api = SimpleNamespace(get_skill=_raise)
    ctx = SimpleNamespace(skill_api=api)
    result = _h_skill_get({"skill_id": "missing"}, ctx)
    assert result["ok"] is False
    assert result["error"]["code"] == "NOT_FOUND"


def test_skill_get_contract_exposes_progressive_resource_retrieval() -> None:
    manifest = REGISTRAR.get_manifest(SimpleNamespace())
    model_tool = next(
        item for item in manifest.model_tools if item.model_tool_id == "skill.get"
    )
    schema = SkillGetArgs.model_json_schema()["properties"]

    assert "resource_path" in model_tool.description
    assert "version_hash" in model_tool.description
    assert "references/guide.md" in schema["resource_path"]["description"]
    assert "reuse it" in schema["version_hash"]["description"]


def test_skill_get_reads_requested_resource_with_pinned_version() -> None:
    calls: list[dict[str, object]] = []

    def _read_skill_resource(**kwargs):
        calls.append(kwargs)
        return {
            "skill_id": kwargs["skill_id"],
            "version_hash": kwargs["version_hash"],
            "resource_path": kwargs["resource_path"],
            "content": "struct ContentView {}",
            "truncated": False,
        }

    ctx = SimpleNamespace(
        skill_api=SimpleNamespace(read_skill_resource=_read_skill_resource)
    )
    result = _h_skill_get(
        {
            "skill_id": "swiftui-performance-audit",
            "version_hash": "v1",
            "resource_path": "references/ContentView.swift",
            "max_chars": 2000,
        },
        ctx,
    )

    assert result["ok"] is True
    assert result["content"] == "struct ContentView {}"
    assert calls == [
        {
            "skill_id": "swiftui-performance-audit",
            "version_hash": "v1",
            "resource_path": "references/ContentView.swift",
            "max_chars": 2000,
        }
    ]


def test_skill_remove_success_deleted_count() -> None:
    api = SimpleNamespace(
        delete_skill=lambda skill_id, version_hash=None: {
            "skills": 1,
            "versions": 2,
            "index": 0,
            "runs": 3,
        }
    )
    ctx = SimpleNamespace(skill_api=api)
    result = _h_skill_remove({"skill_id": "deploy"}, ctx)
    assert result["ok"] is True
    assert result["skill_id"] == "deploy"
    assert result["deleted"] == 6


def test_skill_remove_error_path() -> None:
    def _raise(*args, **kwargs):
        raise _SkillError("SKILL_REMOVE_FAILED", "remove failed")

    api = SimpleNamespace(delete_skill=_raise)
    ctx = SimpleNamespace(skill_api=api)
    result = _h_skill_remove({"skill_id": "deploy"}, ctx)
    assert result["ok"] is False
    assert result["error"]["code"] == "SKILL_REMOVE_FAILED"


def test_skill_ingest_url_unavailable_error() -> None:
    result = _h_skill_ingest_url(
        {"url": "https://example.com/SKILL.md"},
        SimpleNamespace(skill_api=None),
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "SKILL_UNAVAILABLE"


def test_skill_ingest_render_snippet_failure_logs_structured_warning(caplog) -> None:

    def _ingest_text(**kwargs):
        return ("skill-abc", "v1", [])

    def _render_snippet_boom(**kwargs):
        raise RuntimeError("renderer unavailable")

    api = SimpleNamespace(
        ingest_text=_ingest_text,
        render_snippet=_render_snippet_boom,
    )
    ctx = SimpleNamespace(skill_api=api)
    minimal_markdown = "# Skill\nDoes a thing safely.\n"

    with caplog.at_level("WARNING", logger=_skill_plugin.__name__):
        result = _h_skill_ingest(
            {"name": "demo", "markdown": minimal_markdown, "scope": "agent"},
            ctx,
        )

    # Ingest still returns success — the snippet failure is recoverable.
    assert result["ok"] is True
    assert result["skill_id"] == "skill-abc"
    assert result["snippet"] == ""
    # Exactly one structured warning emitted.
    warnings = [
        r
        for r in caplog.records
        if r.levelname == "WARNING" and "skill snippet render failed" in r.getMessage()
    ]
    assert warnings, "expected structured warning when render_snippet raises"
    assert any("RuntimeError" in r.getMessage() for r in warnings)
    assert any("skill-abc" in r.getMessage() for r in warnings)


def _proposal_markdown() -> str:
    return """---
name: local-system-summary
description: Gather a small local system summary.
---
# Local System Summary

## Procedure

Gather facts and write report.md.
"""


def test_skill_propose_stages_markdown_with_runtime_session_refs(tmp_path) -> None:
    store = SQLiteSkillStore(tmp_path / "skill.db", wal=False)
    try:
        ctx = SimpleNamespace(
            skill_api=SimpleNamespace(store=store),
            session_id="session-1",
            run_id="run-1",
            trace_id="trace-1",
        )
        result = _h_skill_propose({"skill_markdown": _proposal_markdown()}, ctx)

        assert result["ok"] is True
        rows = store.list_proposals(queue_state="pending", limit=10)
        assert len(rows) == 1
        proposal = rows[0]["proposal"]
        assert proposal["source_task_shape_ref"] == "session:session-1"
        assert proposal["evidence_refs"] == ["run_id:run-1", "trace_id:trace-1"]
        assert proposal["skill_markdown"] == _proposal_markdown()
        assert store.list_skills() == []
    finally:
        store.close()


def test_skill_propose_schema_explains_portable_markdown_shape() -> None:
    description = SkillProposeArgs.model_json_schema()["properties"][
        "skill_markdown"
    ]["description"]

    assert "YAML frontmatter" in description
    assert "## Procedure" in description
    assert "frontmatter content field" in description


def test_skill_propose_is_idempotent(tmp_path) -> None:
    store = SQLiteSkillStore(tmp_path / "skill.db", wal=False)
    try:
        ctx = SimpleNamespace(
            skill_api=SimpleNamespace(store=store),
            session_id="session-1",
            metadata={},
        )
        first = _h_skill_propose({"skill_markdown": _proposal_markdown()}, ctx)
        ctx.run_id = "run-2"
        ctx.trace_id = "trace-2"
        second = _h_skill_propose({"skill_markdown": _proposal_markdown()}, ctx)

        assert first["proposal_id"] == second["proposal_id"]
        assert first["created_now"] is True
        assert second["created_now"] is False
        assert len(store.list_proposals(queue_state="pending", limit=10)) == 1
    finally:
        store.close()


def test_skill_propose_rejects_authority_fields(tmp_path) -> None:
    store = SQLiteSkillStore(tmp_path / "skill.db", wal=False)
    try:
        ctx = SimpleNamespace(
            skill_api=SimpleNamespace(store=store),
            session_id="session-1",
            metadata={},
        )
        result = _h_skill_propose(
            {
                "skill_markdown": _proposal_markdown(),
                "reviewer_id": "operator",
            },
            ctx,
        )

        assert result["ok"] is False
        assert result["error"]["code"] == "INVALID_ARGS"
        assert store.list_proposals(queue_state="pending", limit=10) == []
    finally:
        store.close()


def test_skill_propose_requires_session(tmp_path) -> None:
    store = SQLiteSkillStore(tmp_path / "skill.db", wal=False)
    try:
        result = _h_skill_propose(
            {"skill_markdown": _proposal_markdown()},
            SimpleNamespace(
                skill_api=SimpleNamespace(store=store),
                session_id="",
                metadata={},
            ),
        )
        assert result["ok"] is False
        assert result["error"]["code"] == "SKILL_PROPOSAL_CONTEXT_REQUIRED"
    finally:
        store.close()
