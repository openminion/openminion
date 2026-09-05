"""Worktree isolation bridge for code-bearing orchestrate children."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from contextlib import contextmanager
import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, cast

from openminion.modules.brain.execution.child_tasks import SubtaskSpec
from openminion.modules.brain.execution.loop_contracts import ExecutionContext
from openminion.modules.brain.loop.services import runner_from_context
from openminion.modules.brain.loop.rollouts import WorktreeIsolator
from openminion.modules.brain.loop.strategies.coding.verification import (
    CODING_VERIFIER_VERDICT_COMPLETE,
)
from openminion.modules.brain.schemas import WorkingState

_MODULE_STATE_KEY = "worktree_children"
_CHILD_STATE_KEY = "worktree_child"


@dataclass(slots=True)
class ChildWorktreeLease:
    isolator: WorktreeIsolator
    worktree: Path
    subtask_id: str
    base_revision: str


_MISSING = object()


def child_verifier_evidence(result: Any) -> dict[str, Any]:
    """Return verification evidence issued by the existing coding owner."""
    outputs = getattr(getattr(result, "action_result", None), "outputs", None)
    if not isinstance(outputs, dict):
        return {}
    verdict = str(outputs.get("coding.verifier_verdict") or "").strip()
    goal_id = str(outputs.get("coding.verifier_goal_id") or "").strip()
    result_count = int(outputs.get("coding.verifier_result_count") or 0)
    if (
        verdict != CODING_VERIFIER_VERDICT_COMPLETE
        or not goal_id
        or result_count < 1
    ):
        return {}
    return {"passed": True, "verifier_refs": [f"coding-verifier:{goal_id}"]}


def _module_bucket(state: WorkingState) -> dict[str, Any]:
    module_state = state.module_state
    bucket = module_state.get(_MODULE_STATE_KEY)
    if not isinstance(bucket, dict):
        bucket = {"version": 1, "children": [], "conflicts": []}
        module_state[_MODULE_STATE_KEY] = bucket
    bucket.setdefault("children", [])
    bucket.setdefault("conflicts", [])
    return bucket


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _git_input(
    repo: Path, *args: str, input_text: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
        input=input_text,
        timeout=30,
    )


def _status_paths(worktree: Path) -> list[str]:
    result = _git(worktree, "status", "--short")
    paths: list[str] = []
    for line in result.stdout.splitlines():
        path = line[3:].strip()
        if path:
            paths.append(path)
    return sorted(dict.fromkeys(paths))


def _diff_text(worktree: Path) -> str:
    result = _git(worktree, "diff", "--")
    return result.stdout if result.returncode == 0 else ""


def _revision_diff(worktree: Path, base_revision: str, child_revision: str) -> str:
    result = _git(
        worktree,
        "diff",
        "--binary",
        base_revision,
        child_revision,
        "--",
    )
    return result.stdout if result.returncode == 0 else ""


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def save_child_worktree_record(
    artifactctl: Any,
    record: dict[str, Any],
) -> str:
    """Persist the current child record through the existing artifact owner."""
    owner_id = str(record.get("artifact", {}).get("owner_id") or "").strip()
    if not owner_id:
        raise ValueError("child artifact record requires its artifact owner")
    record_alias = f"a2a-child:{owner_id}"
    record["record_alias"] = record_alias
    ref = artifactctl.ingest_bytes(
        json.dumps(record, ensure_ascii=True, sort_keys=True).encode("utf-8"),
        mime="application/json",
        original_name="child-worktree-record.json",
        label=f"maer-child-record:{record.get('subtask_id', '')}",
        meta={
            "owner_type": "a2a",
            "owner_id": owner_id,
            "target_digest": str(record.get("target_digest") or ""),
            "integration_status": str(record.get("integration_status") or ""),
        },
        session_id=str(record.get("session_id") or "") or None,
        trace_id=str(record.get("trace_id") or "") or None,
        agent_id=str(record.get("agent_id") or "") or None,
    )
    artifactctl.ref_add("a2a", owner_id, ref.ref)
    artifactctl.alias_set(record_alias, ref.ref)
    return record_alias


def load_child_worktree_record(
    artifactctl: Any,
    record_alias: str,
) -> dict[str, Any]:
    """Load the latest owner-issued child record for review or disposition."""
    alias = str(record_alias or "").strip()
    ref = artifactctl.alias_resolve(alias)
    if ref is None:
        raise ValueError("child artifact record was not found")
    payload = json.loads(artifactctl.read_bytes(ref.ref))
    if not isinstance(payload, dict):
        raise ValueError("child artifact record is invalid")
    owner_id = str(payload.get("artifact", {}).get("owner_id") or "")
    if (
        alias != f"a2a-child:{owner_id}"
        or payload.get("record_alias") != alias
    ):
        raise ValueError("child artifact record is invalid")
    return cast(dict[str, Any], payload)


def _artifactctl_from_context(ctx: ExecutionContext) -> tuple[Any | None, bool]:
    runner = runner_from_context(ctx)
    candidates = [
        getattr(runner, "artifactctl", None),
        getattr(getattr(runner, "tool_api", None), "artifactctl", None),
        getattr(getattr(ctx, "_services", None), "artifactctl", None),
    ]
    for candidate in candidates:
        if candidate is not None:
            return candidate, False
    try:
        from openminion.modules.artifact.refs import create_default_artifactctl

        return create_default_artifactctl(), True
    except (ImportError, OSError, RuntimeError, ValueError):
        return None, False


def _stage_child_commit(lease: ChildWorktreeLease) -> str | None:
    _git(lease.worktree, "add", "-A", "--")
    if not _status_paths(lease.worktree):
        return None
    result = _git(
        lease.worktree,
        "-c",
        "user.email=openminion-child@example.invalid",
        "-c",
        "user.name=OpenMinion Child",
        "commit",
        "-m",
        f"openminion child handoff {lease.subtask_id}",
    )
    if result.returncode != 0:
        return None
    head = _git(lease.worktree, "rev-parse", "HEAD")
    return head.stdout.strip() if head.returncode == 0 else None


def _create_child_artifacts(
    ctx: ExecutionContext,
    *,
    lease: ChildWorktreeLease,
    touched_paths: list[str],
    status: str,
    validation: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    artifactctl, should_close = _artifactctl_from_context(ctx)
    if artifactctl is None:
        return {"status": "artifact_unavailable"}, _diff_text(lease.worktree)
    owner_id = f"{ctx.state.session_id}:{ctx.state.trace_id}:{lease.subtask_id}"
    try:
        with tempfile.TemporaryDirectory(prefix="openminion-child-handoff-") as tmp:
            scratch = Path(tmp)
            child_revision = _stage_child_commit(lease)
            if not child_revision:
                return {"status": "no_commit"}, _diff_text(lease.worktree)
            diff = _revision_diff(lease.worktree, lease.base_revision, child_revision)
            if not diff:
                return {"status": "diff_failed"}, ""
            target_digest = _sha256_text(diff)
            if validation.get("passed") is True:
                validation["target_digest"] = target_digest
            bundle_path = scratch / "child.bundle"
            bundle = _git(
                lease.worktree,
                "bundle",
                "create",
                str(bundle_path),
                "HEAD",
                f"^{lease.base_revision}",
            )
            if bundle.returncode != 0:
                return (
                    {
                        "status": "bundle_failed",
                        "stderr": bundle.stderr.strip()[:500],
                    },
                    diff,
                )
            bundle_sha = _sha256_path(bundle_path)
            manifest = {
                "schema_version": 1,
                "owner_type": "a2a",
                "owner_id": owner_id,
                "subtask_id": lease.subtask_id,
                "base_revision": lease.base_revision,
                "child_revision": child_revision,
                "touched_paths": touched_paths,
                "status": status,
                "validation": validation,
                "bundle_sha256": bundle_sha,
                "target_digest": target_digest,
            }
            manifest_path = scratch / "manifest.json"
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=True, sort_keys=True),
                encoding="utf-8",
            )
            bundle_ref = artifactctl.ingest_file(
                bundle_path,
                mime="application/vnd.git.bundle",
                label=f"maer-child-bundle:{lease.subtask_id}",
                meta=manifest,
                session_id=ctx.state.session_id,
                trace_id=ctx.state.trace_id,
                agent_id=ctx.state.agent_id,
            )
            manifest_ref = artifactctl.ingest_file(
                manifest_path,
                mime="application/json",
                label=f"maer-child-manifest:{lease.subtask_id}",
                meta={"bundle_ref": bundle_ref.ref, **manifest},
                session_id=ctx.state.session_id,
                trace_id=ctx.state.trace_id,
                agent_id=ctx.state.agent_id,
            )
            artifactctl.ref_add("a2a", owner_id, bundle_ref.ref)
            artifactctl.ref_add("a2a", owner_id, manifest_ref.ref)
            return (
                {
                    "status": "stored",
                    "owner_type": "a2a",
                    "owner_id": owner_id,
                    "bundle_ref": bundle_ref.ref,
                    "manifest_ref": manifest_ref.ref,
                    "bundle_sha256": bundle_sha,
                    "child_revision": child_revision,
                    "target_digest": target_digest,
                },
                diff,
            )
    finally:
        if should_close:
            close = getattr(artifactctl, "close", None)
            if callable(close):
                close()


def _record_conflicts(bucket: dict[str, Any]) -> None:
    by_path: dict[str, list[str]] = {}
    for child in list(bucket.get("children", []) or []):
        child_id = str(child.get("subtask_id") or "")
        for path in list(child.get("touched_paths", []) or []):
            by_path.setdefault(str(path), []).append(child_id)
    bucket["conflicts"] = [
        {"path": path, "subtask_ids": ids}
        for path, ids in sorted(by_path.items())
        if len(ids) > 1
    ]


def allocate_child_worktree(
    *,
    subtask: SubtaskSpec,
    child_state: WorkingState,
) -> ChildWorktreeLease | None:
    inputs = subtask.inputs
    if not bool(inputs.get("code_bearing") or inputs.get("worktree_required")):
        return None
    workspace_root = str(inputs.get("workspace_root") or "").strip()
    if not workspace_root:
        raise ValueError(
            "code-bearing orchestrate subtask requires inputs.workspace_root"
        )
    revision = str(inputs.get("base_revision") or "HEAD").strip() or "HEAD"
    isolator = WorktreeIsolator(parent_root=Path(workspace_root), revision=revision)
    worktree = isolator.allocate(1)[0]
    base_result = _git(Path(workspace_root), "rev-parse", revision)
    base_revision = (
        base_result.stdout.strip() if base_result.returncode == 0 else revision
    )
    _module_bucket(child_state)[_CHILD_STATE_KEY] = {
        "workspace": str(worktree),
        "base_revision": base_revision,
        "subtask_id": subtask.subtask_id,
    }
    return ChildWorktreeLease(
        isolator=isolator,
        worktree=worktree,
        subtask_id=subtask.subtask_id,
        base_revision=base_revision,
    )


@contextmanager
def bind_runner_tool_workspace(
    runner: Any,
    *,
    lease: ChildWorktreeLease | None,
) -> Iterator[None]:
    """Temporarily route production tool execution into a child worktree."""
    if lease is None:
        yield
        return

    tool_api = getattr(runner, "tool_api", None)
    if tool_api is None:
        yield
        return

    workspace_override = getattr(tool_api, "workspace_override", None)
    if callable(workspace_override):
        with workspace_override(lease.worktree):
            yield
        return

    previous_workspace_root = getattr(tool_api, "workspace_root", _MISSING)
    policy = getattr(tool_api, "policy", None)
    policy_raw = getattr(policy, "raw", None)
    previous_policy_raw = (
        copy.deepcopy(policy_raw) if isinstance(policy_raw, dict) else None
    )
    worktree_text = str(lease.worktree)

    try:
        if hasattr(tool_api, "workspace_root"):
            tool_api.workspace_root = lease.worktree
        if isinstance(policy_raw, dict):
            policy_raw["workspace_root"] = worktree_text
            context_metadata = policy_raw.get("context_metadata")
            if not isinstance(context_metadata, dict):
                context_metadata = {}
                policy_raw["context_metadata"] = context_metadata
            context_metadata["workspace_root"] = worktree_text
            context_metadata["cwd"] = worktree_text
        yield
    finally:
        if previous_workspace_root is _MISSING:
            try:
                delattr(tool_api, "workspace_root")
            except AttributeError:
                pass
        else:
            tool_api.workspace_root = previous_workspace_root
        if isinstance(policy_raw, dict) and previous_policy_raw is not None:
            policy_raw.clear()
            policy_raw.update(previous_policy_raw)


def finalize_child_worktree(
    ctx: ExecutionContext,
    *,
    lease: ChildWorktreeLease | None,
    status: str,
    validation: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if lease is None:
        return None
    touched_paths = _status_paths(lease.worktree)
    validation_payload = dict(validation or {})
    if touched_paths:
        artifact_record, diff = _create_child_artifacts(
            ctx,
            lease=lease,
            touched_paths=touched_paths,
            status=status,
            validation=validation_payload,
        )
    else:
        artifact_record, diff = {"status": "not_applicable"}, ""
    child_record: dict[str, Any] = {
        "session_id": ctx.state.session_id,
        "trace_id": ctx.state.trace_id,
        "agent_id": ctx.state.agent_id,
        "subtask_id": lease.subtask_id,
        "base_revision": lease.base_revision,
        "repository": str(lease.isolator.parent_root),
        "workspace": str(lease.worktree),
        "touched_paths": touched_paths,
        "diff": diff,
        "target_digest": str(artifact_record.get("target_digest") or ""),
        "validation": validation_payload,
        "status": status,
        "integration_status": (
            "pending_parent_review"
            if artifact_record.get("status") == "stored"
            else "artifact_failed"
            if touched_paths
            else "read_only"
        ),
        "artifact": artifact_record,
    }
    if not touched_paths or artifact_record.get("status") == "stored":
        lease.isolator.release()
    child_record["cleaned_up"] = not lease.worktree.exists()
    if artifact_record.get("status") == "stored":
        artifactctl, should_close = _artifactctl_from_context(ctx)
        if artifactctl is not None:
            try:
                save_child_worktree_record(artifactctl, child_record)
            finally:
                if should_close:
                    artifactctl.close()
    bucket = _module_bucket(ctx.state)
    bucket["children"].append(child_record)
    _record_conflicts(bucket)
    return child_record


def accept_child_worktree_artifact(
    *,
    repo_root: str | Path,
    record: dict[str, Any],
    artifactctl: Any,
) -> dict[str, Any]:
    """Apply a stored child bundle to the parent checkout after safety checks."""
    repo = Path(repo_root).expanduser().resolve(strict=True)
    artifact = record.get("artifact")
    if not isinstance(artifact, dict) or artifact.get("status") != "stored":
        return {"ok": False, "status": "missing_artifact"}
    if record.get("integration_status") != "pending_parent_review":
        return {"ok": False, "status": "artifact_already_disposed"}
    target_digest = str(record.get("target_digest") or "").strip()
    if not target_digest or target_digest != artifact.get("target_digest"):
        return {"ok": False, "status": "target_digest_mismatch"}
    if _sha256_text(str(record.get("diff") or "")) != target_digest:
        return {"ok": False, "status": "target_digest_mismatch"}
    review = record.get("review_receipt")
    if not isinstance(review, dict):
        return {"ok": False, "status": "missing_review"}
    if str(review.get("target_digest") or "") != target_digest:
        return {"ok": False, "status": "stale_review"}
    if str(review.get("bundle_ref") or "") != artifact.get("bundle_ref"):
        return {"ok": False, "status": "stale_review"}
    if review.get("passed") is not True:
        return {"ok": False, "status": "review_failed"}
    validation = record.get("validation")
    if not isinstance(validation, dict) or validation.get("passed") is not True:
        return {"ok": False, "status": "verification_failed"}
    if str(validation.get("target_digest") or "") != target_digest:
        return {"ok": False, "status": "stale_verification"}
    verifier_refs = validation.get("verifier_refs")
    if not isinstance(verifier_refs, list) or not verifier_refs or any(
        not isinstance(ref, str) or not ref.strip() for ref in verifier_refs
    ):
        return {"ok": False, "status": "verification_failed"}
    if review.get("verifier_refs") != verifier_refs:
        return {"ok": False, "status": "stale_review"}
    base_revision = str(record.get("base_revision") or "").strip()
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    if not base_revision or head != base_revision:
        return {"ok": False, "status": "stale_base", "head": head}
    touched_paths = [str(item) for item in record.get("touched_paths", []) or []]
    if _git(repo, "status", "--porcelain", "--", *touched_paths).stdout.strip():
        return {"ok": False, "status": "dirty_affected_paths"}

    with tempfile.TemporaryDirectory(prefix="openminion-child-accept-") as tmp:
        bundle_path = Path(tmp) / "child.bundle"
        bundle_path.write_bytes(artifactctl.read_bytes(str(artifact["bundle_ref"])))
        if _sha256_path(bundle_path) != str(artifact.get("bundle_sha256")):
            return {"ok": False, "status": "digest_mismatch"}
        temp_ref = f"refs/openminion/child/{artifact['bundle_sha256'][:16]}"
        fetch = _git(repo, "fetch", str(bundle_path), f"HEAD:{temp_ref}")
        if fetch.returncode != 0:
            return {"ok": False, "status": "bundle_fetch_failed"}
        diff = _git(repo, "diff", "--binary", base_revision, temp_ref)
        if diff.returncode != 0:
            _git(repo, "update-ref", "-d", temp_ref)
            return {"ok": False, "status": "diff_failed"}
        if _sha256_text(diff.stdout) != target_digest:
            _git(repo, "update-ref", "-d", temp_ref)
            return {"ok": False, "status": "target_digest_mismatch"}
        apply = _git_input(
            repo, "apply", "--index", "--binary", "-", input_text=diff.stdout
        )
        _git(repo, "update-ref", "-d", temp_ref)
        if apply.returncode != 0:
            return {"ok": False, "status": "apply_failed"}
    record["integration_status"] = "accepted"
    return {
        "ok": True,
        "status": "accepted",
        "target_digest": target_digest,
        "reviewer_agent_id": str(review.get("reviewer_agent_id") or ""),
        "verifier_refs": verifier_refs,
        "touched_paths": touched_paths,
    }


def reject_child_worktree_artifact(
    *, record: dict[str, Any], artifactctl: Any | None = None
) -> dict[str, Any]:
    del artifactctl
    artifact = record.get("artifact")
    target_digest = str(record.get("target_digest") or "").strip()
    if record.get("integration_status") != "pending_parent_review":
        return {"ok": False, "status": "artifact_already_disposed"}
    if (
        not isinstance(artifact, dict)
        or artifact.get("status") != "stored"
        or target_digest != artifact.get("target_digest")
    ):
        return {"ok": False, "status": "target_digest_mismatch"}
    record["integration_status"] = "rejected"
    return {
        "ok": True,
        "status": "rejected",
        "target_digest": target_digest,
        "bundle_ref": str(artifact.get("bundle_ref") or ""),
    }


__all__ = [
    "ChildWorktreeLease",
    "accept_child_worktree_artifact",
    "allocate_child_worktree",
    "bind_runner_tool_workspace",
    "child_verifier_evidence",
    "finalize_child_worktree",
    "load_child_worktree_record",
    "reject_child_worktree_artifact",
    "save_child_worktree_record",
]
