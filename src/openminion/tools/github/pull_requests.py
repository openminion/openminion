"""GitHub pull-request response shaping."""

from collections.abc import Mapping
from typing import Any


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


__all__ = ["open_pr_result", "update_pr_result"]
