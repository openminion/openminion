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
