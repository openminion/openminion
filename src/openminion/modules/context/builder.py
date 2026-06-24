from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contracts import CONTEXT_CLIENT_INTERFACE_VERSION, IdentityClient
from .schemas import (
    ArtifactDigest,
    BuildConstraints,
    BuildPackRequest,
    ContextPack,
    FactRecord,
    IdentitySnippet,
    MemoryCard,
    RecentSessionArtifactRef,
    SessionSlice,
    SessionToolEvent,
    SessionTurn,
)
from .service import ContextCtlService
from openminion.base.constants import STATE_KEY_ACTIVE

try:  # Optional dependency for canonical slice contract
    from openminion.modules.session import SliceLimits as _SessctlSliceLimits
    from openminion.modules.session import (
        SQLiteSessionStore as _SessctlSQLiteSessionStore,
    )
except ModuleNotFoundError:  # pragma: no cover - optional workspace dependency
    _SessctlSliceLimits = None
    _SessctlSQLiteSessionStore = None


def _resolve_db_path(db_path: str | Path) -> Path:
    return Path(db_path).expanduser().resolve()


@dataclass(frozen=True)
class BuildOptions:
    session_id: str
    agent_id: str
    purpose: str
    user_input: str
    provider_pref: str | None = None
    constraints: dict[str, Any] | None = None


class _StaticIdentityClient:
    contract_version = CONTEXT_CLIENT_INTERFACE_VERSION

    def render(
        self,
        *,
        agent_id: str,
        purpose: str,
        max_tokens: int,
        provider_pref: str | None = None,
        query_text: str | None = None,
    ) -> IdentitySnippet:
        text = (
            f"agent_id={agent_id}\n"
            f"purpose={purpose}\n"
            f"provider_pref={provider_pref or 'generic'}\n"
            "identity_mode=standalone-local"
        )
        return IdentitySnippet(
            agent_id=agent_id,
            profile_version="local-v1",
            render_version=f"render-{provider_pref or 'generic'}-v1",
            text=text,
        )


class _IdentityctlCoreClient:
    contract_version = CONTEXT_CLIENT_INTERFACE_VERSION

    def __init__(self, service: Any) -> None:
        self._service = service

    def render(
        self,
        *,
        agent_id: str,
        purpose: str,
        max_tokens: int,
        provider_pref: str | None = None,
        query_text: str | None = None,
    ) -> IdentitySnippet:
        snippet = self._service.render(
            agent_id=agent_id,
            purpose=purpose,
            max_tokens=max_tokens,
            provider_pref=provider_pref,
            query_text=query_text,
        )
        return IdentitySnippet(
            agent_id=snippet.agent_id,
            profile_version=snippet.profile_version,
            render_version=snippet.render_version,
            text=snippet.text,
            sections=dict(getattr(snippet, "sections", {}) or {}) or None,
            included_fields=list(getattr(snippet, "included_fields", []) or []),
            omitted_fields=list(getattr(snippet, "omitted_fields", []) or []),
            warnings=list(getattr(snippet, "warnings", []) or []),
        )


class _SQLiteStandaloneSessionClient:
    contract_version = CONTEXT_CLIENT_INTERFACE_VERSION

    def __init__(
        self, sessctl_db_path: Path, *, log_identity_events: bool = True
    ) -> None:
        self._db_path = sessctl_db_path
        self._log_identity_events = log_identity_events

    def get_slice(
        self,
        *,
        session_id: str,
        purpose: str,
        limits: dict[str, int],
    ) -> SessionSlice:
        if _SessctlSQLiteSessionStore is None:
            raise RuntimeError("openminion.modules.session is required for get_slice")

        store = _SessctlSQLiteSessionStore(self._db_path)

        sess_limits_kwargs = {}
        if "recent_turn_limit" in limits:
            sess_limits_kwargs["recent_turns"] = limits["recent_turn_limit"]
        if "tool_events_limit" in limits:
            sess_limits_kwargs["tool_events"] = limits["tool_events_limit"]

        sess_limits = (
            _SessctlSliceLimits(**sess_limits_kwargs) if _SessctlSliceLimits else None
        )

        raw_slice = store.get_slice(
            session_id=session_id,
            purpose=purpose,
            limits=sess_limits,
        )

        turns = []
        for t in raw_slice.get("recent_turns", []):
            role = str(t.get("turn_type", "user"))
            turns.append(
                SessionTurn(
                    turn_id=str(t.get("turn_id", "")),
                    role=role,
                    content=str(t.get("text", "")),
                    ts=str(t.get("ts", "")) if t.get("ts") else None,
                    is_error=bool(t.get("is_error")),
                )
            )

        tool_events = []
        for te in raw_slice.get("recent_tool_events", []):
            tool_events.append(
                SessionToolEvent(
                    event_id=str(te.get("event_id", "")),
                    tool_name=str(te.get("tool_name", "tool")),
                    excerpt=str(te.get("excerpt", "")),
                    artifact_refs=[str(r) for r in te.get("artifact_refs", [])],
                )
            )

        return SessionSlice(
            session_id=session_id,
            slice_version=str(raw_slice.get("slice_version", "")),
            last_event_id=str(raw_slice.get("last_event_id", ""))
            if raw_slice.get("last_event_id")
            else None,
            summary_short=str(raw_slice.get("summary_short", "")),
            summary_long=str(raw_slice.get("summary_long", ""))
            if raw_slice.get("summary_long")
            else None,
            conversation_summary=str(raw_slice.get("conversation_summary") or ""),
            active_task_plan=raw_slice.get("active_task_plan")
            if isinstance(raw_slice.get("active_task_plan"), dict)
            else None,
            task_digest=raw_slice.get("task_digest")
            if isinstance(raw_slice.get("task_digest"), dict)
            else None,
            pending_trailer_feedback=raw_slice.get("pending_trailer_feedback")
            if isinstance(raw_slice.get("pending_trailer_feedback"), dict)
            else None,
            total_turn_count=int(raw_slice.get("total_turn_count") or len(turns)),
            recent_turns=turns,
            open_tasks=[str(t) for t in raw_slice.get("open_tasks", [])],
            active_state=raw_slice.get(STATE_KEY_ACTIVE),
            recent_tool_events=tool_events,
            prompt_context_id=str(raw_slice.get("prompt_context_id", ""))
            if raw_slice.get("prompt_context_id")
            else None,
            checkpoint_id=str(raw_slice.get("checkpoint_id", ""))
            if raw_slice.get("checkpoint_id")
            else None,
            seed_bundle_id=str(raw_slice.get("seed_bundle_id", ""))
            if raw_slice.get("seed_bundle_id")
            else None,
            archive_refs=[str(ref) for ref in raw_slice.get("archive_refs", [])],
        )

    def bind_agent(
        self,
        *,
        session_id: str,
        agent_id: str,
        profile_version: str,
    ) -> None:
        if not self._log_identity_events:
            return
        if _SessctlSQLiteSessionStore is None:
            raise RuntimeError("openminion.modules.session is required for bind_agent")
        store = _SessctlSQLiteSessionStore(self._db_path)
        store.bind_agent(
            session_id=session_id,
            agent_id=agent_id,
            profile_version=profile_version,
        )

    def append_llm_request_started(
        self,
        *,
        session_id: str,
        purpose: str,
        profile_version: str,
        render_version: str,
        agent_id: str,
        slice_version: str,
        pack_version: str,
    ) -> None:
        if not self._log_identity_events:
            return
        if _SessctlSQLiteSessionStore is None:
            raise RuntimeError(
                "openminion.modules.session is required for event logging"
            )
        store = _SessctlSQLiteSessionStore(self._db_path)
        try:
            store.append_event(
                session_id=session_id,
                event_type="llm.request.started",
                payload={
                    "purpose": purpose,
                    "profile_version": profile_version,
                    "render_version": render_version,
                    "slice_version": slice_version,
                    "pack_version": pack_version,
                },
                agent_id=agent_id,
                status="started",
            )
        except ValueError:
            return


class _NullMemoryClient:
    contract_version = CONTEXT_CLIENT_INTERFACE_VERSION

    def query_facts(
        self,
        *,
        session_id: str,
        agent_id: str,
        query: str,
        limit: int,
        mode_name: str | None = None,
    ) -> list[FactRecord]:
        return []

    def query_memory_cards(
        self,
        *,
        session_id: str,
        agent_id: str,
        query: str,
        limit: int,
        mode_name: str | None = None,
    ) -> list[MemoryCard]:
        return []

    def recall_session_start_memory(
        self,
        *,
        session_id: str,
        agent_id: str,
        query: str,
        turn_index: int,
        limit: int,
        mode_name: str | None = None,
    ) -> list[MemoryCard]:
        return []

    def recall_mid_session_memory(
        self,
        *,
        session_id: str,
        agent_id: str,
        turn_index: int,
        intent_ids: list[str],
        intent_statuses: list[str],
        latest_user_message: str,
        active_skill_id: str | None,
        resolved_skill_ids: list[str],
        plan_cursor: int,
        plan_step_ids: list[str],
        recent_tool_families: list[str],
        limit: int,
        mode_name: str | None = None,
    ) -> list[MemoryCard]:
        return []

    def recall_recent_session_artifacts(
        self,
        *,
        session_id: str,
        agent_id: str,
        max_results: int,
        max_session_age: int,
        mode_name: str | None = None,
    ) -> list[RecentSessionArtifactRef]:
        return []

    def get_procedure(self, *, procedure_id: str):
        return None


class _NullArtifactClient:
    contract_version = CONTEXT_CLIENT_INTERFACE_VERSION

    def query_digests(
        self,
        *,
        session_id: str,
        agent_id: str,
        query: str,
        limit: int,
    ) -> list[ArtifactDigest]:
        return []


class ContextPackBuilder:
    """Standalone builder wiring for local sessctl sqlite."""

    def __init__(
        self,
        sessctl_db_path: str | Path,
        *,
        identity_client: IdentityClient | None = None,
        log_identity_events: bool = True,
    ) -> None:
        self._db_path = _resolve_db_path(sessctl_db_path)
        self._sess_client = _SQLiteStandaloneSessionClient(
            self._db_path,
            log_identity_events=log_identity_events,
        )
        self._service = ContextCtlService(
            identityctl=identity_client or _StaticIdentityClient(),
            sessctl=self._sess_client,
            memctl=_NullMemoryClient(),
            artifactctl=_NullArtifactClient(),
        )

    @staticmethod
    def identity_client_from_service(service: Any) -> IdentityClient:
        return _IdentityctlCoreClient(service)

    @property
    def sessctl_db_path(self) -> Path:
        return self._db_path

    def build(self, options: BuildOptions) -> dict[str, Any]:
        pack: ContextPack = self._service.build_pack(
            BuildPackRequest(
                session_id=options.session_id,
                agent_id=options.agent_id,
                purpose=options.purpose,  # type: ignore[arg-type]
                query=options.user_input,
                provider_pref=options.provider_pref,
                constraints=BuildConstraints(**(options.constraints or {})),
            )
        )
        return pack.model_dump()
