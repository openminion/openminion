import io
import json
from collections.abc import Mapping
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from openminion.modules.tool.errors import ToolRuntimeError

from .auth import auth_invalid_error, require_github_pat
from .config import profile_config_from_context
from .constants import (
    DEFAULT_GITHUB_DIFF_MAX_LINES,
    DEFAULT_GITHUB_PROVIDER_ID,
    GITHUB_CHECK_FAILURE_LIMIT,
    GITHUB_CHECK_OUTPUT_MAX_CHARS,
    GITHUB_CHECK_RUNS_MAX_PAGES,
    GITHUB_CHECK_RUNS_PER_PAGE,
)
from .policy import (
    ensure_base_branch_allowed,
    ensure_branch_allowed,
    ensure_force_push_allowed,
    ensure_merge_allowed,
    ensure_paths_allowed,
    ensure_pr_head_allowed,
    ensure_repository_allowed,
)
from .env import get_github_api_base_url, get_github_timeout_seconds
from .pull_requests import (
    merge_pr_readback_result,
    merge_pr_result,
    normalize_comments,
    normalize_pr_summary,
    open_pr_result,
    require_merge_checks,
    require_merge_pr_ready,
    update_pr_result,
)
from .releases import GithubReleaseRestOperations
from .workflows import GithubWorkflowRestOperations


class GithubRestProvider(GithubWorkflowRestOperations, GithubReleaseRestOperations):
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
                "open_prs": [normalize_pr_summary(item) for item in rows[:limit]],
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
            "data": {"pull_request": normalize_pr_summary(row)},
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
        return {
            "ok": True,
            "data": {
                "diff": "\n".join(lines[:max_lines]),
                "truncated": len(lines) > max_lines,
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
        comments = normalize_comments(issue_comments, kind="issue")
        comments.extend(normalize_comments(review_comments, kind="review"))
        return {
            "ok": True,
            "data": {"comments": comments[:limit]},
            "source": {"provider_id": self.provider_id},
        }

    def fetch_checks(self, *, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
        owner, repo = _owner_repo(args)
        head_sha = str(args.get("head_sha") or "").strip()
        expected_checks = list(args.get("expected_checks") or [])
        combined = self._request_json(
            ctx=ctx,
            path=f"/repos/{owner}/{repo}/commits/{head_sha}/status",
        )
        if not isinstance(combined, Mapping):
            raise _protocol_error("github.fetch_checks expected an object response")
        statuses = combined.get("statuses") or []
        if not isinstance(statuses, list):
            raise _protocol_error("github.fetch_checks statuses must be a list")

        check_runs, check_runs_truncated = self._fetch_check_runs(
            ctx=ctx,
            owner=owner,
            repo=repo,
            head_sha=head_sha,
        )
        normalized_runs = [_normalize_check_run(row) for row in check_runs]
        failure_facts = [
            _check_failure_fact(run, expected_checks=expected_checks)
            for run in normalized_runs
            if _check_run_result(run) == "failure"
        ]
        missing_expected = [
            name
            for name in expected_checks
            if not any(run["name"] == name for run in normalized_runs)
        ]
        return {
            "ok": True,
            "data": {
                "head_sha": head_sha,
                "checks_status": _normalize_combined_status(combined),
                "state": str(combined.get("state") or "none"),
                "statuses": statuses,
                "overall_result": _overall_check_result(
                    combined=combined,
                    expected_checks=expected_checks,
                    check_runs=normalized_runs,
                    missing_expected=missing_expected,
                ),
                "expected_checks": expected_checks,
                "missing_expected_checks": missing_expected,
                "check_runs": [
                    {
                        "name": run["name"],
                        "status": run["status"],
                        "conclusion": run["conclusion"],
                        "url": run["url"],
                    }
                    for run in normalized_runs
                ],
                "check_runs_truncated": check_runs_truncated,
                "failure_facts": failure_facts[:GITHUB_CHECK_FAILURE_LIMIT],
                "failure_facts_truncated": len(failure_facts)
                > GITHUB_CHECK_FAILURE_LIMIT,
            },
            "source": {"provider_id": self.provider_id},
        }

    def _fetch_check_runs(
        self,
        *,
        ctx: Any,
        owner: str,
        repo: str,
        head_sha: str,
    ) -> tuple[list[Mapping[str, Any]], bool]:
        check_runs: list[Mapping[str, Any]] = []
        total_count: int | None = None
        last_page_size = 0
        for page in range(1, GITHUB_CHECK_RUNS_MAX_PAGES + 1):
            payload = self._request_json(
                ctx=ctx,
                path=f"/repos/{owner}/{repo}/commits/{head_sha}/check-runs",
                query={
                    "per_page": str(GITHUB_CHECK_RUNS_PER_PAGE),
                    "page": str(page),
                },
            )
            if not isinstance(payload, Mapping):
                raise _protocol_error(
                    "github.fetch_checks check-runs expected an object response"
                )
            rows = payload.get("check_runs")
            if not isinstance(rows, list) or any(
                not isinstance(row, Mapping) for row in rows
            ):
                raise _protocol_error(
                    "github.fetch_checks check_runs must be a list of objects"
                )
            raw_total = payload.get("total_count")
            if raw_total is not None:
                if not isinstance(raw_total, int) or raw_total < 0:
                    raise _protocol_error(
                        "github.fetch_checks total_count must be a non-negative integer"
                    )
                total_count = raw_total
            check_runs.extend(rows)
            last_page_size = len(rows)
            if last_page_size < GITHUB_CHECK_RUNS_PER_PAGE:
                return check_runs, False
            if total_count is not None and len(check_runs) >= total_count:
                return check_runs, False
        return check_runs, bool(
            last_page_size == GITHUB_CHECK_RUNS_PER_PAGE
            and (total_count is None or len(check_runs) < total_count)
        )

    def commit_files(self, *, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
        owner, repo = _owner_repo(args)
        branch = str(args.get("branch") or "").strip()
        base_branch = str(args.get("base_branch") or "").strip()
        message = str(args.get("message") or "").strip()
        files = list(args.get("files") or [])
        force = bool(args.get("force", False))
        policy = profile_config_from_context(ctx)

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
        self._ensure_open_pr_allowed(
            ctx=ctx,
            owner=owner,
            repo=repo,
            head=head,
            base=base,
        )

        reconciled = getattr(ctx, "github_open_pr_reconciled_result", None)
        if isinstance(reconciled, Mapping):
            return dict(reconciled)

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
        return open_pr_result(
            row,
            owner=owner,
            repo=repo,
            head=head,
            base=base,
            head_sha=str(getattr(ctx, "github_open_pr_head_sha", "") or ""),
            provider_id=self.provider_id,
        )

    def resolve_open_pr_head_sha(self, *, args: Mapping[str, Any], ctx: Any) -> str:
        owner, repo = _owner_repo(args)
        head = str(args.get("head") or "").strip()
        base = str(args.get("base") or "").strip()
        self._ensure_open_pr_allowed(
            ctx=ctx,
            owner=owner,
            repo=repo,
            head=head,
            base=base,
        )
        return self._resolve_branch_head_sha(
            ctx=ctx,
            owner=owner,
            repo=repo,
            branch=head,
        )

    def find_open_pr(
        self,
        *,
        args: Mapping[str, Any],
        ctx: Any,
        head_sha: str,
    ) -> dict[str, Any] | None:
        owner, repo = _owner_repo(args)
        head = str(args.get("head") or "").strip()
        base = str(args.get("base") or "").strip()
        self._ensure_open_pr_allowed(
            ctx=ctx,
            owner=owner,
            repo=repo,
            head=head,
            base=base,
        )
        rows = self._request_json(
            ctx=ctx,
            path=f"/repos/{owner}/{repo}/pulls",
            query={
                "state": "all",
                "head": f"{owner}:{head}",
                "base": base,
                "per_page": "100",
            },
        )
        if not isinstance(rows, list):
            raise _protocol_error("github.open_pr readback expected a list response")
        for row in rows:
            if not isinstance(row, Mapping):
                raise _protocol_error(
                    "github.open_pr readback expected object responses"
                )
            row_head = row.get("head")
            row_base = row.get("base")
            if not isinstance(row_head, Mapping) or not isinstance(row_base, Mapping):
                raise _protocol_error("github.open_pr readback omitted branch facts")
            if (
                str(row_head.get("ref") or "") == head
                and str(row_head.get("sha") or "") == head_sha
                and str(row_base.get("ref") or "") == base
            ):
                return open_pr_result(
                    row,
                    owner=owner,
                    repo=repo,
                    head=head,
                    base=base,
                    head_sha=head_sha,
                    provider_id=self.provider_id,
                )
        return None

    def read_update_pr(self, *, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
        owner, repo = _owner_repo(args)
        number = int(args.get("number") or 0)
        ensure_repository_allowed(
            owner=owner,
            repo=repo,
            config=profile_config_from_context(ctx),
        )
        row = self._request_json_or_none_on_404(
            ctx=ctx,
            path=f"/repos/{owner}/{repo}/pulls/{number}",
        )
        if row is None:
            raise ToolRuntimeError(
                "NOT_FOUND",
                "The pull request does not exist.",
                {"reason_code": "github_update_pr_not_found"},
            )
        if not isinstance(row, Mapping):
            raise _protocol_error(
                "github.update_pr readback expected an object response"
            )
        state = str(row.get("state") or "")
        if state != "open":
            raise ToolRuntimeError(
                "INVALID_REQUEST",
                "Only an open pull request can be updated.",
                {"reason_code": "github_update_pr_not_open", "state": state},
            )
        return update_pr_result(
            row,
            owner=owner,
            repo=repo,
            provider_id=self.provider_id,
        )

    def update_pr(self, *, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
        owner, repo = _owner_repo(args)
        number = int(args.get("number") or 0)
        reconciled = getattr(ctx, "github_update_pr_reconciled_result", None)
        if isinstance(reconciled, Mapping):
            return dict(reconciled)

        if not isinstance(getattr(ctx, "github_update_pr_preflight", None), Mapping):
            self.read_update_pr(args=args, ctx=ctx)
        body = {
            field: args[field]
            for field in ("title", "body")
            if args.get(field) is not None
        }
        row = self._request_json(
            ctx=ctx,
            path=f"/repos/{owner}/{repo}/pulls/{number}",
            method="PATCH",
            body=body,
        )
        if not isinstance(row, Mapping):
            raise _protocol_error("github.update_pr expected an object response")
        return update_pr_result(
            row,
            owner=owner,
            repo=repo,
            provider_id=self.provider_id,
        )

    def read_merge_pr(self, *, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
        owner, repo = _owner_repo(args)
        number = int(args.get("number") or 0)
        policy = profile_config_from_context(ctx)
        ensure_repository_allowed(owner=owner, repo=repo, config=policy)
        ensure_merge_allowed(requested=True, config=policy)
        row = self._request_json_or_none_on_404(
            ctx=ctx,
            path=f"/repos/{owner}/{repo}/pulls/{number}",
        )
        if row is None:
            raise ToolRuntimeError(
                "NOT_FOUND",
                "The pull request does not exist.",
                {"reason_code": "github_merge_pr_not_found"},
            )
        if not isinstance(row, Mapping):
            raise _protocol_error("github.merge_pr readback expected an object")
        return merge_pr_readback_result(
            row,
            owner=owner,
            repo=repo,
            provider_id=self.provider_id,
        )

    def merge_pr(self, *, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
        owner, repo = _owner_repo(args)
        number = int(args.get("number") or 0)
        expected_head_sha = str(args.get("expected_head_sha") or "")
        expected_checks = list(args.get("expected_checks") or [])
        merge_method = str(args.get("merge_method") or "")
        policy = profile_config_from_context(ctx)
        ensure_repository_allowed(owner=owner, repo=repo, config=policy)
        ensure_merge_allowed(requested=True, config=policy)
        reconciled = getattr(ctx, "github_merge_pr_reconciled_result", None)
        if isinstance(reconciled, Mapping):
            return dict(reconciled)

        preflight = getattr(ctx, "github_merge_pr_preflight", None)
        if not isinstance(preflight, Mapping):
            preflight = self.read_merge_pr(args=args, ctx=ctx)
        require_merge_pr_ready(
            preflight,
            expected_owner=owner,
            expected_repo=repo,
            expected_number=number,
            expected_head_sha=expected_head_sha,
        )
        checks = getattr(ctx, "github_merge_pr_checks", None)
        if not isinstance(checks, Mapping):
            checks = self.fetch_checks(
                args={
                    "owner": owner,
                    "repo": repo,
                    "head_sha": expected_head_sha,
                    "expected_checks": expected_checks,
                },
                ctx=ctx,
            )
        require_merge_checks(
            checks,
            expected_head_sha=expected_head_sha,
            expected_checks=expected_checks,
        )
        row = self._request_json(
            ctx=ctx,
            path=f"/repos/{owner}/{repo}/pulls/{number}/merge",
            method="PUT",
            body={"sha": expected_head_sha, "merge_method": merge_method},
        )
        if not isinstance(row, Mapping):
            raise _protocol_error("github.merge_pr expected an object response")
        if row.get("merged") is not True:
            raise ToolRuntimeError(
                "UPSTREAM_ERROR",
                "GitHub did not merge the pull request.",
                {
                    "reason_code": "github_merge_pr_conflict",
                    "provider_message": str(row.get("message") or "")[:200],
                },
            )
        if not str(row.get("sha") or ""):
            raise _protocol_error(
                "github.merge_pr response omitted the merge commit SHA"
            )
        return merge_pr_result(
            row,
            owner=owner,
            repo=repo,
            number=number,
            head_sha=expected_head_sha,
            merge_method=merge_method,
            provider_id=self.provider_id,
        )

    @staticmethod
    def _ensure_open_pr_allowed(
        *,
        ctx: Any,
        owner: str,
        repo: str,
        head: str,
        base: str,
    ) -> None:
        policy = profile_config_from_context(ctx)
        ensure_repository_allowed(owner=owner, repo=repo, config=policy)
        ensure_branch_allowed(branch=head, base_branch=base, config=policy)
        ensure_base_branch_allowed(base_branch=base, config=policy)

    def post_pr_review(self, *, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
        owner, repo = _owner_repo(args)
        number = int(args.get("number") or 0)
        event = str(args.get("event") or "").strip().upper()
        policy = profile_config_from_context(ctx)

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
        policy = profile_config_from_context(ctx)

        ensure_repository_allowed(owner=owner, repo=repo, config=policy)
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
        row = self._request_json(
            ctx=ctx,
            path=f"/repos/{owner}/{repo}/pulls/{number}",
        )
        if not isinstance(row, Mapping):
            raise _protocol_error("github PR fetch expected an object response")
        head = _mapping(row.get("head"))
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
            if exc.code == "UPSTREAM_ERROR" and exc.details.get("status_code") == 404:
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
                "INVALID_RESPONSE",
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
                "UPSTREAM_ERROR",
                "GitHub REST API request failed.",
                {
                    "reason_code": "github_api_error",
                    "status_code": exc.code,
                    "body_excerpt": body_excerpt[:200],
                },
            ) from exc
        except URLError as exc:
            raise ToolRuntimeError(
                "UPSTREAM_ERROR",
                "GitHub REST API request failed.",
                {
                    "reason_code": "github_api_unreachable",
                    "detail": str(exc.reason),
                },
            ) from exc
        return bytes(raw).decode("utf-8", errors="replace")


def _owner_repo(args: Mapping[str, Any]) -> tuple[str, str]:
    return str(args.get("owner") or ""), str(args.get("repo") or "")


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _normalize_combined_status(raw: Mapping[str, Any]) -> str:
    state = str(raw.get("state") or "").strip().lower()
    if state == "success":
        return "passing"
    if state in {"failure", "error"}:
        return "failing"
    if state == "pending":
        return "pending"
    return "none"


def _normalize_check_run(raw: Mapping[str, Any]) -> dict[str, Any]:
    name = str(raw.get("name") or "").strip()
    status = str(raw.get("status") or "").strip().lower()
    conclusion = str(raw.get("conclusion") or "").strip().lower()
    if not name or not status or (status == "completed" and not conclusion):
        raise _protocol_error("github.fetch_checks received a malformed check run")
    output = _mapping(raw.get("output"))
    return {
        "name": name,
        "status": status,
        "conclusion": conclusion or None,
        "url": str(raw.get("details_url") or raw.get("html_url") or ""),
        "output": {
            "title": _bounded_check_output(output.get("title")),
            "summary": _bounded_check_output(output.get("summary")),
            "text": _bounded_check_output(output.get("text")),
        },
    }


def _check_run_result(run: Mapping[str, Any]) -> str:
    if run["status"] != "completed":
        return "pending"
    return "success" if run["conclusion"] == "success" else "failure"


def _check_failure_fact(
    run: Mapping[str, Any], *, expected_checks: list[str]
) -> dict[str, Any]:
    output = run["output"]
    return {
        "name": run["name"],
        "conclusion": run["conclusion"],
        "url": run["url"],
        "output_title": output["title"],
        "output_summary": output["summary"],
        "output_text": output["text"],
        "expected": run["name"] in expected_checks,
    }


def _bounded_check_output(value: Any) -> str:
    return str(value or "")[:GITHUB_CHECK_OUTPUT_MAX_CHARS]


def _overall_check_result(
    *,
    combined: Mapping[str, Any],
    expected_checks: list[str],
    check_runs: list[dict[str, Any]],
    missing_expected: list[str],
) -> str:
    combined_result = _combined_result(combined)
    if not expected_checks:
        observed_results = [_check_run_result(run) for run in check_runs]
        if "failure" in observed_results or combined_result == "failure":
            return "failure"
        if "pending" in observed_results or combined_result == "pending":
            return "pending"
        return "success" if observed_results else combined_result

    expected_results = [
        _check_run_result(run) for run in check_runs if run["name"] in expected_checks
    ]
    if "failure" in expected_results or combined_result == "failure":
        return "failure"
    if missing_expected or "pending" in expected_results:
        return "pending"
    if combined.get("statuses") and combined_result == "pending":
        return "pending"
    return "success"


def _combined_result(raw: Mapping[str, Any]) -> str:
    state = str(raw.get("state") or "").strip().lower()
    if state == "success":
        return "success"
    if state in {"failure", "error"}:
        return "failure"
    if state == "pending":
        return "pending"
    return "none"


def _extract_ref_sha(raw: Any) -> str:
    if not isinstance(raw, Mapping):
        raise _protocol_error("github ref response must be an object")
    obj = _mapping(raw.get("object"))
    sha = str(obj.get("sha") or "").strip()
    if not sha:
        raise _protocol_error("github ref response missing object.sha")
    return sha


def _extract_commit_tree_sha(raw: Any) -> str:
    if not isinstance(raw, Mapping):
        raise _protocol_error("github commit response must be an object")
    tree = _mapping(raw.get("tree"))
    sha = str(tree.get("sha") or "").strip()
    if not sha:
        raise _protocol_error("github commit response missing tree.sha")
    return sha


def _protocol_error(message: str) -> ToolRuntimeError:
    return ToolRuntimeError(
        "INVALID_RESPONSE",
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
