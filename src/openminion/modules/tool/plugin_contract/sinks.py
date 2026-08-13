import hashlib
from datetime import datetime, timezone
from typing import Any, Optional

from .invocation import ArtifactRef


class NullEventSink:
    def emit(self, *, event_name: str, payload: dict[str, Any]) -> None:
        del event_name, payload


class MemoryEventSink:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, *, event_name: str, payload: dict[str, Any]) -> None:
        self.events.append({"event_name": event_name, "payload": dict(payload)})


class MemoryArtifactSink:
    """In-memory artifact sink that also returns stable hash-based references."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_bytes(
        self,
        *,
        name: str,
        content: bytes,
        kind: str,
        meta: Optional[dict[str, Any]] = None,
    ) -> ArtifactRef:
        sha = hashlib.sha256(content).hexdigest()
        ref = f"artifact:sha256:{sha}"
        self.objects[ref] = content
        out_meta = dict(meta or {})
        out_meta.setdefault("size", len(content))
        out_meta.setdefault("sha256", sha)
        out_meta.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        return ArtifactRef(ref=ref, kind=kind, name=name, meta=out_meta)


class CASArtifactSink:
    """Artifact sink backed by ArtifactCtl canonical CAS storage."""

    def __init__(
        self,
        *,
        artifactctl: Any,
        session_id: str | None = None,
        trace_id: str | None = None,
        agent_id: str | None = None,
    ) -> None:
        self._artifactctl = artifactctl
        self._session_id = str(session_id or "").strip() or None
        self._trace_id = str(trace_id or "").strip() or None
        self._agent_id = str(agent_id or "").strip() or None

    def put_bytes(
        self,
        *,
        name: str,
        content: bytes,
        kind: str,
        meta: Optional[dict[str, Any]] = None,
    ) -> ArtifactRef:
        ref = self._artifactctl.ingest_bytes(
            data=content,
            mime=str((meta or {}).get("mime", "") or None) or None,
            original_name=name,
            label=name,
            meta=dict(meta or {}),
            session_id=self._session_id,
            trace_id=self._trace_id,
            agent_id=self._agent_id,
        )
        out_meta = dict(meta or {})
        out_meta.setdefault("size", len(content))
        out_meta.setdefault("sha256", str(getattr(ref, "sha256", "") or ""))
        out_meta.setdefault("created_at", str(getattr(ref, "created_at", "") or ""))
        return ArtifactRef(
            ref=str(getattr(ref, "ref", "") or ""),
            kind=kind,
            name=name,
            meta=out_meta,
        )
