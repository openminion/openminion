"""GitHub REST provider."""

import io
import json
from collections.abc import Mapping
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from openminion.modules.tool.errors import ToolRuntimeError

from .auth import auth_invalid_error, require_github_pat
from .constants import DEFAULT_GITHUB_DIFF_MAX_LINES, DEFAULT_GITHUB_PROVIDER_ID
from .policy import (
    ensure_base_branch_allowed,
    ensure_branch_allowed,
    ensure_delete_allowed,
    ensure_force_push_allowed,
    ensure_merge_allowed,
    ensure_paths_allowed,
    ensure_pr_head_allowed,
    ensure_repository_allowed,
    github_write_policy_from_context,
)
from .env import get_github_api_base_url, get_github_timeout_seconds


class GithubRestProvider:
    """GitHub REST provider for factual reads and bounded smoke writes."""

    provider_id = DEFAULT_GITHUB_PROVIDER_ID

    def list_prs(self, *, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
        owner, repo = _owner_repo(args)
        state = str(args.get("state") or "open")
        limit = int(args.get("limit") or 20)
        rows = self._request_json(
            ctx=ctx,
            path=f"/repos/{owner}/{repo}/pulls",
            query={"state": state, "per_page": str(limit)},
        )
        if not isinstance(rows, list):
            raise _protocol_error("github.list_prs expected a list response")
        return {
            "ok": True,
            "data": {
                "open_prs": [_normalize_pr_summary(item) for item in rows[:limit]],
            },
            "source": {"provider_id": self.provider_id},
        }

    def fetch_pr(self, *, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
        owner, repo = _owner_repo(args)
        number = int(args.get("number") or 0)
        row = self._request_json(
            ctx=ctx,
            path=f"/repos/{owner}/{repo}/pulls/{number}",
        )
        if not isinstance(row, Mapping):
            raise _protocol_error("github.fetch_pr expected an object response")
        return {
            "ok": True,
            "data": {"pull_request": _normalize_pr_summary(row)},
            "source": {"provider_id": self.provider_id},
        }

    def fetch_diff(self, *, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
        owner, repo = _owner_repo(args)
        number = int(args.get("number") or 0)
        max_lines = int(args.get("max_lines") or DEFAULT_GITHUB_DIFF_MAX_LINES)
        text = self._request_text(
            ctx=ctx,
            path=f"/repos/{owner}/{repo}/pulls/{number}",
            accept="application/vnd.github.v3.diff",
        )
        lines = text.splitlines()
        truncated = len(lines) > max_lines
        return {
            "ok": True,
            "data": {
                "diff": "\n".join(lines[:max_lines]),
                "truncated": truncated,
                "line_count": len(lines),
            },
            "source": {"provider_id": self.provider_id},
        }

    def fetch_comments(self, *, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
        owner, repo = _owner_repo(args)
        number = int(args.get("number") or 0)
        limit = int(args.get("limit") or 50)
        issue_comments = self._request_json(
            ctx=ctx,
            path=f"/repos/{owner}/{repo}/issues/{number}/comments",
            query={"per_page": str(limit)},
        )
        review_comments = self._request_json(
            ctx=ctx,
            path=f"/repos/{owner}/{repo}/pulls/{number}/comments",
            query={"per_page": str(limit)},
        )
        comments = _normalize_comments(issue_comments, kind="issue")
        comments.extend(_normalize_comments(review_comments, kind="review"))
        return {
            "ok": True,
            "data": {"comments": comments[:limit]},
            "source": {"provider_id": self.provider_id},
        }

    def fetch_checks(self, *, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
        owner, repo = _owner_repo(args)
        head_sha = str(args.get("head_sha") or "").strip()
        combined = self._request_json(
            ctx=ctx,
            path=f"/repos/{owner}/{repo}/commits/{head_sha}/status",
        )
        if not isinstance(combined, Mapping):
            raise _protocol_error("github.fetch_checks expected an object response")
        return {
            "ok": True,
            "data": {
                "head_sha": head_sha,
                "checks_status": _normalize_combined_status(combined),
                "state": str(combined.get("state") or "none"),
                "statuses": list(combined.get("statuses") or []),
            },
            "source": {"provider_id": self.provider_id},
        }

    def commit_files(self, *, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
        owner, repo = _owner_repo(args)
        branch = str(args.get("branch") or "").strip()
        base_branch = str(args.get("base_branch") or "").strip()
        message = str(args.get("message") or "").strip()
        files = list(args.get("files") or [])
        force = bool(args.get("force", False))
        policy = github_write_policy_from_context(ctx)

        ensure_repository_allowed(owner=owner, repo=repo, config=policy)
        ensure_branch_allowed(branch=branch, base_branch=base_branch, config=policy)
        ensure_paths_allowed(
            paths=[
                str(item.get("path") or "")
                for item in files
                if isinstance(item, Mapping)
            ],
            config=policy,
        )
        ensure_force_push_allowed(force=force, config=policy)

        base_sha = self._resolve_branch_head_sha(
            ctx=ctx,
            owner=owner,
            repo=repo,
            branch=base_branch,
        )
        branch_exists = self._branch_exists(
            ctx=ctx,
            owner=owner,
            repo=repo,
            branch=branch,
        )
        parent_sha = (
            self._resolve_branch_head_sha(
                ctx=ctx,
                owner=owner,
                repo=repo,
                branch=branch,
            )
            if branch_exists
            else base_sha
        )
        if not branch_exists:
            self._request_json(
                ctx=ctx,
                path=f"/repos/{owner}/{repo}/git/refs",
                method="POST",
                body={"ref": f"refs/heads/{branch}", "sha": base_sha},
            )

        commit_row = self._request_json(
            ctx=ctx,
            path=f"/repos/{owner}/{repo}/git/commits/{parent_sha}",
        )
        tree_sha = _extract_commit_tree_sha(commit_row)
        tree_entries = [
            {
                "path": str(item.get("path") or ""),
                "mode": "100644",
                "type": "blob",
                "content": str(item.get("content") or ""),
            }
            for item in files
            if isinstance(item, Mapping)
        ]
        tree_row = self._request_json(
            ctx=ctx,
            path=f"/repos/{owner}/{repo}/git/trees",
            method="POST",
            body={"base_tree": tree_sha, "tree": tree_entries},
        )
        new_tree_sha = str(tree_row.get("sha") or "")
        commit_create = self._request_json(
            ctx=ctx,
            path=f"/repos/{owner}/{repo}/git/commits",
            method="POST",
            body={
                "message": message,
                "tree": new_tree_sha,
                "parents": [parent_sha],
            },
        )
        new_commit_sha = str(commit_create.get("sha") or "")
        self._request_json(
            ctx=ctx,
            path=f"/repos/{owner}/{repo}/git/refs/heads/{branch}",
            method="PATCH",
            body={"sha": new_commit_sha, "force": False},
        )
        return {
            "ok": True,
            "data": {
                "branch": branch,
                "commit_sha": new_commit_sha,
                "files": [entry["path"] for entry in tree_entries],
            },
            "source": {"provider_id": self.provider_id},
        }

    def open_pr(self, *, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
        owner, repo = _owner_repo(args)
        head = str(args.get("head") or "").strip()
        base = str(args.get("base") or "").strip()
        policy = github_write_policy_from_context(ctx)

        ensure_repository_allowed(owner=owner, repo=repo, config=policy)
        ensure_branch_allowed(branch=head, base_branch=base, config=policy)
        ensure_base_branch_allowed(base_branch=base, config=policy)

        row = self._request_json(
            ctx=ctx,
            path=f"/repos/{owner}/{repo}/pulls",
            method="POST",
            body={
                "title": str(args.get("title") or ""),
                "body": str(args.get("body") or ""),
                "head": head,
                "base": base,
            },
        )
        if not isinstance(row, Mapping):
            raise _protocol_error("github.open_pr expected an object response")
        return {
            "ok": True,
            "data": {
                "number": int(row.get("number") or 0),
                "html_url": str(row.get("html_url") or ""),
                "head": head,
                "base": base,
                "state": str(row.get("state") or ""),
            },
            "source": {"provider_id": self.provider_id},
        }

    def post_pr_review(self, *, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
        owner, repo = _owner_repo(args)
        number = int(args.get("number") or 0)
        event = str(args.get("event") or "").strip().upper()
        policy = github_write_policy_from_context(ctx)

        ensure_repository_allowed(owner=owner, repo=repo, config=policy)
        ensure_merge_allowed(
            requested=event != "COMMENT",
            config=policy,
            event=event,
        )
        head_ref = self._fetch_pr_head_ref(
            ctx=ctx,
            owner=owner,
            repo=repo,
            number=number,
        )
        ensure_pr_head_allowed(head_ref=head_ref, config=policy)

        row = self._request_json(
            ctx=ctx,
            path=f"/repos/{owner}/{repo}/pulls/{number}/reviews",
            method="POST",
            body={"event": event, "body": str(args.get("body") or "")},
        )
        if not isinstance(row, Mapping):
            raise _protocol_error("github.post_pr_review expected an object response")
        return {
            "ok": True,
            "data": {
                "id": row.get("id"),
                "html_url": str(row.get("html_url") or ""),
                "event": event,
                "body": str(row.get("body") or ""),
                "state": str(row.get("state") or ""),
            },
            "source": {"provider_id": self.provider_id},
        }

    def post_pr_comment(self, *, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
        owner, repo = _owner_repo(args)
        number = int(args.get("number") or 0)
        policy = github_write_policy_from_context(ctx)

        ensure_repository_allowed(owner=owner, repo=repo, config=policy)
        ensure_delete_allowed(requested=False, config=policy)
        head_ref = self._fetch_pr_head_ref(
            ctx=ctx,
            owner=owner,
            repo=repo,
            number=number,
        )
        ensure_pr_head_allowed(head_ref=head_ref, config=policy)

        row = self._request_json(
            ctx=ctx,
            path=f"/repos/{owner}/{repo}/issues/{number}/comments",
            method="POST",
            body={"body": str(args.get("body") or "")},
        )
        if not isinstance(row, Mapping):
            raise _protocol_error("github.post_pr_comment expected an object response")
        return {
            "ok": True,
            "data": {
                "id": row.get("id"),
                "html_url": str(row.get("html_url") or ""),
                "body": str(row.get("body") or ""),
            },
            "source": {"provider_id": self.provider_id},
        }

    def healthcheck(self) -> bool:
        return True

    def _branch_exists(
        self,
        *,
        ctx: Any,
        owner: str,
        repo: str,
        branch: str,
    ) -> bool:
        row = self._request_json_or_none_on_404(
            ctx=ctx,
            path=f"/repos/{owner}/{repo}/git/ref/heads/{branch}",
        )
        return isinstance(row, Mapping)

    def _fetch_pr_head_ref(
        self,
        *,
        ctx: Any,
        owner: str,
        repo: str,
        number: int,
    ) -> str:
        """Read the target PR's head ref so the smoke-branch policy can be
        applied structurally before any write call (RWPRS §5.3, §5.4).
        """
        row = self._request_json(
            ctx=ctx,
            path=f"/repos/{owner}/{repo}/pulls/{number}",
        )
        if not isinstance(row, Mapping):
            raise _protocol_error("github PR fetch expected an object response")
        head = row.get("head") if isinstance(row.get("head"), Mapping) else {}
        ref = str(head.get("ref") or "").strip()
        if not ref:
            raise _protocol_error("github PR response missing head.ref")
        return ref

    def _resolve_branch_head_sha(
        self,
        *,
        ctx: Any,
        owner: str,
        repo: str,
        branch: str,
    ) -> str:
        row = self._request_json(
            ctx=ctx,
            path=f"/repos/{owner}/{repo}/git/ref/heads/{branch}",
        )
        return _extract_ref_sha(row)

    def _request_json_or_none_on_404(
        self,
        *,
        ctx: Any,
        path: str,
        query: Mapping[str, str] | None = None,
        method: str = "GET",
        body: Mapping[str, Any] | None = None,
    ) -> Any:
        try:
            return self._request_json(
                ctx=ctx,
                path=path,
                query=query,
                method=method,
                body=body,
            )
        except ToolRuntimeError as exc:
            if exc.code == "REMOTE_ERROR" and exc.details.get("status_code") == 404:
                return None
            raise

    def _request_json(
        self,
        *,
        ctx: Any,
        path: str,
        query: Mapping[str, str] | None = None,
        method: str = "GET",
        body: Mapping[str, Any] | None = None,
    ) -> Any:
        text = self._request_text(
            ctx=ctx,
            path=path,
            query=query,
            method=method,
            body=body,
        )
        if not text.strip():
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ToolRuntimeError(
                "REMOTE_PROTOCOL_ERROR",
                "GitHub REST response was not valid JSON.",
                {"reason_code": "github_response_not_json"},
            ) from exc

    def _request_text(
        self,
        *,
        ctx: Any,
        path: str,
        query: Mapping[str, str] | None = None,
        accept: str = "application/vnd.github+json",
        method: str = "GET",
        body: Mapping[str, Any] | None = None,
    ) -> str:
        token = require_github_pat(context=ctx)
        base_url = get_github_api_base_url(context=ctx).rstrip("/")
        timeout = get_github_timeout_seconds(context=ctx)
        url = f"{base_url}{path}"
        if query:
            url = f"{url}?{urlencode(query)}"
        payload = None
        headers = {
            "Accept": accept,
            "Authorization": f"Bearer {token}",
            "User-Agent": "openminion-github-tools",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(
            url,
            data=payload,
            method=method,
            headers=headers,
        )
        try:
            with urlopen(request, timeout=timeout) as response:  # noqa: S310
                raw = response.read()
        except HTTPError as exc:
            body_excerpt = _read_http_error_body(exc)
            if exc.code in {401, 403}:
                raise auth_invalid_error(
                    status_code=exc.code,
                    body_excerpt=body_excerpt,
                ) from exc
            raise ToolRuntimeError(
                "REMOTE_ERROR",
                "GitHub REST API request failed.",
                {
                    "reason_code": "github_api_error",
                    "status_code": exc.code,
                    "body_excerpt": body_excerpt[:200],
                },
            ) from exc
        except URLError as exc:
            raise ToolRuntimeError(
                "REMOTE_ERROR",
                "GitHub REST API request failed.",
                {
                    "reason_code": "github_api_unreachable",
                    "detail": str(exc.reason),
                },
            ) from exc
        return raw.decode("utf-8", errors="replace")


def _owner_repo(args: Mapping[str, Any]) -> tuple[str, str]:
    return str(args.get("owner") or ""), str(args.get("repo") or "")


def _normalize_pr_summary(raw: Mapping[str, Any]) -> dict[str, Any]:
    head = raw.get("head") if isinstance(raw.get("head"), Mapping) else {}
    base = raw.get("base") if isinstance(raw.get("base"), Mapping) else {}
    user = raw.get("user") if isinstance(raw.get("user"), Mapping) else {}
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


def _normalize_comments(raw: Any, *, kind: str) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    comments: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        user = item.get("user") if isinstance(item.get("user"), Mapping) else {}
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


def _normalize_combined_status(raw: Mapping[str, Any]) -> str:
    state = str(raw.get("state") or "").strip().lower()
    if state == "success":
        return "passing"
    if state in {"failure", "error"}:
        return "failing"
    if state == "pending":
        return "pending"
    return "none"


def _extract_ref_sha(raw: Any) -> str:
    if not isinstance(raw, Mapping):
        raise _protocol_error("github ref response must be an object")
    obj = raw.get("object") if isinstance(raw.get("object"), Mapping) else {}
    sha = str(obj.get("sha") or "").strip()
    if not sha:
        raise _protocol_error("github ref response missing object.sha")
    return sha


def _extract_commit_tree_sha(raw: Any) -> str:
    if not isinstance(raw, Mapping):
        raise _protocol_error("github commit response must be an object")
    tree = raw.get("tree") if isinstance(raw.get("tree"), Mapping) else {}
    sha = str(tree.get("sha") or "").strip()
    if not sha:
        raise _protocol_error("github commit response missing tree.sha")
    return sha


def _protocol_error(message: str) -> ToolRuntimeError:
    return ToolRuntimeError(
        "REMOTE_PROTOCOL_ERROR",
        message,
        {"reason_code": "github_response_shape_invalid"},
    )


def _read_http_error_body(exc: HTTPError) -> str:
    try:
        payload = exc.read()
    except Exception:  # noqa: BLE001
        payload = b""
    if not payload and getattr(exc, "fp", None) is not None:
        try:
            fp = exc.fp
            if isinstance(fp, io.BytesIO):
                fp.seek(0)
            payload = fp.read()
        except Exception:  # noqa: BLE001
            payload = b""
    return payload.decode("utf-8", errors="replace")


__all__ = ["GithubRestProvider"]
