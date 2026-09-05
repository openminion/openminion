from types import SimpleNamespace
from typing import Any

import pytest

from openminion.modules.tool.errors import ToolRuntimeError
from openminion.tools.github.rest import GithubRestProvider


def _ctx() -> SimpleNamespace:
    return SimpleNamespace(
        agent_profile=SimpleNamespace(
            provider_config_overrides={
                "github": {
                    "allowed_repositories": ["openminion/release-test"],
                    "allowed_workflows": ["release.yml"],
                    "allowed_workflow_refs": ["v1.2.3-rc1"],
                    "allowed_workflow_targets": ["testpypi", "pypi"],
                    "allowed_workflow_inputs": {
                        "request_id": ["release-123"],
                        "target": ["testpypi", "pypi"],
                    },
                }
            }
        )
    )


def _workflow_args(**updates: Any) -> dict[str, Any]:
    return {
        "owner": "openminion",
        "repo": "release-test",
        "workflow": "release.yml",
        "ref": "v1.2.3-rc1",
        "request_id": "release-123",
        "target": "testpypi",
        "inputs": {"request_id": "release-123", "target": "testpypi"},
        **updates,
    }


def _run(run_id: int = 7) -> dict[str, Any]:
    return {
        "id": run_id,
        "workflow_id": 9,
        "display_title": "release-123",
        "head_branch": "v1.2.3-rc1",
        "head_sha": "a" * 40,
        "event": "workflow_dispatch",
        "status": "queued",
        "conclusion": None,
        "html_url": f"https://github.com/openminion/release-test/actions/runs/{run_id}",
    }


class _Provider(GithubRestProvider):
    def __init__(
        self, responses: list[Any], not_found: list[Any] | None = None
    ) -> None:
        self.responses = iter(responses)
        self.not_found = iter(not_found or [])
        self.calls: list[dict[str, Any]] = []

    def _request_json(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return next(self.responses)

    def _request_json_or_none_on_404(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return next(self.not_found)


def test_dispatch_workflow_returns_exact_bounded_readback() -> None:
    provider = _Provider([None, {"workflow_runs": [_run()]}])
    result = provider.dispatch_workflow(args=_workflow_args(), ctx=_ctx())

    assert result["data"]["match"] == "exact"
    assert result["data"]["runs"][0]["run_id"] == 7
    assert provider.calls[0]["method"] == "POST"
    assert provider.calls[0]["body"] == {
        "ref": "v1.2.3-rc1",
        "inputs": {"request_id": "release-123", "target": "testpypi"},
    }


@pytest.mark.parametrize(
    ("updates", "reason"),
    [
        ({"workflow": "other.yml"}, "POLICY_DENIED_WORKFLOW"),
        ({"ref": "main"}, "POLICY_DENIED_WORKFLOW_REF"),
        (
            {"inputs": {"request_id": "release-123", "target": "prod"}},
            "POLICY_DENIED_WORKFLOW_INPUT",
        ),
        (
            {"inputs": {"request_id": "release-123", "other": "testpypi"}},
            "POLICY_DENIED_WORKFLOW_INPUT",
        ),
        ({"target": "prod"}, "POLICY_DENIED_WORKFLOW_TARGET"),
    ],
)
def test_dispatch_workflow_rejects_unallowlisted_values(
    updates: dict[str, Any], reason: str
) -> None:
    with pytest.raises(ToolRuntimeError) as exc:
        _Provider([]).dispatch_workflow(args=_workflow_args(**updates), ctx=_ctx())
    assert exc.value.code == "POLICY_DENIED"
    assert exc.value.details["reason_code"] == reason


def test_list_workflow_runs_reports_ambiguous_bounded_matches() -> None:
    provider = _Provider([{"workflow_runs": [_run(1), _run(2)]}])
    result = provider.list_workflow_runs(args=_workflow_args(), ctx=_ctx())
    assert result["data"]["match"] == "ambiguous"
    assert [row["run_id"] for row in result["data"]["runs"]] == [1, 2]


def test_list_workflow_runs_does_not_claim_exact_when_truncated() -> None:
    rows = [_run(), *[{**_run(index), "display_title": "other"} for index in range(8, 57)]]
    provider = _Provider([{"workflow_runs": rows}])
    result = provider.list_workflow_runs(
        args={**_workflow_args(), "limit": 1}, ctx=_ctx()
    )
    assert result["data"]["match"] == "ambiguous"
    assert result["data"]["truncated"] is True


@pytest.mark.parametrize("updates", [{"workflow": "other.yml"}, {"ref": "main"}])
def test_list_workflow_runs_rejects_unallowlisted_identity(
    updates: dict[str, Any],
) -> None:
    provider = _Provider([])
    with pytest.raises(ToolRuntimeError) as exc:
        provider.list_workflow_runs(args=_workflow_args(**updates), ctx=_ctx())
    assert exc.value.code == "POLICY_DENIED"
    assert provider.calls == []


def _release_args(**updates: Any) -> dict[str, Any]:
    return {
        "owner": "openminion",
        "repo": "release-test",
        "tag": "v1.2.3-rc1",
        "expected_commit_sha": "a" * 40,
        "title": "v1.2.3-rc1",
        "notes": "RC notes",
        "draft": True,
        "prerelease": True,
        **updates,
    }


def test_create_release_uses_existing_dereferenced_tag() -> None:
    release = {
        "id": 17,
        "tag_name": "v1.2.3-rc1",
        "name": "v1.2.3-rc1",
        "body": "RC notes",
        "draft": True,
        "prerelease": True,
        "html_url": "https://github.com/openminion/release-test/releases/tag/v1.2.3-rc1",
    }
    provider = _Provider(
        [release],
        not_found=[{"object": {"type": "commit", "sha": "a" * 40}}, None],
    )
    result = provider.create_release(args=_release_args(), ctx=_ctx())
    assert result["data"]["tag_sha"] == "a" * 40
    assert result["data"]["release"]["release_id"] == 17
    assert provider.calls[-1]["body"] == {
        "tag_name": "v1.2.3-rc1",
        "name": "v1.2.3-rc1",
        "body": "RC notes",
        "draft": True,
        "prerelease": True,
    }


def test_create_release_rejects_tag_sha_mismatch_before_post() -> None:
    provider = _Provider(
        [], not_found=[{"object": {"type": "commit", "sha": "b" * 40}}, None]
    )
    with pytest.raises(ToolRuntimeError) as exc:
        provider.create_release(args=_release_args(), ctx=_ctx())
    assert exc.value.details["reason_code"] == "github_release_tag_sha_mismatch"
    assert all(call.get("method", "GET") == "GET" for call in provider.calls)


def test_create_release_rejects_existing_release_before_post() -> None:
    existing = {
        "id": 17,
        "tag_name": "v1.2.3-rc1",
        "name": "v1.2.3-rc1",
        "body": "RC notes",
        "draft": True,
        "prerelease": True,
        "html_url": "https://github.com/openminion/release-test/releases/tag/v1.2.3-rc1",
    }
    provider = _Provider(
        [],
        not_found=[{"object": {"type": "commit", "sha": "a" * 40}}, existing],
    )
    with pytest.raises(ToolRuntimeError) as exc:
        provider.create_release(args=_release_args(), ctx=_ctx())
    assert exc.value.code == "ALREADY_EXISTS"
    assert all(call.get("method", "GET") == "GET" for call in provider.calls)


def test_read_release_dereferences_one_annotated_tag() -> None:
    provider = _Provider(
        [{"object": {"type": "commit", "sha": "a" * 40}}],
        not_found=[{"object": {"type": "tag", "sha": "b" * 40}}, None],
    )
    result = provider.read_release(args=_release_args(), ctx=_ctx())
    assert result["data"]["tag_sha"] == "a" * 40
    assert result["data"]["release"] is None
