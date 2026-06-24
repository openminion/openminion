from typing import Any, ClassVar, Protocol, TYPE_CHECKING

from .contracts import (
    CONTEXT_CLIENT_INTERFACE_VERSION,
    ensure_context_client_compatibility,
)

if TYPE_CHECKING:
    from .schemas import (
        BuildPackRequest,
        ContextManifest,
        ContextPack,
        FactRecord,
        MemoryCard,
        ArtifactDigest,
        RenderMessage,
        SummaryDelta,
        TokenReport,
    )


CONTEXT_INTERFACE_VERSION = CONTEXT_CLIENT_INTERFACE_VERSION


class ContextCtlInterface(Protocol):
    """ContextCtlService public interface contract."""

    contract_version: ClassVar[str] = CONTEXT_INTERFACE_VERSION

    def build_pack(self, request: "BuildPackRequest") -> "ContextPack": ...

    def record_cache_metrics(
        self,
        *,
        session_id: str,
        cache_hit: bool,
        prefix_token_count: int,
        full_token_count: int,
    ) -> None: ...

    def make_delta(
        self,
        *,
        session_id: str,
        base_summary: str,
        delta_text: str,
    ) -> "SummaryDelta": ...

    def maybe_compact(self, session_id: str, *, threshold: int = 5) -> bool: ...

    def get_summary_base(self, session_id: str) -> str | None: ...

    def get_summary_deltas(self, session_id: str) -> list["SummaryDelta"]: ...

    def render_fact_table(self, facts: list["FactRecord"], max_tokens: int) -> str: ...

    def render_memory_cards(
        self, records: list["MemoryCard"], max_tokens: int
    ) -> str: ...

    def render_artifact_digest(
        self, digest: "ArtifactDigest", max_tokens: int
    ) -> str: ...

    def render_procedure_snippet(self, proc: Any, max_tokens: int) -> str: ...

    def estimate_tokens(self, messages: list["RenderMessage"]) -> "TokenReport": ...

    def explain_pack(self, pack_version: str) -> "ContextManifest | None": ...


__all__ = [
    "CONTEXT_INTERFACE_VERSION",
    "ContextCtlInterface",
    "ensure_context_client_compatibility",
]
