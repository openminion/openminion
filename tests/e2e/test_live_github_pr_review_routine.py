from __future__ import annotations

import json
import os

import pytest

from openminion.modules.tool.registry import ToolRegistry
from openminion.tools.github.auth import require_github_pat
from openminion.tools.github.interfaces import TOOL_GITHUB_LIST_PRS
from openminion.tools.github.plugin import register as register_github_tools
from openminion.tools.github.providers import provider_registry, register_provider
from openminion.tools.github.rest import GithubRestProvider
from openminion.tools.task.routine.dispatcher import GitHubPrReviewHandler
from openminion.tools.task.routine.schemas import (
    GitHubPrReviewConfigV1,
    RoutinePayloadV1,
)
from openminion.services.runtime.routine_context import (
    CronRunRoutineSink,
    ToolRegistryPreTurnContext,
)

pytestmark = pytest.mark.e2e


_LIVE_FLAG = "OPENMINION_LIVE_GITHUB_PR_REVIEW_E2E"
_OWNER_ENV = "OPENMINION_LIVE_GITHUB_OWNER"
_REPO_ENV = "OPENMINION_LIVE_GITHUB_REPO"


def _live_enabled() -> bool:
    return os.environ.get(_LIVE_FLAG, "").strip() in {"1", "true", "yes"}


def _has_pat() -> bool:
    return bool(os.environ.get("GITHUB_TOKEN", "").strip())


def _target_owner_repo() -> tuple[str, str]:
    owner = os.environ.get(_OWNER_ENV, "openminion").strip()
    repo = os.environ.get(_REPO_ENV, "test-repo-for-agent").strip()
    return owner, repo


@pytest.mark.skipif(
    not _live_enabled(),
    reason=f"set {_LIVE_FLAG}=1 to enable BRPR-09 live test",
)
@pytest.mark.skipif(
    not _has_pat(),
    reason="GITHUB_TOKEN not set; BRPR-09 live test requires a PAT",
)
def test_brpr_09_live_github_provider_routine_smoke() -> None:
    pat = require_github_pat(env=os.environ)
    assert pat, "GITHUB_TOKEN must be a non-empty PAT"
    owner, repo = _target_owner_repo()

    routine = RoutinePayloadV1(
        config=GitHubPrReviewConfigV1(
            owner=owner,
            repo=repo,
            state_filter="open",
        )
    )
    registry = ToolRegistry()
    register_github_tools(registry)
    provider_registry().reset()
    register_provider(GithubRestProvider())
    try:
        ctx = ToolRegistryPreTurnContext(
            registry=registry,
            routine_id="brpr-09-live",
            session_id="brpr-09-live-session",
            agent_id="brpr-09-live-agent",
        )
        provider_probe = ctx.invoke_tool(
            name=TOOL_GITHUB_LIST_PRS,
            args={"owner": owner, "repo": repo, "state": "open", "limit": 10},
        )
        assert provider_probe.get("ok") is True, (
            f"live github.list_prs failed for {owner}/{repo}: "
            f"{provider_probe.get('error')}"
        )
        open_prs = provider_probe.get("data", {}).get("open_prs", [])
        assert open_prs, f"{owner}/{repo} must have at least one open PR for BRPR-09"

        handler = GitHubPrReviewHandler()
        facts = handler.pre_turn(
            routine=routine,
            routine_id="brpr-09-live",
            ctx=ctx,
        )
        assert facts.repo == f"{owner}/{repo}"
        assert facts.open_prs

        first = facts.open_prs[0]
        outcome_text = "<routine_outcome>{}</routine_outcome>".format(
            json.dumps(
                {
                    "reviewed_prs": [
                        {
                            "number": first.number,
                            "head_sha_reviewed": first.head_sha,
                            "review_state": "needs_human_review",
                            "summary": "Live BRPR-09 smoke review.",
                            "findings": [],
                        }
                    ]
                }
            )
        )
        sink = CronRunRoutineSink()
        result = handler.post_turn(
            routine=routine,
            routine_id="brpr-09-live",
            facts=facts,
            outcome_text=outcome_text,
            sink=sink,
        )
        assert result.ok is True
        assert result.artifact_id
        assert sink.artifact_body
        assert (
            result.updated_routine.cursor.last_review_per_pr[str(first.number)].head_sha
            == first.head_sha
        )
    finally:
        provider_registry().reset()
