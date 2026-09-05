from __future__ import annotations

from openminion.modules.tool.contracts.model_ids import (
    MODEL_GITHUB_COMMIT_FILES,
    MODEL_GITHUB_FETCH_CHECKS,
    MODEL_GITHUB_FETCH_COMMENTS,
    MODEL_GITHUB_FETCH_DIFF,
    MODEL_GITHUB_FETCH_PR,
    MODEL_GITHUB_LIST_PRS,
    MODEL_GITHUB_OPEN_PR,
    MODEL_GITHUB_UPDATE_PR,
    MODEL_GITHUB_MERGE_PR,
    MODEL_GITHUB_DISPATCH_WORKFLOW,
    MODEL_GITHUB_LIST_WORKFLOW_RUNS,
    MODEL_GITHUB_CREATE_RELEASE,
    MODEL_GITHUB_POST_PR_COMMENT,
    MODEL_GITHUB_POST_PR_REVIEW,
)
from openminion.modules.tool.contracts.runtime_ids import (
    RUNTIME_GITHUB_COMMIT_FILES,
    RUNTIME_GITHUB_FETCH_CHECKS,
    RUNTIME_GITHUB_FETCH_COMMENTS,
    RUNTIME_GITHUB_FETCH_DIFF,
    RUNTIME_GITHUB_FETCH_PR,
    RUNTIME_GITHUB_LIST_PRS,
    RUNTIME_GITHUB_OPEN_PR,
    RUNTIME_GITHUB_UPDATE_PR,
    RUNTIME_GITHUB_MERGE_PR,
    RUNTIME_GITHUB_DISPATCH_WORKFLOW,
    RUNTIME_GITHUB_LIST_WORKFLOW_RUNS,
    RUNTIME_GITHUB_CREATE_RELEASE,
    RUNTIME_GITHUB_POST_PR_COMMENT,
    RUNTIME_GITHUB_POST_PR_REVIEW,
)
from openminion.tools.github.registrar import REGISTRAR


def test_registrar_module_id() -> None:
    assert REGISTRAR.module_id == "github"
    assert REGISTRAR.is_provider_only is False


def test_manifest_lists_all_model_tools() -> None:
    manifest = REGISTRAR.get_manifest(ctx=None)
    model_ids = {entry.model_tool_id for entry in manifest.model_tools}
    assert model_ids == {
        MODEL_GITHUB_LIST_PRS,
        MODEL_GITHUB_FETCH_PR,
        MODEL_GITHUB_FETCH_DIFF,
        MODEL_GITHUB_FETCH_COMMENTS,
        MODEL_GITHUB_FETCH_CHECKS,
        MODEL_GITHUB_COMMIT_FILES,
        MODEL_GITHUB_OPEN_PR,
        MODEL_GITHUB_UPDATE_PR,
        MODEL_GITHUB_MERGE_PR,
        MODEL_GITHUB_DISPATCH_WORKFLOW,
        MODEL_GITHUB_LIST_WORKFLOW_RUNS,
        MODEL_GITHUB_CREATE_RELEASE,
        MODEL_GITHUB_POST_PR_REVIEW,
        MODEL_GITHUB_POST_PR_COMMENT,
    }


def test_manifest_lists_all_runtime_bindings() -> None:
    manifest = REGISTRAR.get_manifest(ctx=None)
    binding_ids = {entry.runtime_binding_id for entry in manifest.runtime_bindings}
    assert binding_ids == {
        RUNTIME_GITHUB_LIST_PRS,
        RUNTIME_GITHUB_FETCH_PR,
        RUNTIME_GITHUB_FETCH_DIFF,
        RUNTIME_GITHUB_FETCH_COMMENTS,
        RUNTIME_GITHUB_FETCH_CHECKS,
        RUNTIME_GITHUB_COMMIT_FILES,
        RUNTIME_GITHUB_OPEN_PR,
        RUNTIME_GITHUB_UPDATE_PR,
        RUNTIME_GITHUB_MERGE_PR,
        RUNTIME_GITHUB_DISPATCH_WORKFLOW,
        RUNTIME_GITHUB_LIST_WORKFLOW_RUNS,
        RUNTIME_GITHUB_CREATE_RELEASE,
        RUNTIME_GITHUB_POST_PR_REVIEW,
        RUNTIME_GITHUB_POST_PR_COMMENT,
    }


def test_runtime_candidates_match_canonical_tool_names() -> None:
    manifest = REGISTRAR.get_manifest(ctx=None)
    candidates_by_binding = {
        entry.runtime_binding_id: entry.runtime_candidates
        for entry in manifest.runtime_bindings
    }
    assert candidates_by_binding[RUNTIME_GITHUB_LIST_PRS] == ("github.list_prs",)
    assert candidates_by_binding[RUNTIME_GITHUB_FETCH_PR] == ("github.fetch_pr",)
    assert candidates_by_binding[RUNTIME_GITHUB_FETCH_DIFF] == ("github.fetch_diff",)
    assert candidates_by_binding[RUNTIME_GITHUB_FETCH_COMMENTS] == (
        "github.fetch_comments",
    )
    assert candidates_by_binding[RUNTIME_GITHUB_FETCH_CHECKS] == (
        "github.fetch_checks",
    )
    assert candidates_by_binding[RUNTIME_GITHUB_COMMIT_FILES] == (
        "github.commit_files",
    )
    assert candidates_by_binding[RUNTIME_GITHUB_OPEN_PR] == ("github.open_pr",)
    assert candidates_by_binding[RUNTIME_GITHUB_UPDATE_PR] == ("github.update_pr",)
    assert candidates_by_binding[RUNTIME_GITHUB_MERGE_PR] == ("github.merge_pr",)
    assert candidates_by_binding[RUNTIME_GITHUB_DISPATCH_WORKFLOW] == (
        "github.dispatch_workflow",
    )
    assert candidates_by_binding[RUNTIME_GITHUB_LIST_WORKFLOW_RUNS] == (
        "github.list_workflow_runs",
    )
    assert candidates_by_binding[RUNTIME_GITHUB_CREATE_RELEASE] == (
        "github.create_release",
    )
    assert candidates_by_binding[RUNTIME_GITHUB_POST_PR_REVIEW] == (
        "github.post_pr_review",
    )
    assert candidates_by_binding[RUNTIME_GITHUB_POST_PR_COMMENT] == (
        "github.post_pr_comment",
    )
