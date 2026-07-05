from __future__ import annotations

import json
from http import HTTPStatus
from pathlib import Path

import pytest

from openminion.api.routes.contracts import APIRouteContext
from openminion.api.routes.skill import handle_request
from openminion.modules.skill.runtime.skill import Skill


DEMO_SKILL_MD = """
---
name: Sync Git Branch
id: git_sync_branch
status: verified
tags: [git, dev]
tools: [tool.shell]
risk: low
applies_to:
  intents: [sync branch, pull latest]
---

## Summary
Pull latest changes for a git branch.

## Procedure
- tool.shell run "git fetch --all"
- tool.shell run "git pull --ff-only"
""".strip()


def _config_path(tmp_path: Path) -> str:
    db = tmp_path / "skill.db"
    cfg = tmp_path / "skill.json"
    cfg.write_text(
        json.dumps(
            {
                "skill": {
                    "sqlite_path": str(db),
                    "blob_root": str(tmp_path / "blob"),
                    "fallback_root": str(tmp_path / "fallback"),
                    "wal": False,
                }
            }
        ),
        encoding="utf-8",
    )
    return str(cfg)


def _ingest_demo(config_path: str) -> str:
    ctl = Skill(config_path)
    try:
        skill_id, _ver, _warnings = ctl.ingest_text(
            name="Sync Git Branch",
            markdown=DEMO_SKILL_MD,
        )
        return skill_id
    finally:
        ctl.close()


@pytest.fixture
def cfg_path(tmp_path: Path) -> str:
    return _config_path(tmp_path)


def _ctx(config_path: str) -> APIRouteContext:
    return APIRouteContext(
        config_path=config_path,
        runtime=None,
        runtime_bootstrap_error=None,
        request_headers=None,
        request_id="test-request",
    )


def test_get_skills_returns_seeded_skill(cfg_path: str) -> None:
    skill_id = _ingest_demo(cfg_path)
    result = handle_request(
        _ctx(cfg_path),
        method_name="GET",
        path="/v1/skills",
        body=None,
        query=None,
    )
    assert result is not None
    assert result.status == HTTPStatus.OK
    assert result.payload["ok"] is True
    skills = result.payload["skills"]
    assert any(item["skill_id"] == skill_id for item in skills)


def test_get_skill_detail_returns_full_package(cfg_path: str) -> None:
    skill_id = _ingest_demo(cfg_path)
    result = handle_request(
        _ctx(cfg_path),
        method_name="GET",
        path=f"/v1/skills/{skill_id}",
        body=None,
        query=None,
    )
    assert result is not None
    assert result.status == HTTPStatus.OK
    assert result.payload["ok"] is True
    assert result.payload["skill"]["skill_id"] == skill_id
    assert result.payload["skill"]["name"] == "Sync Git Branch"


def test_get_skill_detail_returns_404_for_unknown(cfg_path: str) -> None:
    result = handle_request(
        _ctx(cfg_path),
        method_name="GET",
        path="/v1/skills/no-such-skill",
        body=None,
        query=None,
    )
    assert result is not None
    assert result.status == HTTPStatus.NOT_FOUND
    assert result.payload["ok"] is False
    assert result.payload["error"]["code"] == "NOT_FOUND"


def test_post_disable_requires_reason(cfg_path: str) -> None:
    skill_id = _ingest_demo(cfg_path)
    result = handle_request(
        _ctx(cfg_path),
        method_name="POST",
        path=f"/v1/skills/{skill_id}/disable",
        body={"not_reason": "x"},
        query=None,
    )
    assert result is not None
    assert result.status == HTTPStatus.BAD_REQUEST
    assert result.payload["error"]["code"] == "invalid_request"


def test_post_disable_requires_body(cfg_path: str) -> None:
    skill_id = _ingest_demo(cfg_path)
    result = handle_request(
        _ctx(cfg_path),
        method_name="POST",
        path=f"/v1/skills/{skill_id}/disable",
        body=None,
        query=None,
    )
    assert result is not None
    assert result.status == HTTPStatus.BAD_REQUEST


def test_post_disable_sets_status_deprecated(cfg_path: str) -> None:
    skill_id = _ingest_demo(cfg_path)
    result = handle_request(
        _ctx(cfg_path),
        method_name="POST",
        path=f"/v1/skills/{skill_id}/disable",
        body={"reason": "operator http test"},
        query=None,
    )
    assert result is not None
    assert result.status == HTTPStatus.OK
    assert result.payload["ok"] is True
    assert result.payload["disabled"]["new_status"] == "deprecated"
    assert result.payload["disabled"]["reason"] == "operator http test"

    # And it surfaces via the deprecated status filter.
    listed = handle_request(
        _ctx(cfg_path),
        method_name="GET",
        path="/v1/skills",
        body=None,
        query="status=deprecated",
    )
    assert listed is not None
    assert listed.status == HTTPStatus.OK
    listed_ids = {item["skill_id"] for item in listed.payload["skills"]}
    assert skill_id in listed_ids


def test_router_fallthrough_returns_none_for_unrelated_path(
    cfg_path: str,
) -> None:
    assert (
        handle_request(
            _ctx(cfg_path),
            method_name="GET",
            path="/sessions/x/messages",
            body=None,
            query=None,
        )
        is None
    )


def test_router_fallthrough_returns_none_for_wrong_method(
    cfg_path: str,
) -> None:
    skill_id = _ingest_demo(cfg_path)
    # PUT is not a supported method for the skill detail route.
    assert (
        handle_request(
            _ctx(cfg_path),
            method_name="PUT",
            path=f"/v1/skills/{skill_id}",
            body=None,
            query=None,
        )
        is None
    )
