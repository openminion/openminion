"""GitHub pull-request response shaping."""

from collections.abc import Mapping
from typing import Any

from openminion.modules.tool.errors import ToolRuntimeError


def normalize_pr_summary(raw: Mapping[str, Any]) -> dict[str, Any]:
    head = _pr_mapping(raw.get("head"))
    base = _pr_mapping(raw.get("base"))
    user = _pr_mapping(raw.get("user"))
    return {
        "number": int(raw.get("number") or 0),
        "title": str(raw.get("title") or ""),
        "author": str(user.get("login") or ""),
        "head_sha": str(head.get("sha") or ""),
        "base_ref": str(base.get("ref") or "main"),
        "head_ref": str(head.get("ref") or ""),
        "draft": bool(raw.get("draft", False)),
        "mergeable_state": str(raw.get("mergeable_state") or "unknown"),
        "checks_status": "none",
        "labels": _label_names(raw.get("labels")),
        "review_state": "none",
        "lines_added": int(raw.get("additions") or 0),
        "lines_deleted": int(raw.get("deletions") or 0),
        "diff_truncated": False,
        "diff_preview": "",
        "comments_count": int(raw.get("comments") or 0)
        + int(raw.get("review_comments") or 0),
        "url": str(raw.get("html_url") or ""),
    }


def normalize_comments(raw: Any, *, kind: str) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    comments: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        user = _pr_mapping(item.get("user"))
        comments.append(
            {
                "id": item.get("id"),
                "kind": kind,
                "author": str(user.get("login") or ""),
                "body": str(item.get("body") or ""),
                "url": str(item.get("html_url") or ""),
            }
        )
    return comments


def open_pr_result(
    row: Mapping[str, Any],
    *,
    owner: str,
    repo: str,
    head: str,
    base: str,
    head_sha: str,
    provider_id: str,
) -> dict[str, Any]:
    data = {
        "owner": owner,
        "repo": repo,
        "number": int(row.get("number") or 0),
        "html_url": str(row.get("html_url") or ""),
        "head": head,
        "base": base,
        "state": str(row.get("state") or ""),
    }
    if head_sha:
        data["head_sha"] = head_sha
    return {
        "ok": True,
        "data": data,
        "source": {"provider_id": provider_id},
    }


def update_pr_result(
    row: Mapping[str, Any],
    *,
    owner: str,
    repo: str,
    provider_id: str,
) -> dict[str, Any]:
    raw_head = row.get("head")
    head = raw_head if isinstance(raw_head, Mapping) else {}
    return {
        "ok": True,
        "data": {
            "owner": owner,
            "repo": repo,
            "number": int(row.get("number") or 0),
            "html_url": str(row.get("html_url") or ""),
            "title": str(row.get("title") or ""),
            "body": str(row.get("body") or ""),
            "state": str(row.get("state") or ""),
            "head_sha": str(head.get("sha") or ""),
        },
        "source": {"provider_id": provider_id},
    }


def merge_pr_readback_result(
    row: Mapping[str, Any],
    *,
    owner: str,
    repo: str,
    provider_id: str,
) -> dict[str, Any]:
    head = row.get("head")
    head_data = head if isinstance(head, Mapping) else {}
    return {
        "ok": True,
        "data": {
            "owner": owner,
            "repo": repo,
            "number": int(row.get("number") or 0),
            "html_url": str(row.get("html_url") or ""),
            "state": str(row.get("state") or ""),
            "head_sha": str(head_data.get("sha") or ""),
            "merged": row.get("merged"),
            "merge_commit_sha": str(row.get("merge_commit_sha") or ""),
        },
        "source": {"provider_id": provider_id},
    }


def merge_pr_result(
    row: Mapping[str, Any],
    *,
    owner: str,
    repo: str,
    number: int,
    head_sha: str,
    merge_method: str,
    provider_id: str,
) -> dict[str, Any]:
    return {
        "ok": True,
        "data": {
            "owner": owner,
            "repo": repo,
            "number": number,
            "merged": True,
            "message": str(row.get("message") or ""),
            "head_sha": head_sha,
            "merge_method": merge_method,
            "merge_commit_sha": str(row.get("sha") or ""),
        },
        "source": {"provider_id": provider_id},
    }


def require_merge_pr_ready(
    result: Mapping[str, Any],
    *,
    expected_owner: str,
    expected_repo: str,
    expected_number: int,
    expected_head_sha: str,
    allow_merged: bool = False,
) -> Mapping[str, Any]:
    data = result.get("data")
    if not isinstance(data, Mapping):
        raise ToolRuntimeError(
            "INVALID_RESPONSE",
            "GitHub merge preflight omitted pull-request data.",
            {"reason_code": "github_merge_pr_preflight_bad_result"},
        )
    target = (
        str(data.get("owner") or ""),
        str(data.get("repo") or ""),
        int(data.get("number") or 0),
    )
    if target != (expected_owner, expected_repo, expected_number):
        raise ToolRuntimeError(
            "INVALID_RESPONSE",
            "GitHub merge preflight did not match the approved action.",
            {"reason_code": "github_merge_pr_result_mismatch"},
        )
    head_sha = str(data.get("head_sha") or "")
    if not head_sha:
        raise ToolRuntimeError(
            "INVALID_RESPONSE",
            "GitHub merge preflight omitted the pull-request head SHA.",
            {"reason_code": "github_merge_pr_head_sha_missing"},
        )
    if head_sha != expected_head_sha:
        raise ToolRuntimeError(
            "INVALID_REQUEST",
            "The pull-request head changed after merge approval.",
            {
                "reason_code": "github_merge_pr_stale_head",
                "expected_head_sha": expected_head_sha,
                "current_head_sha": head_sha,
            },
        )
    merged = data.get("merged")
    if not isinstance(merged, bool):
        raise ToolRuntimeError(
            "INVALID_RESPONSE",
            "GitHub merge preflight returned an invalid merged state.",
            {"reason_code": "github_merge_pr_preflight_bad_result"},
        )
    if merged and allow_merged:
        return data
    if merged:
        raise ToolRuntimeError(
            "INVALID_REQUEST",
            "The pull request is already merged.",
            {"reason_code": "github_merge_pr_already_merged"},
        )
    state = str(data.get("state") or "")
    if state != "open":
        raise ToolRuntimeError(
            "INVALID_REQUEST",
            "Only an open pull request can be merged.",
            {"reason_code": "github_merge_pr_not_open", "state": state},
        )
    return data


def require_merge_checks(
    result: Mapping[str, Any],
    *,
    expected_head_sha: str,
    expected_checks: list[str],
) -> Mapping[str, Any]:
    data = result.get("data")
    if not isinstance(data, Mapping):
        raise ToolRuntimeError(
            "INVALID_RESPONSE",
            "GitHub merge preflight omitted check data.",
            {"reason_code": "github_merge_pr_checks_bad_result"},
        )
    returned_head_sha = str(data.get("head_sha") or "")
    returned_checks = data.get("expected_checks")
    if returned_head_sha != expected_head_sha or returned_checks != expected_checks:
        raise ToolRuntimeError(
            "INVALID_RESPONSE",
            "GitHub merge preflight returned mismatched check facts.",
            {"reason_code": "github_merge_pr_checks_mismatch"},
        )
    missing = data.get("missing_expected_checks")
    if not isinstance(missing, list):
        raise ToolRuntimeError(
            "INVALID_RESPONSE",
            "GitHub merge preflight returned malformed missing-check facts.",
            {"reason_code": "github_merge_pr_checks_bad_result"},
        )
    if missing:
        raise ToolRuntimeError(
            "INVALID_REQUEST",
            "Required pull-request checks are missing.",
            {
                "reason_code": "github_merge_pr_expected_checks_missing",
                "missing_expected_checks": list(missing),
            },
        )
    overall = str(data.get("overall_result") or "")
    if overall == "failure":
        raise ToolRuntimeError(
            "INVALID_REQUEST",
            "Required pull-request checks failed.",
            {"reason_code": "github_merge_pr_checks_failed"},
        )
    if overall == "pending":
        raise ToolRuntimeError(
            "INVALID_REQUEST",
            "Required pull-request checks are still pending.",
            {"reason_code": "github_merge_pr_checks_pending"},
        )
    if overall != "success":
        raise ToolRuntimeError(
            "INVALID_RESPONSE",
            "GitHub merge preflight returned an unknown check result.",
            {"reason_code": "github_merge_pr_checks_bad_result"},
        )
    return data


def _pr_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _label_names(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    names: list[str] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name") or "").strip()
        if name:
            names.append(name)
    return names


__all__ = [
    "merge_pr_readback_result",
    "merge_pr_result",
    "normalize_comments",
    "normalize_pr_summary",
    "open_pr_result",
    "require_merge_checks",
    "require_merge_pr_ready",
    "update_pr_result",
]
