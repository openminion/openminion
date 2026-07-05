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


def test_http_review_proposal_requires_body(cfg_path: str) -> None:
    _seed_proposal(cfg_path)
    result = handle_request(
        _ctx(cfg_path),
        method_name="POST",
        path="/v1/skills/proposals/sprq-http-1/review",
        body=None,
        query=None,
    )
    assert result is not None
    assert result.status == HTTPStatus.BAD_REQUEST


def test_http_review_proposal_requires_reviewer_id(cfg_path: str) -> None:
    _seed_proposal(cfg_path)
    result = handle_request(
        _ctx(cfg_path),
        method_name="POST",
        path="/v1/skills/proposals/sprq-http-1/review",
        body={
            "criterion_decisions": [
                {"criterion_id": "fit", "status": "accepted", "comment": "ok"}
            ]
        },
        query=None,
    )
    assert result is not None
    assert result.status == HTTPStatus.BAD_REQUEST


def test_http_review_proposal_requires_criteria(cfg_path: str) -> None:
    _seed_proposal(cfg_path)
    result = handle_request(
        _ctx(cfg_path),
        method_name="POST",
        path="/v1/skills/proposals/sprq-http-1/review",
        body={"reviewer_id": "operator-http"},
        query=None,
    )
    assert result is not None
    assert result.status == HTTPStatus.BAD_REQUEST


def test_http_review_proposal_accepts_operator_review(cfg_path: str) -> None:
    _seed_proposal(cfg_path)
    result = handle_request(
        _ctx(cfg_path),
        method_name="POST",
        path="/v1/skills/proposals/sprq-http-1/review",
        body={
            "reviewer_id": "operator-http",
            "review_policy_id": "sprq_review_v1",
            "criterion_decisions": [
                {
                    "criterion_id": "fit",
                    "status": "accepted",
                    "comment": "Matches intent.",
                }
            ],
        },
        query=None,
    )
    assert result is not None
    assert result.status == HTTPStatus.OK
    assert result.payload["review"]["status"] == "accepted"
    assert result.payload["review"]["reviewer_id"] == "operator-http"


@pytest.mark.parametrize(
    "runtime_id", ["runtime", "system", "auto", "automatic", "self"]
)
def test_http_review_proposal_rejects_runtime_reviewer(
    cfg_path: str, runtime_id: str
) -> None:
    _seed_proposal(cfg_path)
    result = handle_request(
        _ctx(cfg_path),
        method_name="POST",
        path="/v1/skills/proposals/sprq-http-1/review",
        body={
            "reviewer_id": runtime_id,
            "criterion_decisions": [
                {"criterion_id": "fit", "status": "accepted", "comment": "x"}
            ],
        },
        query=None,
    )
    assert result is not None
    assert result.status == HTTPStatus.BAD_REQUEST
    inspect = handle_request(
        _ctx(cfg_path),
        method_name="GET",
        path="/v1/skills/proposals/sprq-http-1",
        body=None,
        query=None,
    )
    assert inspect is not None
    assert inspect.payload["proposal"]["queue_state"] == "pending"


def test_http_review_proposal_returns_404_for_unknown(cfg_path: str) -> None:
    result = handle_request(
        _ctx(cfg_path),
        method_name="POST",
        path="/v1/skills/proposals/missing-id/review",
        body={
            "reviewer_id": "operator-http",
            "criterion_decisions": [
                {"criterion_id": "fit", "status": "accepted", "comment": "x"}
            ],
        },
        query=None,
    )
    assert result is not None
    assert result.status == HTTPStatus.NOT_FOUND


def test_http_apply_proposal_returns_addition(cfg_path: str) -> None:
    _seed_proposal(cfg_path)
    handle_request(
        _ctx(cfg_path),
        method_name="POST",
        path="/v1/skills/proposals/sprq-http-1/review",
        body={
            "reviewer_id": "operator-http",
            "criterion_decisions": [
                {"criterion_id": "fit", "status": "accepted", "comment": "Accept."}
            ],
        },
        query=None,
    )
    result = handle_request(
        _ctx(cfg_path),
        method_name="POST",
        path="/v1/skills/proposals/sprq-http-1/apply",
        body=None,
        query=None,
    )
    assert result is not None
    assert result.status == HTTPStatus.OK
    addition = result.payload["addition"]
    assert addition["added_skill_id"].startswith("emergent.")
    assert addition["added_by"] == "operator-http"


def test_http_apply_proposal_refuses_pending(cfg_path: str) -> None:
    _seed_proposal(cfg_path)
    result = handle_request(
        _ctx(cfg_path),
        method_name="POST",
        path="/v1/skills/proposals/sprq-http-1/apply",
        body=None,
        query=None,
    )
    assert result is not None
    assert result.status == HTTPStatus.BAD_REQUEST


def test_http_apply_proposal_returns_404_for_unknown(cfg_path: str) -> None:
    result = handle_request(
        _ctx(cfg_path),
        method_name="POST",
        path="/v1/skills/proposals/missing-id/apply",
        body=None,
        query=None,
    )
    assert result is not None
    assert result.status == HTTPStatus.NOT_FOUND


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
