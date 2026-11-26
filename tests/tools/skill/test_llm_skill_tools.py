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
)
from openminion.tools.skill.registrar import REGISTRAR
from openminion.tools.skill.schemas import SkillGetArgs


@dataclass
class _Pkg:
    skill_id: str
    version_hash: str
    scope: str = "global"
    agent_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "skill_id": self.skill_id,
            "version_hash": self.version_hash,
            "scope": self.scope,
            "agent_id": self.agent_id,
        }


class _SkillError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def test_skill_list_success_applies_limit() -> None:
    api = SimpleNamespace(
        list_skills=lambda filters=None, repository_root=None: [
            {"skill_id": "one", "scope": "global", "agent_id": None},
            {"skill_id": "two", "scope": "agent", "agent_id": "agent-a"},
            {"skill_id": "three", "scope": "agent", "agent_id": None},
        ]
    )
    ctx = SimpleNamespace(skill_api=api, agent_id="agent-a")
    result = _h_skill_list({"limit": 2, "scope": "agent"}, ctx)
    assert result["ok"] is True
    assert result["total"] == 2
    assert [item["skill_id"] for item in result["skills"]] == ["one", "two"]


def test_skill_list_unavailable_error() -> None:
    result = _h_skill_list({"limit": 2}, SimpleNamespace(skill_api=None))
    assert result["ok"] is False
    assert result["error"]["code"] == "SKILL_UNAVAILABLE"


def test_skill_get_success_from_to_dict_object() -> None:
    api = SimpleNamespace(
        get_runtime_skill=lambda skill_id, version_hash=None, **kwargs: _Pkg(
            skill_id, "v1"
        ),
        get_skill_version_state=lambda **kwargs: {
            "admission_state": "admitted",
            "active_version_hash": "v1",
        },
        render_snippet=lambda **kwargs: (
            "# Procedure\n1. Inspect the target.",
            "snippet-v1",
        ),
    )
    ctx = SimpleNamespace(skill_api=api, agent_id="agent-a")
    result = _h_skill_get({"skill_id": "deploy"}, ctx)
    assert result["ok"] is True
    assert result["snippet"] == "# Procedure\n1. Inspect the target."
    assert result["snippet_hash"] == "snippet-v1"
    assert result["skill"]["skill_id"] == "deploy"
    assert result["skill"]["version_hash"] == "v1"
    assert list(result)[:3] == ["ok", "snippet", "snippet_hash"]


def test_skill_get_not_found_error() -> None:
    def _raise(*args, **kwargs):
        raise _SkillError("NOT_FOUND", "Skill not found")

    api = SimpleNamespace(get_runtime_skill=_raise)
    ctx = SimpleNamespace(skill_api=api, agent_id="agent-a")
    result = _h_skill_get({"skill_id": "missing"}, ctx)
    assert result["ok"] is False
    assert result["error"]["code"] == "NOT_FOUND"


def test_skill_get_contract_exposes_progressive_resource_retrieval() -> None:
    manifest = REGISTRAR.get_manifest(SimpleNamespace())
    model_tool = next(
        item for item in manifest.model_tools if item.model_tool_id == "skill.get"
    )
    schema = SkillGetArgs.model_json_schema()["properties"]

    assert "required skill_id" in model_tool.description
    assert "not name" in model_tool.description
    assert schema["skill_id"]["description"].startswith("Required")
    assert "do not send name" in schema["skill_id"]["description"]
    assert "resource_path" in model_tool.description
    assert "version_hash" in model_tool.description
    assert "references/guide.md" in schema["resource_path"]["description"]
    assert "reuse it" in schema["version_hash"]["description"]


def test_skill_ingest_contract_limits_default_save_to_one() -> None:
    manifest = REGISTRAR.get_manifest(SimpleNamespace())
    model_tool = next(
        item for item in manifest.model_tools if item.model_tool_id == "skill.ingest"
    )

    assert "at most one" in model_tool.description
    assert "substantial reusable work" in model_tool.description
    assert "do not send skill_markdown" in model_tool.description


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
        skill_api=SimpleNamespace(
            get_runtime_skill=lambda skill_id, version_hash=None, **kwargs: _Pkg(
                skill_id, "v1"
            ),
            get_skill_version_state=lambda **kwargs: {
                "admission_state": "admitted",
                "active_version_hash": "v1",
            },
            read_skill_resource=_read_skill_resource,
        ),
        agent_id="agent-a",
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
        get_skill_owner=lambda **kwargs: {"scope": "agent", "agent_id": "agent-a"},
        delete_skill=lambda skill_id, version_hash=None: {
            "skills": 1,
            "versions": 2,
            "index": 0,
            "runs": 3,
        },
    )
    ctx = SimpleNamespace(skill_api=api, agent_id="agent-a")
    result = _h_skill_remove({"skill_id": "deploy"}, ctx)
    assert result["ok"] is True
    assert result["skill_id"] == "deploy"
    assert result["deleted"] == 6


def test_skill_remove_error_path() -> None:
    def _raise(*args, **kwargs):
        raise _SkillError("SKILL_REMOVE_FAILED", "remove failed")

    api = SimpleNamespace(
        get_skill_owner=lambda **kwargs: {"scope": "agent", "agent_id": "agent-a"},
        delete_skill=_raise,
    )
    ctx = SimpleNamespace(skill_api=api, agent_id="agent-a")
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


def test_skill_ingest_pending_version_does_not_render_snippet(caplog) -> None:

    def _ingest_text(**kwargs):
        return ("skill-abc", "v1", [])

    def _render_snippet_boom(**kwargs):
        raise RuntimeError("renderer unavailable")

    api = SimpleNamespace(
        ingest_text=_ingest_text,
        get_skill_version_state=lambda **kwargs: {
            "admission_state": "pending",
            "active_version_hash": None,
        },
        render_snippet=_render_snippet_boom,
    )
    ctx = SimpleNamespace(skill_api=api, agent_id="agent-a")
    minimal_markdown = "# Skill\nDoes a thing safely.\n"

    with caplog.at_level("WARNING", logger=_skill_plugin.__name__):
        result = _h_skill_ingest(
            {"name": "demo", "markdown": minimal_markdown, "scope": "agent"},
            ctx,
        )

    assert result["ok"] is True
    assert result["skill_id"] == "skill-abc"
    assert result["snippet"] == ""
    assert not caplog.records


def test_skill_get_hides_pending_and_foreign_versions() -> None:
    from openminion.modules.skill.runtime.skill.catalog import SkillCatalogMixin
    from types import MethodType

    pending_api = SimpleNamespace(
        get_skill_owner=lambda **kwargs: {"scope": "agent", "agent_id": "agent-a"},
        get_skill=lambda skill_id, version_hash=None: _Pkg(
            skill_id, "pending", scope="agent", agent_id="agent-a"
        ),
        get_skill_version_state=lambda **kwargs: {
            "admission_state": "pending",
            "active_version_hash": "active",
        },
    )
    pending_api.get_runtime_skill = MethodType(
        SkillCatalogMixin.get_runtime_skill, pending_api
    )
    pending = _h_skill_get(
        {"skill_id": "deploy", "version_hash": "pending"},
        SimpleNamespace(skill_api=pending_api, agent_id="agent-a"),
    )
    assert pending["error"]["code"] == "NOT_FOUND"

    foreign_api = SimpleNamespace(
        get_skill_owner=lambda **kwargs: {"scope": "agent", "agent_id": "agent-b"},
        get_skill=lambda skill_id, version_hash=None: _Pkg(
            skill_id, "v1", scope="agent", agent_id="agent-b"
        ),
        get_skill_version_state=lambda **kwargs: {
            "admission_state": "admitted",
            "active_version_hash": "v1",
        },
    )
    foreign_api.get_runtime_skill = MethodType(
        SkillCatalogMixin.get_runtime_skill, foreign_api
    )
    foreign = _h_skill_get(
        {"skill_id": "deploy"},
        SimpleNamespace(skill_api=foreign_api, agent_id="agent-a"),
    )
    assert foreign["error"]["code"] == "NOT_FOUND"


def test_skill_remove_rejects_global_skill() -> None:
    api = SimpleNamespace(
        get_skill_owner=lambda **kwargs: {"scope": "global", "agent_id": None},
    )
    result = _h_skill_remove(
        {"skill_id": "shared"},
        SimpleNamespace(skill_api=api, agent_id="agent-a"),
    )
    assert result["error"]["code"] == "NOT_FOUND"


def test_skill_remove_all_authorizes_owned_pending_row_without_active_version() -> None:
    api = SimpleNamespace(
        get_skill_owner=lambda **kwargs: {"scope": "agent", "agent_id": "agent-a"},
        delete_skill=lambda **kwargs: {"skills": 1, "versions": 1},
    )

    result = _h_skill_remove(
        {"skill_id": "pending-skill"},
        SimpleNamespace(skill_api=api, agent_id="agent-a"),
    )

    assert result == {"ok": True, "skill_id": "pending-skill", "deleted": 2}


def test_model_url_ingest_requires_and_passes_agent_identity(monkeypatch) -> None:
    missing = _h_skill_ingest_url(
        {"url": "https://example.com/SKILL.md"},
        SimpleNamespace(skill_api=object()),
    )
    assert missing["error"]["code"] == "INVALID_RUNTIME_CONTEXT"

    calls: list[dict[str, object]] = []

    def _ingest(_skill, **kwargs):
        calls.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(_skill_plugin, "ingest_skill_url", _ingest)
    result = _h_skill_ingest_url(
        {"url": "https://example.com/SKILL.md"},
        SimpleNamespace(skill_api=object(), agent_id="agent-a"),
    )
    assert result["ok"] is True
    assert calls[0]["scope"] == "agent"
    assert calls[0]["agent_id"] == "agent-a"


def test_model_ingest_preserves_typed_owner_conflict() -> None:
    def _raise(**kwargs):
        raise _SkillError("SKILL_SCOPE_OWNER_CONFLICT", "owned elsewhere")

    result = _h_skill_ingest(
        {"name": "demo", "markdown": "# Demo\n\n## Procedure\nDo it."},
        SimpleNamespace(skill_api=SimpleNamespace(ingest_text=_raise), agent_id="a"),
    )
    assert result["error"]["code"] == "SKILL_SCOPE_OWNER_CONFLICT"
