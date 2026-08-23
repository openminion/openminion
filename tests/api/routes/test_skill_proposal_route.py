from __future__ import annotations

import json
from http import HTTPStatus
from pathlib import Path

import pytest

from openminion.api.routes.contracts import APIRouteContext
from openminion.api.routes.skill import handle_request
from openminion.modules.skill.proposal import SkillProposal, SkillProposalDraft
from openminion.modules.skill.proposal.queue import create_proposal
from openminion.modules.skill.runtime.skill import Skill


def _config_path(tmp_path: Path) -> str:
    data_root = tmp_path / ".openminion"
    skill_root = data_root / "skill"
    db = skill_root / "skill.db"
    cfg = tmp_path / "skill.json"
    cfg.write_text(
        json.dumps(
            {
                "skill": {
                    "sqlite_path": str(db),
                    "blob_root": str(skill_root / "blob"),
                    "fallback_root": str(skill_root / "fallback"),
                    "wal": False,
                }
            }
        ),
        encoding="utf-8",
    )
    return str(cfg)


def _ctx(config_path: str) -> APIRouteContext:
    return APIRouteContext(
        config_path=config_path,
        runtime=None,
        runtime_bootstrap_error=None,
        request_headers=None,
        request_id="test-sprq-http",
    )


def _seed_proposal(config_path: str, *, proposal_id: str = "sprq-http-1") -> str:
    ctl = Skill(config_path)
    try:
        create_proposal(
            ctl.store,
            SkillProposal(
                proposal_id=proposal_id,
                source_task_shape_ref="task_shape:research|live_information|latest_news",
                proposed_skill_definition=SkillProposalDraft(
                    name="research-latest-news-playbook",
                    display_name="Research Latest News Playbook",
                    short_description="Seeded for HTTP route test.",
                    tools=[],
                    tags=["research", "live_information", "latest_news"],
                    risk_class="low",
                    applies_to={"intents": ["latest_news"], "steps": []},
                    inputs_schema=[],
                    verification_rules=[],
                ),
                evidence_refs=["performance:research|live_information|latest_news"],
                proposer_policy_id="skill_promotion_cadence_v1",
                proposed_at="",
            ),
        )
    finally:
        ctl.close()
    return proposal_id


@pytest.fixture
def cfg_path(tmp_path: Path) -> str:
    return _config_path(tmp_path)


def test_http_list_proposals_returns_seeded_pending(cfg_path: str) -> None:
    _seed_proposal(cfg_path)
    result = handle_request(
        _ctx(cfg_path),
        method_name="GET",
        path="/v1/skills/proposals",
        body=None,
        query=None,
    )
    assert result is not None
    assert result.status == HTTPStatus.OK
    payload = result.payload
    assert payload["ok"] is True
    assert payload["proposals"][0]["proposal_id"] == "sprq-http-1"
    assert payload["proposals"][0]["queue_state"] == "pending"


def test_http_list_proposals_filters_by_queue_state(cfg_path: str) -> None:
    _seed_proposal(cfg_path)
    result = handle_request(
        _ctx(cfg_path),
        method_name="GET",
        path="/v1/skills/proposals",
        body=None,
        query="queue_state=reviewed",
    )
    assert result is not None
    assert result.status == HTTPStatus.OK
    assert result.payload["proposals"] == []


def test_http_list_proposals_rejects_invalid_queue_state(cfg_path: str) -> None:
    _seed_proposal(cfg_path)
    result = handle_request(
        _ctx(cfg_path),
        method_name="GET",
        path="/v1/skills/proposals",
        body=None,
        query="queue_state=not-a-real-state",
    )
    assert result is not None
    assert result.status == HTTPStatus.BAD_REQUEST


def test_http_get_proposal_returns_full_record(cfg_path: str) -> None:
    _seed_proposal(cfg_path)
    result = handle_request(
        _ctx(cfg_path),
        method_name="GET",
        path="/v1/skills/proposals/sprq-http-1",
        body=None,
        query=None,
    )
    assert result is not None
    assert result.status == HTTPStatus.OK
    assert result.payload["proposal"]["proposal_id"] == "sprq-http-1"
    assert result.payload["proposal"]["review"] is None


def test_http_get_proposal_returns_404(cfg_path: str) -> None:
    result = handle_request(
        _ctx(cfg_path),
        method_name="GET",
        path="/v1/skills/proposals/missing-id",
        body=None,
        query=None,
    )
    assert result is not None
    assert result.status == HTTPStatus.NOT_FOUND
    assert result.payload["error"]["code"] == "NOT_FOUND"


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/v1/skills/proposals/sprq-http-1/review", {"reviewer_id": "operator-http"}),
        ("/v1/skills/proposals/sprq-http-1/apply", None),
    ],
)
def test_http_proposal_mutations_require_proven_operator_authority(
    cfg_path: str, path: str, body: dict | None
) -> None:
    _seed_proposal(cfg_path)
    result = handle_request(
        _ctx(cfg_path),
        method_name="POST",
        path=path,
        body=body,
        query=None,
    )
    assert result is not None
    assert result.status == HTTPStatus.FORBIDDEN
    assert result.payload["error"]["code"] == "SKILL_OPERATOR_AUTH_REQUIRED"


def test_http_router_does_not_swallow_skill_detail_routes(cfg_path: str) -> None:
    result = handle_request(
        _ctx(cfg_path),
        method_name="GET",
        path="/v1/skills/proposals",
        body=None,
        query=None,
    )
    assert result is not None
    assert result.status == HTTPStatus.OK
    assert "proposals" in result.payload
    assert "skill" not in result.payload
