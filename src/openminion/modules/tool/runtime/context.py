"""Tool runtime context and dependency resolvers."""

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from openminion.base.time import utc_now_iso as iso_now
from openminion.base.config.env import EnvironmentConfig, resolve_environment_config
from openminion.base.logging import get_logger

from ..contracts.schemas import Artifact, LogEntry, Scope
from ..errors import ToolRuntimeError
from ..plugin_api import PolicyAdapter, SafetyAdapter
from .audit import (
    audit_writes_jsonl,
    audit_writes_storage,
    resolve_tool_runtime_audit_mode,
)
from .delegation import A2ADelegateApi
from .memory import MemoryToolRuntimeService
from .policy import Policy
from .repositories import (
    LazyRepositoryHandle,
    RuntimeRepositories,
    build_runtime_repositories,
)


__all__ = [
    "RuntimeContext",
    "preferred_artifact_ref",
    "resolve_audit_repository",
    "resolve_cron_repository",
    "resolve_a2a_delegate_api",
    "resolve_identity_repository",
    "resolve_memory_service",
]


_LOG = get_logger("tool.runtime")


def _context_metadata_from_policy(policy: Policy | None) -> dict[str, Any]:
    raw = getattr(policy, "raw", {})
    if not isinstance(raw, Mapping):
        return {}
    metadata = raw.get("context_metadata")
    return dict(metadata) if isinstance(metadata, Mapping) else {}


def _orchestration_metadata_from_policy(policy: Policy | None) -> dict[str, Any]:
    metadata = _context_metadata_from_policy(policy)
    orchestration = metadata.get("orchestration")
    if not isinstance(orchestration, Mapping):
        return {}
    return {
        str(key): value
        for key, value in orchestration.items()
        if str(key or "").strip()
    }


def resolve_identity_repository(ctx: "RuntimeContext") -> Any | None:
    """Resolve identity repository handle from runtime context wiring."""
    try:
        repo = ctx.repositories.identity.get()
    except Exception:
        repo = None
    if repo is not None:
        return repo

    fallback = build_runtime_repositories(
        context_metadata=_context_metadata_from_policy(getattr(ctx, "policy", None))
    )
    try:
        repo = fallback.identity.get()
    except Exception:
        repo = None
    if repo is None:
        return None

    ctx.repositories.identity = fallback.identity
    if ctx.repositories.identity_path is None:
        ctx.repositories.identity_path = fallback.identity_path
    return repo


def resolve_cron_repository(ctx: "RuntimeContext") -> Any | None:
    """Resolve cron repository handle from runtime context wiring."""
    try:
        repo = ctx.repositories.cron.get()
    except Exception:
        repo = None
    if repo is not None:
        return repo

    fallback = build_runtime_repositories(
        context_metadata=_context_metadata_from_policy(getattr(ctx, "policy", None))
    )
    try:
        repo = fallback.cron.get()
    except Exception:
        repo = None
    if repo is None:
        return None

    ctx.repositories.cron = fallback.cron
    if ctx.repositories.cron_db_path is None:
        ctx.repositories.cron_db_path = fallback.cron_db_path
    return repo


def resolve_audit_repository(ctx: "RuntimeContext") -> Any | None:
    """Resolve audit repository handle from runtime context wiring."""
    try:
        repo = ctx.repositories.audit.get()
    except Exception:
        repo = None
    if repo is not None:
        return repo

    fallback = build_runtime_repositories(
        context_metadata=_context_metadata_from_policy(getattr(ctx, "policy", None))
    )
    try:
        repo = fallback.audit.get()
    except Exception:
        repo = None
    if repo is None:
        return None

    ctx.repositories.audit = fallback.audit
    if ctx.repositories.audit_db_path is None:
        ctx.repositories.audit_db_path = fallback.audit_db_path
    return repo


def resolve_memory_service(ctx: "RuntimeContext") -> MemoryToolRuntimeService | None:
    """Resolve the approved typed memory-service seam for tool handlers."""
    service = getattr(ctx, "memory_service", None)
    if isinstance(service, MemoryToolRuntimeService):
        return service
    return None


def resolve_a2a_delegate_api(ctx: "RuntimeContext") -> A2ADelegateApi | None:
    """Resolve the approved typed A2A-delegation seam for tool handlers."""
    seam = getattr(ctx, "a2a_delegate_api", None)
    if isinstance(seam, A2ADelegateApi):
        return seam
    return None


def preferred_artifact_ref(artifact: Any) -> str:
    """Return canonical CAS ref when available, else the local artifact path/ref."""
    if isinstance(artifact, Mapping):
        canonical_ref = str(artifact.get("canonical_ref", "") or "").strip()
        if canonical_ref:
            return canonical_ref
        direct_ref = str(artifact.get("ref", "") or "").strip()
        if direct_ref:
            return direct_ref
        return str(artifact.get("path", "") or "").strip()

    canonical_ref = str(getattr(artifact, "canonical_ref", "") or "").strip()
    if canonical_ref:
        return canonical_ref
    direct_ref = str(getattr(artifact, "ref", "") or "").strip()
    if direct_ref:
        return direct_ref
    return str(getattr(artifact, "path", "") or "").strip()


@dataclass
class RuntimeContext:
    policy: Policy
    workspace: Path
    run_root: Path
    scope: Scope
    confirm: bool
    env: EnvironmentConfig = field(default_factory=resolve_environment_config)
    repositories: RuntimeRepositories = field(default_factory=RuntimeRepositories)
    logs: list[LogEntry] = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)
    safety_adapter: Optional[SafetyAdapter] = None
    policy_adapter: Optional[PolicyAdapter] = None
    skill_api: Optional[Any] = None
    telemetryctl: Optional[Any] = None
    telemetry_session_id: Optional[str] = None
    telemetry_turn_id: Optional[str] = None
    artifactctl: Optional[Any] = None
    memory_service: MemoryToolRuntimeService | None = None
    sandbox_runner: Any | None = None
    authored_tools_api: Any | None = None
    a2a_delegate_api: A2ADelegateApi | None = None
    agent_profile: Optional[Any] = None

    def add_log(
        self, level: str, msg: str, meta: Optional[Dict[str, Any]] = None
    ) -> None:
        self.logs.append(LogEntry(ts=iso_now(), level=level, msg=msg, meta=meta or {}))

    def write_artifact(
        self,
        rel_path: str,
        content: bytes,
        mime: str,
        *,
        durable: bool = False,
    ) -> Artifact:
        max_single = self.policy.limit_int("max_single_artifact_bytes", 10_000_000)
        max_total = self.policy.limit_int("max_artifact_bytes_total", 50_000_000)
        existing_total = sum(item.bytes for item in self.artifacts)
        if len(content) > max_single:
            raise ToolRuntimeError(
                "POLICY_DENIED",
                f"Artifact exceeds max_single_artifact_bytes ({max_single})",
                {
                    "rule": "limits.max_single_artifact_bytes",
                    "artifact_bytes": len(content),
                },
            )
        if existing_total + len(content) > max_total:
            raise ToolRuntimeError(
                "POLICY_DENIED",
                f"Artifact total exceeds max_artifact_bytes_total ({max_total})",
                {
                    "rule": "limits.max_artifact_bytes_total",
                    "artifact_bytes_total": existing_total + len(content),
                },
            )

        path = (self.run_root / rel_path).resolve(strict=False)
        try:
            path.relative_to(self.run_root)
        except ValueError:
            raise ToolRuntimeError(
                "POLICY_DENIED", "Artifact path escape denied", {"path": str(path)}
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        sha = hashlib.sha256(content).hexdigest()
        canonical_ref: str | None = None
        if durable:
            artifactctl = getattr(self, "artifactctl", None)
            if artifactctl is None:
                self.add_log(
                    "warning",
                    "CAS ingest unavailable for durable artifact write",
                    {
                        "rel_path": rel_path,
                        "mime": mime,
                        "bytes": len(content),
                        "durable": True,
                    },
                )
            else:
                ingest_meta = {
                    "runtime_bridge": "RuntimeContext.write_artifact",
                    "tool_name": str(getattr(self, "tool_name", "") or "").strip(),
                    "local_rel_path": rel_path,
                    "mime": mime,
                }
                run_id = str(getattr(self, "run_id", "") or "").strip()
                if run_id:
                    ingest_meta["run_id"] = run_id
                trace_id = str(getattr(self, "trace_id", "") or "").strip()
                if trace_id:
                    ingest_meta["trace_id"] = trace_id
                try:
                    ref = artifactctl.ingest_bytes(
                        data=content,
                        mime=mime,
                        original_name=Path(rel_path).name,
                        label=Path(rel_path).name,
                        meta=ingest_meta,
                        session_id=str(getattr(self, "session_id", "") or "").strip()
                        or None,
                        trace_id=trace_id or None,
                        agent_id=str(getattr(self, "agent_id", "") or "").strip()
                        or None,
                    )
                    canonical_ref = str(getattr(ref, "ref", "") or "").strip() or None
                except Exception as exc:
                    self.add_log(
                        "warning",
                        "CAS ingest failed for durable artifact write",
                        {
                            "rel_path": rel_path,
                            "mime": mime,
                            "bytes": len(content),
                            "durable": True,
                            "error": f"{type(exc).__name__}: {exc}",
                        },
                    )
                    _LOG.warning(
                        "tool.runtime.artifact_cas_ingest_failed rel_path=%s error=%s: %s",
                        rel_path,
                        type(exc).__name__,
                        exc,
                    )
        artifact = Artifact(
            type="file",
            path=rel_path,
            mime=mime,
            bytes=len(content),
            sha256=sha,
            canonical_ref=canonical_ref,
        )
        self.artifacts.append(artifact)
        return artifact

    def write_audit_event(self, event: Dict[str, Any]) -> None:
        payload = dict(event or {})
        orchestration = _orchestration_metadata_from_policy(self.policy)
        for key, value in orchestration.items():
            payload.setdefault(key, value)
        payload["event_id"] = str(payload.get("event_id") or uuid.uuid4().hex)
        payload["ts"] = str(payload.get("ts") or iso_now())
        mode = resolve_tool_runtime_audit_mode(policy=self.policy)

        if audit_writes_jsonl(mode):
            line = json.dumps(payload, ensure_ascii=True)
            audit_path = self.run_root / "audit.jsonl"
            with audit_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")

        if not audit_writes_storage(mode):
            return
        audit_repo = resolve_audit_repository(self)
        if audit_repo is None:
            return
        try:
            audit_repo.append_event(payload, run_root=self.run_root)
        except Exception:
            return


# Suppress "imported but unused" — LazyRepositoryHandle re-exported via __init__.
_ = LazyRepositoryHandle
