from typing import TYPE_CHECKING, Any, ClassVar, Protocol, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover — typing only
    from .schemas import (
        MetaDirective,
        RetrievedContext,
        RLMBudgets,
        RLMConfig,
        RLMConstraints,
        RLMResponse,
        RetrievalFilters,
        TaskState,
        WMState,
    )


RLM_CONTRACT_VERSION = "v1"
RLM_INTERFACE_VERSION = "v1"


@runtime_checkable
class SessionClient(Protocol):
    def get_latest_working_state(self, session_id: str) -> dict[str, Any] | None: ...

    def put_working_state(
        self,
        session_id: str,
        *,
        state_ref: str | None = None,
        state_inline: dict[str, Any] | None = None,
    ) -> int: ...

    def append_event(
        self,
        session_id: str,
        type: str | None = None,
        payload: dict[str, Any] | None = None,
        *,
        event_type: str | None = None,
        agent_id: str | None = None,
        trace_id: str | None = None,
        task_id: str | None = None,
        artifact_refs: list[str] | None = None,
        memory_refs: list[str] | None = None,
        status: str | None = None,
        error: dict[str, Any] | None = None,
    ) -> str: ...

    def list_events(
        self,
        session_id: str,
        *,
        event_type: str | None = None,
        trace_id: str | None = None,
        agent_id: str | None = None,
        status: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]: ...

    def get_slice(
        self,
        session_id: str,
        purpose: str,
        limits: dict[str, Any],
    ) -> dict[str, Any]: ...


@runtime_checkable
class ContextClient(Protocol):
    def build_pack(self, request: Any) -> Any: ...


@runtime_checkable
class RetrievalClient(Protocol):
    def retrieve(
        self,
        *,
        query: str,
        purpose: str,
        scope: dict[str, Any],
        k: int,
        strategy: str,
        filters: dict[str, Any] | None = None,
    ) -> list[Any]: ...

    def expand(
        self,
        *,
        ref: str,
        mode: str,
        k: int,
    ) -> list[Any]: ...


@runtime_checkable
class CompressionClient(Protocol):
    def compress(
        self,
        *,
        blocks: list[dict[str, Any]],
        query: str,
        budgets: dict[str, Any],
        policy: dict[str, Any],
    ) -> Any: ...


@runtime_checkable
class LLMClient(Protocol):
    def call_for_agent(
        self,
        agent_id: str,
        purpose: str,
        request: dict[str, Any],
        agent_policy: dict[str, Any],
    ) -> Any: ...


@runtime_checkable
class ArtifactClient(Protocol):
    def list_recent(
        self, limit: int = 50, scope_filters: dict[str, Any] | None = None
    ) -> list[Any]: ...

    def read_bytes(self, ref_or_sha: str) -> bytes: ...

    def ingest_bytes(
        self,
        data: bytes,
        mime: str | None = None,
        original_name: str | None = None,
        label: str | None = None,
        meta: dict[str, Any] | None = None,
        session_id: str | None = None,
        trace_id: str | None = None,
        agent_id: str | None = None,
    ) -> Any: ...


@runtime_checkable
class MemoryClient(Protocol):
    def retrieve(
        self,
        *,
        session_id: str,
        agent_id: str,
        query: str,
        k: int,
        filters: dict[str, Any] | None = None,
    ) -> list[Any]: ...

    def query_facts(
        self,
        *,
        session_id: str,
        agent_id: str,
        query: str,
        limit: int,
    ) -> list[Any]: ...

    def stage_candidate(
        self,
        *,
        scope: str,
        record_type: str,
        title: str,
        content: dict[str, Any] | str,
        tags: list[str] | None = None,
        evidence_refs: list[str] | None = None,
    ) -> str: ...


@runtime_checkable
class SkillClient(Protocol):
    def match(
        self,
        intent_text: str,
        step_hint: dict[str, Any] | None,
        agent_id: str,
        k: int = 3,
        status_filter: list[str] | str | None = None,
    ) -> list[Any]: ...

    def render_snippet(
        self,
        skill_id: str,
        version_hash: str | None,
        purpose: str,
        max_tokens: int,
    ) -> tuple[str, str]: ...


class RLMServiceInterface(Protocol):
    """Recursive loop service interface contract."""

    contract_version: ClassVar[str] = RLM_INTERFACE_VERSION

    def __init__(
        self,
        *,
        sessctl: Any,
        contextctl: Any | None = None,
        llmctl: Any,
        artifactctl: Any | None = None,
        memctl: Any | None = None,
        skillctl: Any | None = None,
        retrievectl: Any | None = None,
        compressctl: Any | None = None,
        config: "RLMConfig | dict[str, Any] | None" = None,
    ) -> None: ...

    def generate(
        self,
        *,
        session_id: str,
        agent_id: str,
        purpose: str,
        query: str,
        ts: "TaskState | dict[str, Any] | None" = None,
        budgets: "RLMBudgets | dict[str, Any] | None" = None,
        constraints: "RLMConstraints | dict[str, Any] | None" = None,
        meta_directive: "MetaDirective | dict[str, Any] | None" = None,
        agent_policy: dict[str, Any] | None = None,
    ) -> "RLMResponse": ...

    def refresh_working_memory(
        self, session_id: str, agent_id: str, reason: str
    ) -> "WMState": ...

    def retrieve(
        self,
        *,
        session_id: str,
        agent_id: str,
        query: str,
        k: int,
        purpose: str = "act",
        strategy: Any = "auto",  # RetrievalStrategy
        filters: "RetrievalFilters | dict[str, Any] | None" = None,
    ) -> list["RetrievedContext"]: ...

    def expand(self, ref: str, mode: str, k: int) -> list["RetrievedContext"]: ...


def ensure_rlm_compatibility(
    rlm_service: Any, strict: bool = True
) -> tuple[bool, list[str]]:
    """Validate RLM service implements the required interface."""
    errors = []

    if not hasattr(rlm_service, "contract_version"):
        errors.append("Missing contract_version attribute")
    elif rlm_service.contract_version != RLM_INTERFACE_VERSION:
        errors.append(
            f"Version mismatch: expected {RLM_INTERFACE_VERSION}, "
            f"got {rlm_service.contract_version}"
        )

    required_methods = ["generate", "refresh_working_memory", "retrieve", "expand"]

    for method in required_methods:
        if not hasattr(rlm_service, method) or not callable(
            getattr(rlm_service, method)
        ):
            errors.append(f"Missing required method: {method}")

    if errors:
        if strict:

            class RlmError(Exception):
                def __init__(self, code, message):
                    self.code = code
                    self.message = message

            raise RlmError(
                "RLM_SERVICE_INTERFACE_VIOLATION", f"RLM service incompatible: {errors}"
            )
        return False, errors

    return True, []
