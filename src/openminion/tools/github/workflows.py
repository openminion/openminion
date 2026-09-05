"""Bounded GitHub workflow dispatch and run readback."""

from collections.abc import Mapping
from typing import Any

from openminion.modules.tool.errors import ToolRuntimeError

from .config import profile_config_from_context
from .constants import GITHUB_WORKFLOW_RUNS_MAX_PAGES, GITHUB_WORKFLOW_RUNS_PER_PAGE
from .policy import (
    ensure_repository_allowed,
    ensure_workflow_allowed,
    ensure_workflow_ref_allowed,
)


class GithubWorkflowRestOperations:
    provider_id: str

    def dispatch_workflow(self, *, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
        owner, repo, workflow, ref, request_id = _workflow_identity(args)
        target = str(args.get("target") or "")
        inputs = dict(args.get("inputs") or {})
        policy = profile_config_from_context(ctx)
        ensure_repository_allowed(owner=owner, repo=repo, config=policy)
        ensure_workflow_allowed(
            workflow=workflow,
            ref=ref,
            target=target,
            inputs=inputs,
            config=policy,
        )
        if inputs.get("request_id") != request_id:
            raise ToolRuntimeError(
                "INVALID_REQUEST",
                "Workflow input request_id must match the dispatch request identity.",
                {"reason_code": "github_workflow_request_id_mismatch"},
            )
        reconciled = getattr(ctx, "github_dispatch_workflow_reconciled_result", None)
        if isinstance(reconciled, Mapping):
            return dict(reconciled)
        self._request_json(
            ctx=ctx,
            path=f"/repos/{owner}/{repo}/actions/workflows/{workflow}/dispatches",
            method="POST",
            body={"ref": ref, "inputs": inputs},
        )
        readback = self.list_workflow_runs(args=args, ctx=ctx)
        data = dict(readback["data"])
        data.update({"target": target, "dispatched": True})
        return {"ok": True, "data": data, "source": {"provider_id": self.provider_id}}

    def read_dispatch_workflow(
        self, *, args: Mapping[str, Any], ctx: Any
    ) -> dict[str, Any]:
        return self.list_workflow_runs(args=args, ctx=ctx)

    def list_workflow_runs(
        self, *, args: Mapping[str, Any], ctx: Any
    ) -> dict[str, Any]:
        owner, repo, workflow, ref, request_id = _workflow_identity(args)
        policy = profile_config_from_context(ctx)
        ensure_repository_allowed(owner=owner, repo=repo, config=policy)
        ensure_workflow_ref_allowed(workflow=workflow, ref=ref, config=policy)
        event = str(args.get("event") or "workflow_dispatch")
        limit = int(args.get("limit") or 20)
        matches: list[dict[str, Any]] = []
        truncated = False
        for page in range(1, GITHUB_WORKFLOW_RUNS_MAX_PAGES + 1):
            payload = self._request_json(
                ctx=ctx,
                path=f"/repos/{owner}/{repo}/actions/workflows/{workflow}/runs",
                query={
                    "branch": ref,
                    "event": event,
                    "per_page": str(GITHUB_WORKFLOW_RUNS_PER_PAGE),
                    "page": str(page),
                },
            )
            if not isinstance(payload, Mapping):
                raise _workflow_protocol_error(
                    "workflow-runs response must be an object"
                )
            rows = payload.get("workflow_runs")
            if not isinstance(rows, list) or any(
                not isinstance(row, Mapping) for row in rows
            ):
                raise _workflow_protocol_error(
                    "workflow_runs must be a list of objects"
                )
            for row in rows:
                normalized = _workflow_run(row)
                if (
                    normalized["head_branch"] == ref
                    and normalized["event"] == event
                    and normalized["request_id"] == request_id
                ):
                    matches.append(normalized)
            if len(matches) >= limit:
                truncated = (
                    len(matches) > limit or len(rows) == GITHUB_WORKFLOW_RUNS_PER_PAGE
                )
                break
            if len(rows) < GITHUB_WORKFLOW_RUNS_PER_PAGE:
                break
        else:
            truncated = True
        bounded = matches[:limit]
        return {
            "ok": True,
            "data": {
                "owner": owner,
                "repo": repo,
                "workflow": workflow,
                "ref": ref,
                "request_id": request_id,
                "event": event,
                "runs": bounded,
                "match": "not_found"
                if not bounded
                else ("exact" if len(bounded) == 1 and not truncated else "ambiguous"),
                "truncated": truncated,
            },
            "source": {"provider_id": self.provider_id},
        }

    def _request_json(
        self,
        *,
        ctx: Any,
        path: str,
        query: Mapping[str, str] | None = None,
        method: str = "GET",
        body: Mapping[str, Any] | None = None,
    ) -> Any:
        raise NotImplementedError


def _workflow_identity(args: Mapping[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        str(args.get("owner") or ""),
        str(args.get("repo") or ""),
        str(args.get("workflow") or ""),
        str(args.get("ref") or ""),
        str(args.get("request_id") or ""),
    )


def _workflow_run(row: Mapping[str, Any]) -> dict[str, Any]:
    run_id = row.get("id")
    workflow_id = row.get("workflow_id")
    if not isinstance(run_id, int) or not isinstance(workflow_id, int):
        raise _workflow_protocol_error("workflow run omitted numeric identity")
    request_id = str(row.get("display_title") or row.get("name") or "").strip()
    return {
        "run_id": run_id,
        "workflow_id": workflow_id,
        "request_id": request_id,
        "head_branch": str(row.get("head_branch") or ""),
        "head_sha": str(row.get("head_sha") or ""),
        "event": str(row.get("event") or ""),
        "status": str(row.get("status") or ""),
        "conclusion": str(row.get("conclusion") or ""),
        "html_url": str(row.get("html_url") or ""),
    }


def _workflow_protocol_error(message: str) -> ToolRuntimeError:
    return ToolRuntimeError(
        "INVALID_RESPONSE",
        f"GitHub {message}.",
        {"reason_code": "github_workflow_response_invalid"},
    )


__all__ = ["GithubWorkflowRestOperations"]
