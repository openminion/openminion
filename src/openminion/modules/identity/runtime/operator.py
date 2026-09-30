from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from hashlib import sha256
import os
from pathlib import Path
import tempfile
from typing import Any

from pydantic import ValidationError
import yaml

from openminion.modules.identity.config import load_yaml_file
from openminion.modules.identity.models import AgentProfile, AgentProfileInput
from openminion.modules.identity.runtime.lockfile import (
    IDENTITY_LOCKFILE_NAME,
    read_identity_lockfile,
)
from openminion.modules.identity.runtime.service import IdentityCtl


class IdentityOperatorError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        issues: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.issues = issues or []


def inspect_identity(
    ctl: IdentityCtl,
    *,
    identity_root: Path,
    agent_id: str | None = None,
    purpose: str = "act",
    max_tokens: int = 180,
) -> dict[str, Any]:
    if agent_id is None:
        summaries = ctl.list_profiles()
        return {
            "profiles": [
                _inspect_profile(
                    ctl,
                    identity_root=identity_root,
                    agent_id=item.agent_id,
                    purpose=purpose,
                    max_tokens=max_tokens,
                )
                for item in summaries
            ]
        }

    if ctl.get_profile_summary(agent_id) is None:
        raise IdentityOperatorError(
            f"profile not found: {agent_id}",
            code="profile_not_found",
        )
    return _inspect_profile(
        ctl,
        identity_root=identity_root,
        agent_id=agent_id,
        purpose=purpose,
        max_tokens=max_tokens,
    )


def validate_identity_candidate(
    ctl: IdentityCtl,
    *,
    candidate_path: Path,
    strict: bool = False,
    purpose: str = "act",
    max_tokens: int = 180,
) -> dict[str, Any]:
    profile = _load_direct_profile(candidate_path)
    validation = ctl.validate_profile(profile, strict=strict)
    if not validation.ok:
        raise IdentityOperatorError(
            "identity candidate failed semantic validation",
            code="validation_failed",
            issues=[{"message": message} for message in validation.errors],
        )
    snippet = ctl.render_from_profile(
        profile,
        purpose=purpose,
        max_tokens=max_tokens,
    )
    return {
        "ok": True,
        "agent_id": profile.agent_id,
        "profile": profile.model_dump(mode="json", exclude_none=True),
        "validation": validation.model_dump(mode="json"),
        "render": snippet.model_dump(mode="json"),
    }


def apply_identity_candidate(
    ctl: IdentityCtl,
    *,
    identity_root: Path,
    candidate_path: Path,
    expected_profile_version: str,
    expected_source_sha256: str,
    strict: bool = False,
) -> dict[str, Any]:
    candidate = validate_identity_candidate(
        ctl,
        candidate_path=candidate_path,
        strict=strict,
    )
    profile = AgentProfile.model_validate(candidate["profile"])
    _validate_agent_id_path(profile.agent_id)

    resolved_root = identity_root.expanduser().resolve()
    profile_root = (resolved_root / profile.agent_id).resolve()
    target = profile_root / "profile.yaml"
    if profile_root.parent != resolved_root:
        raise IdentityOperatorError(
            "agent_id must be one safe path segment",
            code="unsafe_agent_id",
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    with _profile_apply_lock(target):
        current_version = _validate_apply_state(
            ctl,
            profile=profile,
            target=target,
            expected_profile_version=expected_profile_version,
            expected_source_sha256=expected_source_sha256,
        )
        _write_and_sync_profile(ctl, profile=profile, target=target)

    result = inspect_identity(
        ctl,
        identity_root=resolved_root,
        agent_id=profile.agent_id,
    )
    result["previous_profile_version"] = current_version
    return result


def _validate_apply_state(
    ctl: IdentityCtl,
    *,
    profile: AgentProfile,
    target: Path,
    expected_profile_version: str,
    expected_source_sha256: str,
) -> str:
    current = ctl.get_profile_summary(profile.agent_id)
    current_version = current.profile_version if current is not None else "missing"
    if expected_profile_version != current_version:
        raise IdentityOperatorError(
            "identity profile changed since it was loaded",
            code="stale_profile_version",
            issues=[
                {
                    "expected_profile_version": expected_profile_version,
                    "current_profile_version": current_version,
                }
            ],
        )
    if target.is_symlink():
        raise IdentityOperatorError(
            "canonical profile.yaml cannot be a symbolic link",
            code="profile_read_only",
        )
    if current is None:
        current_source_sha256 = _file_sha256(target) if target.is_file() else "missing"
        if expected_source_sha256 != current_source_sha256:
            _raise_source_conflict(
                expected_source_sha256,
                current_source_sha256,
                creating=True,
            )
        return current_version

    current_profile = ctl.get_profile(profile.agent_id)
    if current_profile is None:
        raise RuntimeError("identity summary has no stored profile")
    source = str((current_profile.meta or {}).get("source", "")).strip().lower()
    if source != "yaml" or not target.is_file():
        raise IdentityOperatorError(
            "only canonical direct YAML profiles can be edited",
            code="profile_read_only",
        )
    current_source_sha256 = _file_sha256(target)
    if expected_source_sha256 != current_source_sha256:
        _raise_source_conflict(expected_source_sha256, current_source_sha256)
    return current_version


def _raise_source_conflict(
    expected: str, current: str, *, creating: bool = False
) -> None:
    message = (
        "identity YAML creation state changed since it was loaded"
        if creating
        else "identity YAML changed since it was loaded"
    )
    raise IdentityOperatorError(
        message,
        code="stale_source_sha256",
        issues=[
            {
                "expected_source_sha256": expected,
                "current_source_sha256": current,
            }
        ],
    )


def _write_and_sync_profile(
    ctl: IdentityCtl, *, profile: AgentProfile, target: Path
) -> None:
    payload = profile.model_dump(mode="python", exclude_none=True)
    payload["meta"] = {**dict(profile.meta or {}), "source": "yaml"}
    payload["meta"].pop("source_path", None)
    target.parent.mkdir(parents=True, exist_ok=True)
    previous_profile = ctl.store.get_profile(profile.agent_id)
    previous_files = {
        path: path.read_bytes() if path.is_file() else None
        for path in (target, *_generated_paths(target.parent))
    }
    _atomic_write(
        target,
        yaml.safe_dump(payload, sort_keys=False, default_flow_style=False),
    )
    try:
        loaded = ctl.load_profiles_from_path(target)
        if profile.agent_id not in loaded:
            raise RuntimeError(
                f"identity sync did not load profile: {profile.agent_id}"
            )
    except Exception as exc:
        _restore_files(previous_files)
        if previous_profile is None:
            ctl.store.delete_profile(profile.agent_id)
        else:
            ctl.store.restore_profile(previous_profile)
        ctl.clear_cache(agent_id=profile.agent_id)
        raise IdentityOperatorError(
            "identity synchronization failed; prior source and store were restored",
            code="synchronization_failed",
            issues=[{"message": str(exc)}],
        ) from exc


def runtime_identity_snapshot(
    ctl: IdentityCtl,
    *,
    agent_id: str,
    purpose: str,
    max_tokens: int,
) -> dict[str, Any] | None:
    profile = ctl.get_profile(agent_id)
    if profile is None:
        return None
    summary = ctl.get_profile_summary(agent_id)
    if summary is None:
        raise RuntimeError("identity profile has no stored summary")
    snippet = ctl.render(agent_id, purpose=purpose, max_tokens=max_tokens)
    return {
        "agent_id": profile.agent_id,
        "display_name": profile.display_name,
        "mission": profile.role.mission,
        "tone": profile.personality.tone,
        "source": str((profile.meta or {}).get("source", "unknown")),
        "profile_revision": profile.profile_revision,
        "profile_version": summary.profile_version,
        "render_version": snippet.render_version,
        "purpose": snippet.purpose,
        "rendered_text": snippet.text,
        "included_fields": list(snippet.included_fields),
        "omitted_fields": list(snippet.omitted_fields),
    }


def verify_runtime_identity(
    ctl: IdentityCtl,
    *,
    identity_root: Path,
    agent_id: str,
    purpose: str,
    max_tokens: int,
) -> dict[str, Any] | None:
    if ctl.get_profile_summary(agent_id) is None:
        return None
    report = inspect_identity(
        ctl,
        identity_root=identity_root,
        agent_id=agent_id,
        purpose=purpose,
        max_tokens=max_tokens,
    )
    render = dict(report.get("render") or {})
    effective = dict(report.get("effective_profile") or {})
    role = dict(effective.get("role") or {})
    personality = dict(effective.get("personality") or {})
    return {
        **report,
        "mission": role.get("mission", ""),
        "tone": personality.get("tone", ""),
        "render_version": render.get("render_version", ""),
        "rendered_text": render.get("text", ""),
        "purpose": render.get("purpose", purpose),
    }


def reload_runtime_identity(
    ctl: IdentityCtl,
    *,
    identity_root: Path,
    agent_id: str,
) -> None:
    profile_path = identity_root / agent_id / "profile.yaml"
    if not profile_path.is_file():
        raise FileNotFoundError(f"canonical identity profile not found: {profile_path}")
    ctl.load_profiles_from_path(profile_path)


def _inspect_profile(
    ctl: IdentityCtl,
    *,
    identity_root: Path,
    agent_id: str,
    purpose: str,
    max_tokens: int,
) -> dict[str, Any]:
    _validate_agent_id_path(agent_id)
    profile = ctl.get_profile(agent_id)
    if profile is None:
        raise IdentityOperatorError(
            f"profile not found: {agent_id}",
            code="profile_not_found",
        )
    summary = ctl.get_profile_summary(agent_id)
    if summary is None:
        raise RuntimeError("identity profile has no stored summary")
    source = str((profile.meta or {}).get("source", "") or "unknown").strip().lower()
    root = identity_root.expanduser().resolve()
    profile_root = (root / agent_id).resolve()
    canonical_path = profile_root / "profile.yaml"
    canonical_resolved = canonical_path.resolve()
    recorded_path = str((profile.meta or {}).get("source_path", "") or "").strip()
    source_path = (
        Path(recorded_path).expanduser().resolve() if recorded_path else canonical_path
    )
    canonical_source = (
        profile_root.parent == root
        and not canonical_path.is_symlink()
        and source_path == canonical_resolved
        and source_path.is_file()
    )
    authored_yaml = (
        source_path.read_text(encoding="utf-8") if canonical_source else None
    )
    source_sha256 = _file_sha256(source_path) if canonical_source else None
    editable = source == "yaml" and canonical_source and not profile.inherits
    if editable:
        read_only_reason = None
    elif profile.inherits:
        read_only_reason = (
            "inherited profiles are inspectable but not directly editable"
        )
    elif source != "yaml":
        read_only_reason = f"{source or 'unknown'}-managed profiles are read-only"
    elif source_path != canonical_resolved or profile_root.parent != root:
        read_only_reason = "source YAML is outside the canonical identity root"
    else:
        read_only_reason = "canonical profile.yaml was not found"

    validation = ctl.validate_profile(profile)
    snippet = ctl.render(agent_id, purpose=purpose, max_tokens=max_tokens)
    sidecars = _generated_sidecar_status(
        canonical_path.parent,
        profile_version=summary.profile_version,
    )
    verification = _verification_status(
        ctl,
        identity_root=root,
        source=source,
        source_path=canonical_path if canonical_source else None,
        expected_agent_id=agent_id,
        stored_profile_version=summary.profile_version,
        stored_validation=validation.model_dump(mode="json"),
        render_validation=ctl.validate_render(snippet).model_dump(mode="json"),
        generated_sidecars=sidecars,
        purpose=purpose,
        max_tokens=max_tokens,
    )
    return {
        "agent_id": agent_id,
        "display_name": profile.display_name,
        "source": source or "unknown",
        "source_path": str(source_path) if recorded_path else None,
        "source_sha256": source_sha256,
        "editable": editable,
        "read_only_reason": read_only_reason,
        "authored_yaml": authored_yaml,
        "effective_profile": profile.model_dump(mode="json", exclude_none=True),
        "profile_revision": summary.profile_revision,
        "profile_version": summary.profile_version,
        "updated_at": summary.updated_at,
        "validation": validation.model_dump(mode="json"),
        "verification": verification,
        "render": snippet.model_dump(mode="json"),
        "generated_sidecars": sidecars,
    }


def _load_direct_profile(candidate_path: Path) -> AgentProfile:
    path = candidate_path.expanduser().resolve()
    try:
        payload = load_yaml_file(path)
        if "profiles" in payload:
            raise IdentityOperatorError(
                "identity apply accepts one direct profile, not a profiles collection",
                code="multi_profile_candidate",
            )
        profile = AgentProfile.model_validate(payload)
    except IdentityOperatorError:
        raise
    except (OSError, ValueError, ValidationError) as exc:
        issues: list[dict[str, Any]] = (
            [dict(item) for item in exc.errors(include_url=False)]
            if isinstance(exc, ValidationError)
            else [{"message": str(exc)}]
        )
        code = "invalid_candidate"
        if isinstance(exc, ValidationError) and any(
            tuple(item.get("loc", ())) == ("agent_id",)
            and "safe path segment" in str(item.get("msg", ""))
            for item in exc.errors(include_url=False)
        ):
            code = "unsafe_agent_id"
        raise IdentityOperatorError(
            "identity candidate is invalid",
            code=code,
            issues=issues,
        ) from exc
    if profile.inherits:
        raise IdentityOperatorError(
            "inherited profiles are read-only in this editor workflow",
            code="inherited_candidate",
        )
    _validate_agent_id_path(profile.agent_id)
    return profile.model_copy(
        update={"meta": {**dict(profile.meta or {}), "source": "yaml"}}
    )


def _validate_agent_id_path(agent_id: str) -> None:
    if (
        agent_id in {".", ".."}
        or Path(agent_id).name != agent_id
        or any(separator in agent_id for separator in ("/", "\\"))
    ):
        raise IdentityOperatorError(
            "agent_id must be one safe path segment",
            code="unsafe_agent_id",
        )


def _verification_status(
    ctl: IdentityCtl,
    *,
    identity_root: Path,
    source: str,
    source_path: Path | None,
    expected_agent_id: str,
    stored_profile_version: str,
    stored_validation: dict[str, Any],
    render_validation: dict[str, Any],
    generated_sidecars: dict[str, Any],
    purpose: str,
    max_tokens: int,
) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    source_profile_version: str | None = None
    source_status = "not_applicable"
    if source == "yaml":
        if source_path is None:
            source_status = "missing"
            issues.append({"message": "canonical profile.yaml was not found"})
        else:
            try:
                payload = load_yaml_file(source_path)
                if "profiles" in payload:
                    raise IdentityOperatorError(
                        "canonical profile.yaml must contain one profile",
                        code="multi_profile_candidate",
                    )
                profile_input = AgentProfileInput.model_validate(payload)
                source_agent_id = (profile_input.agent_id or "").strip()
                if source_agent_id != expected_agent_id:
                    raise IdentityOperatorError(
                        "profile.yaml agent_id does not match its agent directory",
                        code="agent_id_mismatch",
                        issues=[
                            {
                                "expected_agent_id": expected_agent_id,
                                "actual_agent_id": source_agent_id,
                            }
                        ],
                    )
                source_profile = ctl.resolve_profile_input(
                    profile_input,
                    agent_id=expected_agent_id,
                    source_path=source_path,
                )
                source_validation = ctl.validate_profile(source_profile)
                if not source_validation.ok:
                    raise IdentityOperatorError(
                        "identity source failed semantic validation",
                        code="validation_failed",
                        issues=[
                            {"message": message} for message in source_validation.errors
                        ],
                    )
                source_profile_version = ctl.render_from_profile(
                    source_profile,
                    purpose=purpose,
                    max_tokens=max_tokens,
                ).profile_version
                source_status = (
                    "clean"
                    if source_profile_version == stored_profile_version
                    else "drifted"
                )
                if source_status == "drifted":
                    issues.append(
                        {"message": "source YAML does not match the stored profile"}
                    )
                if profile_input.inherits:
                    parent_status, parent_issues = _inherited_source_status(
                        ctl,
                        identity_root=identity_root,
                        parent_id=profile_input.inherits,
                        purpose=purpose,
                        max_tokens=max_tokens,
                    )
                    if parent_issues:
                        source_status = parent_status
                        source_profile_version = None
                        issues.extend(parent_issues)
            except (IdentityOperatorError, ValidationError, ValueError) as exc:
                source_status = "invalid"
                issues.extend(
                    exc.issues
                    if isinstance(exc, IdentityOperatorError) and exc.issues
                    else [{"message": str(exc)}]
                )

    return _verification_result(
        issues=issues,
        source=source,
        source_status=source_status,
        source_profile_version=source_profile_version,
        stored_profile_version=stored_profile_version,
        stored_ok=bool(stored_validation.get("ok")),
        render_ok=bool(render_validation.get("ok")),
        sidecar_status=str(generated_sidecars.get("status", "unknown")),
    )


def _inherited_source_status(
    ctl: IdentityCtl,
    *,
    identity_root: Path,
    parent_id: str,
    purpose: str,
    max_tokens: int,
) -> tuple[str, list[dict[str, str]]]:
    parent_report = _inspect_profile(
        ctl,
        identity_root=identity_root,
        agent_id=parent_id,
        purpose=purpose,
        max_tokens=max_tokens,
    )
    status = str(parent_report["verification"]["source_status"])
    if status in {"clean", "not_applicable"}:
        return status, []
    return status, [
        {"message": f"inherited source is not clean: {parent_id} ({status})"}
    ]


def _verification_result(
    *,
    issues: list[dict[str, Any]],
    source: str,
    source_status: str,
    source_profile_version: str | None,
    stored_profile_version: str,
    stored_ok: bool,
    render_ok: bool,
    sidecar_status: str,
) -> dict[str, Any]:
    if not stored_ok:
        issues.append({"message": "stored profile validation failed"})
    if not render_ok:
        issues.append({"message": "render validation failed"})
    if source == "yaml" and sidecar_status != "clean":
        issues.append({"message": "generated identity sidecars are not synchronized"})
    return {
        "ok": not issues,
        "source_status": source_status,
        "source_profile_version": source_profile_version,
        "stored_profile_version": stored_profile_version,
        "sidecar_status": sidecar_status,
        "issues": issues,
    }


def _generated_sidecar_status(
    bundle_root: Path,
    *,
    profile_version: str,
) -> dict[str, Any]:
    lock_path = bundle_root / IDENTITY_LOCKFILE_NAME
    if not lock_path.is_file():
        return {"status": "missing", "lock_profile_version": None, "files": []}
    try:
        lockfile = read_identity_lockfile(lock_path)
        drifted = []
        for item in lockfile.files:
            path = _safe_generated_path(bundle_root, item.relative_path)
            if (
                path is None
                or not path.is_file()
                or sha256(path.read_bytes()).hexdigest() != item.sha256
            ):
                drifted.append(item.relative_path)
        clean = (
            not drifted and lockfile.generated_from_profile_version == profile_version
        )
        return {
            "status": "clean" if clean else "drifted",
            "lock_profile_version": lockfile.generated_from_profile_version,
            "files": [item.relative_path for item in lockfile.files],
            "drifted_files": drifted,
        }
    except (OSError, ValueError) as exc:
        return {
            "status": "invalid",
            "lock_profile_version": None,
            "files": [],
            "error": str(exc),
        }


def _atomic_write(path: Path, content: str) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".profile.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temporary_path = Path(handle.name)
        temporary_path.chmod(0o600)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


@contextmanager
def _profile_apply_lock(target: Path) -> Iterator[None]:
    lock_path = target.parent / ".identity-apply.lock"
    if lock_path.is_symlink():
        raise IdentityOperatorError(
            f"identity apply lock must not be a symlink: {lock_path}",
            code="unsafe_lock_path",
        )
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(lock_path, flags, 0o600)
    with os.fdopen(descriptor, "r+b") as handle:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            if not handle.read(1):
                handle.seek(0)
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            getattr(msvcrt, "locking")(handle.fileno(), getattr(msvcrt, "LK_LOCK"), 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                getattr(msvcrt, "locking")(
                    handle.fileno(), getattr(msvcrt, "LK_UNLCK"), 1
                )
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _generated_paths(root: Path) -> tuple[Path, ...]:
    return tuple(
        root / name
        for name in ("AGENT.md", "SOUL.md", "README.md", IDENTITY_LOCKFILE_NAME)
    )


def _safe_generated_path(root: Path, relative_path: str) -> Path | None:
    relative = Path(relative_path)
    if relative.is_absolute():
        return None
    candidate = root / relative
    if candidate.is_symlink():
        return None
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def _restore_files(previous: dict[Path, bytes | None]) -> None:
    for path, payload in previous.items():
        if payload is None:
            path.unlink(missing_ok=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)


__all__ = [
    "IdentityOperatorError",
    "apply_identity_candidate",
    "inspect_identity",
    "validate_identity_candidate",
]
