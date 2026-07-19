from __future__ import annotations

import json
from http import HTTPStatus
from pathlib import Path

import pytest

from openminion.api.routes.contracts import APIRouteContext
from openminion.api.routes.skill import handle_request
from openminion.modules.skill.proposal import SkillProposal, SkillProposalDraft
from openminion.modules.skill.proposal.queue import (
    create_proposal,
    record_proposal_review,
)
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
        request_id="test-scsp-http",
    )


def _seed_proposal(config_path: str, *, proposal_id: str = "scsp-http-1") -> str:
    ctl = Skill(config_path)
    try:
        create_proposal(
            ctl.store,
            SkillProposal(
                proposal_id=proposal_id,
                source_task_shape_ref="task_shape:http|http|http",
                proposed_skill_definition=SkillProposalDraft(
                    name="http-playbook",
                    display_name="HTTP Playbook",
                    short_description="Seeded for SCSP HTTP test.",
                    tools=[],
                    tags=["http"],
                    risk_class="low",
                    applies_to={"intents": ["http"], "steps": []},
                    inputs_schema=[],
                    verification_rules=[],
                ),
                evidence_refs=[],
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


def test_http_suggestion_inbox_returns_pending(cfg_path: str) -> None:
    _seed_proposal(cfg_path)
    result = handle_request(
        _ctx(cfg_path),
        method_name="GET",
        path="/v1/skills/suggestions/inbox",
        body=None,
        query=None,
    )
    assert result is not None
    assert result.status == HTTPStatus.OK
    payload = result.payload
    assert payload["ok"] is True
    assert len(payload["suggestions"]) == 1
    assert payload["suggestions"][0]["proposal_id"] == "scsp-http-1"


def test_http_suggestion_status_returns_typed_counts(cfg_path: str) -> None:
    _seed_proposal(cfg_path)
    result = handle_request(
        _ctx(cfg_path),
        method_name="GET",
        path="/v1/skills/suggestions/status",
        body=None,
        query=None,
    )
    assert result is not None
    assert result.status == HTTPStatus.OK
    status = result.payload["status"]
    assert status["surfaced_count"] == 0
    assert status["pending_count"] == 1
    for forbidden in ("quality", "value", "score", "confidence"):
        assert forbidden not in status


def test_http_suggestion_surface_pass_writes_audit(cfg_path: str) -> None:
    _seed_proposal(cfg_path)
    result = handle_request(
        _ctx(cfg_path),
        method_name="POST",
        path="/v1/skills/suggestions/surface",
        body={},
        query=None,
    )
    assert result is not None
    assert result.status == HTTPStatus.OK
    assert len(result.payload["surfaced"]) == 1
    status_result = handle_request(
        _ctx(cfg_path),
        method_name="GET",
        path="/v1/skills/suggestions/status",
        body=None,
        query=None,
    )
    assert status_result is not None
    assert status_result.payload["status"]["surfaced_count"] == 1


def test_http_suggestion_status_counts_review_outcomes(cfg_path: str) -> None:
    ctl = Skill(cfg_path)
    try:
        create_proposal(
            ctl.store,
            SkillProposal(
                proposal_id="scsp-http-out-1",
                source_task_shape_ref="task_shape:out|out|out-1",
                proposed_skill_definition=SkillProposalDraft(
                    name="distinct-out-playbook-1",
                    display_name="Out 1",
                    short_description="x",
                    tools=[],
                    tags=["capability-out-http-1"],
                    risk_class="low",
                    applies_to={"intents": ["intent-out-http-1"], "steps": []},
                    inputs_schema=[],
                    verification_rules=[],
                ),
                evidence_refs=[],
                proposer_policy_id="skill_promotion_cadence_v1",
                proposed_at="",
            ),
        )
        record_proposal_review(
            ctl.store,
            proposal_id="scsp-http-out-1",
            reviewer_id="operator-http",
            review_policy_id="scsp",
            criterion_decisions=[
                {
                    "criterion_id": "fit",
                    "status": "deferred",
                    "comment": "later",
                }
            ],
        )
    finally:
        ctl.close()
    result = handle_request(
        _ctx(cfg_path),
        method_name="GET",
        path="/v1/skills/suggestions/status",
        body=None,
        query=None,
    )
    assert result is not None
    assert result.payload["status"]["deferred_count"] == 1


def test_http_suggestion_routes_do_not_swallow_proposal_routes(
    cfg_path: str,
) -> None:
    _seed_proposal(cfg_path)
    result = handle_request(
        _ctx(cfg_path),
        method_name="GET",
        path="/v1/skills/proposals",
        body=None,
        query=None,
    )
    assert result is not None
    assert "proposals" in result.payload
    assert "suggestions" not in result.payload
