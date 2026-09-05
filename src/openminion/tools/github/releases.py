"""GitHub tag verification and release creation."""

from collections.abc import Mapping
from typing import Any

from openminion.modules.tool.errors import ToolRuntimeError

from .config import profile_config_from_context
from .policy import ensure_repository_allowed


class GithubReleaseRestOperations:
    provider_id: str

    def read_release(self, *, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
        owner, repo, tag = _release_identity(args)
        ensure_repository_allowed(
            owner=owner,
            repo=repo,
            config=profile_config_from_context(ctx),
        )
        tag_sha = self._dereferenced_tag_sha(ctx=ctx, owner=owner, repo=repo, tag=tag)
        release = self._request_json_or_none_on_404(
            ctx=ctx,
            path=f"/repos/{owner}/{repo}/releases/tags/{tag}",
        )
        if release is not None and not isinstance(release, Mapping):
            raise _release_protocol_error("release readback must be an object")
        return _release_result(
            release,
            owner=owner,
            repo=repo,
            tag=tag,
            tag_sha=tag_sha,
            provider_id=self.provider_id,
        )

    def create_release(self, *, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
        owner, repo, tag = _release_identity(args)
        ensure_repository_allowed(
            owner=owner,
            repo=repo,
            config=profile_config_from_context(ctx),
        )
        reconciled = getattr(ctx, "github_create_release_reconciled_result", None)
        if isinstance(reconciled, Mapping):
            return dict(reconciled)
        preflight = getattr(ctx, "github_create_release_preflight", None)
        if not isinstance(preflight, Mapping):
            preflight = self.read_release(args=args, ctx=ctx)
        data = preflight.get("data")
        if not isinstance(data, Mapping):
            raise _release_protocol_error("release preflight omitted data")
        expected_sha = str(args.get("expected_commit_sha") or "")
        if str(data.get("tag_sha") or "") != expected_sha:
            raise ToolRuntimeError(
                "INVALID_REQUEST",
                "The existing tag does not dereference to the expected commit SHA.",
                {"reason_code": "github_release_tag_sha_mismatch"},
            )
        if data.get("release") is not None:
            raise ToolRuntimeError(
                "ALREADY_EXISTS",
                "A GitHub release already exists for this tag.",
                {"reason_code": "github_release_already_exists"},
            )
        row = self._request_json(
            ctx=ctx,
            path=f"/repos/{owner}/{repo}/releases",
            method="POST",
            body={
                "tag_name": tag,
                "name": str(args.get("title") or ""),
                "body": str(args.get("notes") or ""),
                "draft": bool(args.get("draft")),
                "prerelease": bool(args.get("prerelease")),
            },
        )
        if not isinstance(row, Mapping):
            raise _release_protocol_error("create-release response must be an object")
        return _release_result(
            row,
            owner=owner,
            repo=repo,
            tag=tag,
            tag_sha=expected_sha,
            provider_id=self.provider_id,
        )

    def _dereferenced_tag_sha(
        self, *, ctx: Any, owner: str, repo: str, tag: str
    ) -> str:
        ref = self._request_json_or_none_on_404(
            ctx=ctx,
            path=f"/repos/{owner}/{repo}/git/ref/tags/{tag}",
        )
        if ref is None:
            raise ToolRuntimeError(
                "NOT_FOUND",
                "The Git tag does not exist.",
                {"reason_code": "github_release_tag_not_found", "tag": tag},
            )
        if not isinstance(ref, Mapping) or not isinstance(ref.get("object"), Mapping):
            raise _release_protocol_error("tag ref omitted object identity")
        obj = ref["object"]
        object_type = str(obj.get("type") or "")
        sha = str(obj.get("sha") or "")
        if object_type == "commit" and sha:
            return sha
        if object_type != "tag" or not sha:
            raise _release_protocol_error("tag ref has an unsupported object type")
        annotated = self._request_json(
            ctx=ctx,
            path=f"/repos/{owner}/{repo}/git/tags/{sha}",
        )
        if not isinstance(annotated, Mapping) or not isinstance(
            annotated.get("object"), Mapping
        ):
            raise _release_protocol_error("annotated tag omitted target identity")
        target = annotated["object"]
        if str(target.get("type") or "") != "commit" or not str(
            target.get("sha") or ""
        ):
            raise _release_protocol_error("annotated tag does not target a commit")
        return str(target["sha"])

    def _request_json_or_none_on_404(self, *, ctx: Any, path: str) -> Any:
        raise NotImplementedError

    def _request_json(
        self,
        *,
        ctx: Any,
        path: str,
        method: str = "GET",
        body: Mapping[str, Any] | None = None,
    ) -> Any:
        raise NotImplementedError


def _release_identity(args: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        str(args.get("owner") or ""),
        str(args.get("repo") or ""),
        str(args.get("tag") or ""),
    )


def _release_result(
    row: Mapping[str, Any] | None,
    *,
    owner: str,
    repo: str,
    tag: str,
    tag_sha: str,
    provider_id: str,
) -> dict[str, Any]:
    release = None
    if row is not None:
        release_id = row.get("id")
        if not isinstance(release_id, int):
            raise _release_protocol_error("release omitted numeric identity")
        release = {
            "release_id": release_id,
            "tag": str(row.get("tag_name") or ""),
            "title": str(row.get("name") or ""),
            "notes": str(row.get("body") or ""),
            "draft": bool(row.get("draft")),
            "prerelease": bool(row.get("prerelease")),
            "html_url": str(row.get("html_url") or ""),
        }
    return {
        "ok": True,
        "data": {
            "owner": owner,
            "repo": repo,
            "tag": tag,
            "tag_sha": tag_sha,
            "release": release,
        },
        "source": {"provider_id": provider_id},
    }


def _release_protocol_error(message: str) -> ToolRuntimeError:
    return ToolRuntimeError(
        "INVALID_RESPONSE",
        f"GitHub {message}.",
        {"reason_code": "github_release_response_invalid"},
    )


__all__ = ["GithubReleaseRestOperations"]
