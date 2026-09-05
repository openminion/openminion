import subprocess
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from openminion.modules.tool.errors import ToolRuntimeError
from openminion.tools.git.errors import (
    GIT_AUTH_FAILED,
    GIT_BINARY_ERROR,
    GIT_NON_FAST_FORWARD,
    GIT_REMOTE_OUTCOME_UNCERTAIN,
)
from openminion.tools.git.runtime import (
    GitCommandResult,
    classify_git_failure,
    require_configured_git_remote,
    resolve_git_ref_oid,
    resolve_git_remote_ref_oid,
    resolve_git_repo_root,
    run_git,
)


def _schema_git_token(value: str, *, field: str, full_ref: bool = False) -> str:
    token = value.strip()
    if not token or token[0] in {"-", "+"} or ":" in token:
        raise ValueError(f"invalid {field}")
    if full_ref and not token.startswith("refs/"):
        raise ValueError(f"{field} must be a fully-qualified git ref")
    return token


class _StrictRemoteModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GitFetchArgs(_StrictRemoteModel):
    remote: str = Field(
        ..., min_length=1, description="Configured remote name to fetch from."
    )
    ref: str = Field(
        ...,
        min_length=1,
        description="Explicit remote ref to fetch, such as refs/heads/main.",
    )

    @field_validator("remote")
    @classmethod
    def _remote_is_configured_name(cls, value: str) -> str:
        return _schema_git_token(value, field="remote")

    @field_validator("ref")
    @classmethod
    def _ref_is_explicit(cls, value: str) -> str:
        return _schema_git_token(value, field="ref", full_ref=True)


class GitPushArgs(_StrictRemoteModel):
    remote: str = Field(
        ..., min_length=1, description="Configured remote name to push to."
    )
    source_ref: str = Field(
        ..., min_length=1, description="Explicit local source ref or commit."
    )
    target_ref: str = Field(
        ...,
        min_length=1,
        description="Explicit fully-qualified remote target ref.",
    )

    @field_validator("remote")
    @classmethod
    def _remote_is_configured_name(cls, value: str) -> str:
        return _schema_git_token(value, field="remote")

    @field_validator("source_ref")
    @classmethod
    def _source_ref_is_explicit(cls, value: str) -> str:
        return _schema_git_token(value, field="source_ref")

    @field_validator("target_ref")
    @classmethod
    def _target_ref_is_explicit(cls, value: str) -> str:
        return _schema_git_token(value, field="target_ref", full_ref=True)


class GitTagArgs(_StrictRemoteModel):
    action: Literal["list", "create", "push"]
    name: str | None = Field(
        default=None,
        description="Tag name. Required for create and push; optional for list.",
    )
    target_ref: str | None = Field(
        default=None,
        description="Explicit target ref for annotated tag creation.",
    )
    message: str | None = Field(
        default=None,
        description="Annotation message for tag creation.",
    )
    remote: str | None = Field(
        default=None,
        description="Configured remote name for tag publication.",
    )

    @field_validator("name", "target_ref", "message", "remote")
    @classmethod
    def _optional_values_are_trimmed(cls, value: str | None) -> str | None:
        if value is None:
            return None
        token = value.strip()
        if not token:
            raise ValueError("empty optional arguments must be omitted")
        return token

    @model_validator(mode="after")
    def _validate_action_arguments(self) -> "GitTagArgs":
        if self.name:
            self.name = _schema_git_token(self.name, field="name")
        if self.target_ref:
            self.target_ref = _schema_git_token(self.target_ref, field="target_ref")
        if self.remote:
            self.remote = _schema_git_token(self.remote, field="remote")
        if self.action == "list":
            if self.target_ref or self.message or self.remote:
                raise ValueError("list accepts only the optional name argument")
            return self
        if self.action == "create":
            if not self.name or not self.target_ref or not self.message:
                raise ValueError("create requires name, target_ref, and message")
            if self.remote:
                raise ValueError("create does not accept remote")
            return self
        if not self.name or not self.remote:
            raise ValueError("push requires name and remote")
        if self.target_ref or self.message:
            raise ValueError("push does not accept target_ref or message")
        return self


def _validate_full_ref(cwd: str, ref: str, *, field: str) -> None:
    result = run_git(("check-ref-format", ref), cwd=cwd)
    if result.exit_code != 0 or not ref.startswith("refs/"):
        raise ToolRuntimeError(
            "INVALID_ARGUMENT",
            f"{field} must be a fully-qualified git ref",
            {"field": field, "value": ref},
        )


def _remote_failure(result: GitCommandResult) -> ToolRuntimeError:
    failure = classify_git_failure(result)
    if failure.code in {GIT_AUTH_FAILED, GIT_NON_FAST_FORWARD}:
        return failure
    if failure.code != GIT_BINARY_ERROR:
        return failure
    return ToolRuntimeError(
        GIT_REMOTE_OUTCOME_UNCERTAIN,
        "git remote update outcome is uncertain",
        {**failure.details, "reason_code": "git_remote_outcome_uncertain"},
    )


def _remote_workspace(ctx: Any) -> str:
    return str(resolve_git_repo_root(ctx))


def _remote_token(value: Any, *, field: str) -> str:
    token = str(value or "").strip()
    if not token:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT", f"{field} is required", {"field": field}
        )
    if token[0] in {"-", "+"} or ":" in token:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT",
            f"invalid {field}",
            {"field": field, "value": token},
        )
    return token


def _remote_result(
    *,
    result: GitCommandResult,
    parsed: Any,
) -> dict[str, Any]:
    return {
        "command": list(result.command[1:]),
        "exit_code": result.exit_code,
        "parsed": parsed,
        "raw_stdout": result.stdout,
        "raw_stderr": result.stderr,
    }


def _require_remote_success(result: GitCommandResult) -> None:
    if result.exit_code != 0:
        raise classify_git_failure(result)


def _uncertain_remote_error(*, cwd: str, remote: str) -> ToolRuntimeError:
    return ToolRuntimeError(
        GIT_REMOTE_OUTCOME_UNCERTAIN,
        "git remote update outcome is uncertain",
        {
            "reason_code": "git_remote_outcome_uncertain",
            "cwd": cwd,
            "remote": remote,
        },
    )


def _h_fetch(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    cwd = _remote_workspace(ctx)
    remote = _remote_token(args.get("remote"), field="remote")
    ref = _remote_token(args.get("ref"), field="ref")
    _validate_full_ref(cwd, ref, field="ref")
    require_configured_git_remote(cwd, remote)
    result = run_git(("fetch", "--", remote, ref), cwd=cwd)
    _require_remote_success(result)
    return _remote_result(
        result=result,
        parsed={
            "remote": remote,
            "ref": ref,
            "fetched_oid": resolve_git_ref_oid(cwd, "FETCH_HEAD"),
        },
    )


def _h_push(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    reconciled = getattr(ctx, "git_remote_reconciled_result", None)
    if isinstance(reconciled, dict):
        return dict(reconciled)

    cwd = _remote_workspace(ctx)
    remote = _remote_token(args.get("remote"), field="remote")
    source_ref = _remote_token(args.get("source_ref"), field="source_ref")
    target_ref = _remote_token(args.get("target_ref"), field="target_ref")
    _validate_full_ref(cwd, target_ref, field="target_ref")
    require_configured_git_remote(cwd, remote)
    source_oid = resolve_git_ref_oid(cwd, source_ref)
    before_oid = resolve_git_remote_ref_oid(cwd, remote=remote, ref=target_ref)
    expected_before = getattr(ctx, "git_remote_expected_before_oid", before_oid)
    if expected_before != before_oid:
        raise ToolRuntimeError(
            "INVALID_REQUEST",
            "remote ref changed after project approval",
            {
                "reason_code": "git_remote_precondition_changed",
                "remote": remote,
                "target_ref": target_ref,
            },
        )

    try:
        result = run_git(
            ("push", "--porcelain", "--", remote, f"{source_ref}:{target_ref}"),
            cwd=cwd,
        )
    except subprocess.TimeoutExpired as exc:
        raise _uncertain_remote_error(cwd=cwd, remote=remote) from exc
    if result.exit_code != 0:
        raise _remote_failure(result)
    try:
        remote_oid = resolve_git_remote_ref_oid(cwd, remote=remote, ref=target_ref)
    except ToolRuntimeError as exc:
        raise _uncertain_remote_error(cwd=cwd, remote=remote) from exc
    if remote_oid != source_oid:
        raise _uncertain_remote_error(cwd=cwd, remote=remote)
    return _remote_result(
        result=result,
        parsed={
            "action": "push",
            "repository": cwd,
            "remote": remote,
            "source_ref": source_ref,
            "source_oid": source_oid,
            "target_ref": target_ref,
            "previous_remote_oid": before_oid,
            "remote_oid": remote_oid,
        },
    )


def _list_tags(cwd: str, name: str) -> dict[str, Any]:
    ref = f"refs/tags/{_remote_token(name, field='name')}" if name else "refs/tags"
    result = run_git(
        (
            "for-each-ref",
            "--format=%(refname:short)%09%(objectname)%09%(objecttype)%09%(*objectname)%09%(subject)",
            ref,
        ),
        cwd=cwd,
    )
    _require_remote_success(result)
    tags = []
    for line in result.stdout.splitlines():
        tag_name, oid, object_type, target_oid, message = line.split("\t", 4)
        tags.append(
            {
                "name": tag_name,
                "oid": oid,
                "annotated": object_type == "tag",
                "target_oid": target_oid or oid,
                "message": message,
            }
        )
    return _remote_result(result=result, parsed={"action": "list", "tags": tags})


def _create_tag(cwd: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    tag_name = _remote_token(name, field="name")
    tag_ref = f"refs/tags/{tag_name}"
    _validate_full_ref(cwd, tag_ref, field="name")
    target_ref = _remote_token(args.get("target_ref"), field="target_ref")
    message = str(args.get("message") or "").strip()
    result = run_git(
        ("tag", "-a", "-m", message, "--", tag_name, target_ref),
        cwd=cwd,
    )
    _require_remote_success(result)
    return _remote_result(
        result=result,
        parsed={
            "action": "create",
            "name": tag_name,
            "oid": resolve_git_ref_oid(cwd, tag_ref),
            "target_oid": resolve_git_ref_oid(cwd, f"{tag_ref}^{{}}"),
        },
    )


def _h_tag(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    action = str(args.get("action") or "")
    cwd = _remote_workspace(ctx)
    name = str(args.get("name") or "").strip()
    if action == "list":
        return _list_tags(cwd, name)
    if action == "create":
        return _create_tag(cwd, name, args)

    tag_name = _remote_token(name, field="name")
    tag_ref = f"refs/tags/{tag_name}"
    _validate_full_ref(cwd, tag_ref, field="name")
    reconciled = getattr(ctx, "git_remote_reconciled_result", None)
    if isinstance(reconciled, dict):
        return dict(reconciled)
    remote = _remote_token(args.get("remote"), field="remote")
    require_configured_git_remote(cwd, remote)
    tag_oid = resolve_git_ref_oid(cwd, tag_ref)
    target_oid = resolve_git_ref_oid(cwd, f"{tag_ref}^{{}}")
    before_oid = resolve_git_remote_ref_oid(cwd, remote=remote, ref=tag_ref)
    expected_before = getattr(ctx, "git_remote_expected_before_oid", before_oid)
    if expected_before != before_oid:
        raise ToolRuntimeError(
            "INVALID_REQUEST",
            "remote tag changed after project approval",
            {
                "reason_code": "git_remote_precondition_changed",
                "remote": remote,
                "tag_ref": tag_ref,
            },
        )
    try:
        result = run_git(
            ("push", "--porcelain", "--", remote, f"{tag_ref}:{tag_ref}"),
            cwd=cwd,
        )
    except subprocess.TimeoutExpired as exc:
        raise _uncertain_remote_error(cwd=cwd, remote=remote) from exc
    if result.exit_code != 0:
        raise _remote_failure(result)
    try:
        remote_oid = resolve_git_remote_ref_oid(cwd, remote=remote, ref=tag_ref)
        remote_target_oid = resolve_git_remote_ref_oid(
            cwd,
            remote=remote,
            ref=f"{tag_ref}^{{}}",
        )
    except ToolRuntimeError as exc:
        raise _uncertain_remote_error(cwd=cwd, remote=remote) from exc
    if remote_oid != tag_oid or remote_target_oid != target_oid:
        raise _uncertain_remote_error(cwd=cwd, remote=remote)
    return _remote_result(
        result=result,
        parsed={
            "action": "push",
            "repository": cwd,
            "remote": remote,
            "name": tag_name,
            "tag_ref": tag_ref,
            "tag_oid": tag_oid,
            "target_oid": target_oid,
            "previous_remote_oid": before_oid,
            "remote_oid": remote_oid,
            "remote_target_oid": remote_target_oid,
        },
    )
