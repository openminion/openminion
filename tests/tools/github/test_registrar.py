from __future__ import annotations

from openminion.modules.tool.contracts.model_ids import (
    MODEL_GITHUB_COMMIT_FILES,
    MODEL_GITHUB_FETCH_CHECKS,
    MODEL_GITHUB_FETCH_COMMENTS,
    MODEL_GITHUB_FETCH_DIFF,
    MODEL_GITHUB_FETCH_PR,
    MODEL_GITHUB_LIST_PRS,
    MODEL_GITHUB_OPEN_PR,
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
    RUNTIME_GITHUB_POST_PR_COMMENT,
    RUNTIME_GITHUB_POST_PR_REVIEW,
)
from openminion.tools.github.registrar import REGISTRAR


def test_registrar_module_id() -> None:
    assert REGISTRAR.module_id == "github"
    assert REGISTRAR.is_provider_only is False


def test_manifest_lists_all_nine_model_tools() -> None:
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
        MODEL_GITHUB_POST_PR_REVIEW,
        MODEL_GITHUB_POST_PR_COMMENT,
    }


def test_manifest_lists_all_nine_runtime_bindings() -> None:
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
    assert candidates_by_binding[RUNTIME_GITHUB_POST_PR_REVIEW] == (
        "github.post_pr_review",
    )
    assert candidates_by_binding[RUNTIME_GITHUB_POST_PR_COMMENT] == (
        "github.post_pr_comment",
    )
