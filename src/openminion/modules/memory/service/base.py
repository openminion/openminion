"""Memory service owner."""

# mypy: disable-error-code="attr-defined,redundant-cast,no-any-return"

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, cast

from openminion.modules.memory.backends.builtin import adapt_backend_to_store
from openminion.modules.memory.backends.interfaces import KnowledgeBackend
from openminion.modules.memory.errors import InvalidArgumentError
from openminion.modules.memory.models import MemoryScope
from openminion.modules.memory.runtime.policies import (
    CapsuleRefreshPolicyEngine,
    PolicyDecision,
    PromotionPolicyEngine,
    RetentionPolicyEngine,
    RetrievalPolicyEngine,
    build_default_policy_engine_bundle,
)
from openminion.modules.memory.interfaces import MEMORY_INTERFACE_VERSION
from openminion.modules.memory.runtime.promotion import PromotionPolicy
from openminion.modules.memory.runtime.candidate_lifecycle import (
    MemoryCandidateLifecycle,
)
from openminion.modules.memory.storage.base import MemoryStore
from openminion.modules.memory.portability.service import (
    MemoryBundleServiceOps,
)
from openminion.modules.memory.diagnostics.events import emit_query_metrics
from .mutations import MemoryServiceMutationMixin
from .queries import MemoryServiceQueryMixin


class MemoryService(MemoryServiceQueryMixin, MemoryServiceMutationMixin):
    """Facade combining storage, policy engines, and validation."""

    contract_version = MEMORY_INTERFACE_VERSION
    _FORGET_PAGE_SIZE = MemoryBundleServiceOps._FORGET_PAGE_SIZE

    def __init__(
        self,
        store: MemoryStore | None = None,
        policy: PromotionPolicy | PromotionPolicyEngine | None = None,
        vector_adapter: Any = None,
        *,
        backend: KnowledgeBackend | None = None,
        retrieval_policy: RetrievalPolicyEngine | None = None,
        capsule_refresh_policy: CapsuleRefreshPolicyEngine | None = None,
        retention_policy: RetentionPolicyEngine | None = None,
        policy_config: Any | None = None,
        ranking_config: Any | None = None,
        telemetryctl: Any | None = None,
        telemetry_session_id: str | None = None,
        telemetry_turn_id: str | None = None,
    ) -> None:
        if store is not None and backend is not None:
            raise InvalidArgumentError(
                "MemoryService accepts either store or backend, not both"
            )
        if backend is not None:
            store = adapt_backend_to_store(backend)
        if store is None:
            raise InvalidArgumentError(
                "MemoryService requires a store or backend implementation"
            )
        self._backend = backend
        self._store = store
        self._vector_adapter = vector_adapter
        defaults = build_default_policy_engine_bundle(policy_config)
        self._promotion_policy = self._coerce_promotion_policy(
            policy=policy,
            default=defaults.promotion,
        )
        self._retrieval_policy = retrieval_policy or defaults.retrieval
        self._capsule_refresh_policy = (
            capsule_refresh_policy or defaults.capsule_refresh
        )
        self._retention_policy = retention_policy or defaults.retention
        self._policy_decisions: list[dict[str, Any]] = []
        self._ranking_config = ranking_config
        self._tiering_config: Any | None = None
        self._telemetryctl = telemetryctl
        self._telemetry_session_id = str(telemetry_session_id or "").strip() or None
        self._telemetry_turn_id = str(telemetry_turn_id or "").strip() or None
        self._bundle_ops = MemoryBundleServiceOps(self)
        self._candidate_lifecycle = MemoryCandidateLifecycle(self)

    def _bundle_helper(self) -> MemoryBundleServiceOps:
        helper = getattr(self, "_bundle_ops", None)
        if helper is None:
            helper = MemoryBundleServiceOps(self)
            self._bundle_ops = helper
        return cast(MemoryBundleServiceOps, helper)

    def _candidate_helper(self) -> MemoryCandidateLifecycle:
        helper = getattr(self, "_candidate_lifecycle", None)
        if helper is None:
            helper = MemoryCandidateLifecycle(self)
            self._candidate_lifecycle = helper
        return cast(MemoryCandidateLifecycle, helper)

    def set_telemetry_context(
        self,
        *,
        session_id: str,
        turn_id: str,
    ) -> None:
        self._telemetry_session_id = str(session_id or "").strip() or None
        self._telemetry_turn_id = str(turn_id or "").strip() or None

    def _resolve_telemetry_session_id(
        self,
        *,
        scopes: list[str] | None = None,
    ) -> str:
        if self._telemetry_session_id:
            return str(self._telemetry_session_id)
        for scope in scopes or []:
            normalized = str(scope or "").strip()
            if not normalized:
                continue
            try:
                parsed = MemoryScope.parse(normalized)
            except ValueError:
                return normalized
            if parsed.is_session:
                return parsed.value
            return str(parsed)
        return ""

    def _resolve_telemetry_turn_id(self) -> str:
        return str(self._telemetry_turn_id or "").strip()

    def _emit_query_metrics(
        self,
        *,
        session_id: str,
        turn_id: str,
        operation: str,
        result_count: int,
        latency_ms: float,
        token_estimate: int,
        status: str = "ok",
        extra: dict[str, Any] | None = None,
    ) -> None:
        emit_query_metrics(
            telemetryctl=self._telemetryctl,
            session_id=session_id,
            turn_id=turn_id,
            operation=operation,
            result_count=result_count,
            latency_ms=latency_ms,
            token_estimate=token_estimate,
            status=status,
            extra=extra,
        )

    def _coerce_promotion_policy(
        self,
        *,
        policy: PromotionPolicy | PromotionPolicyEngine | None,
        default: PromotionPolicyEngine,
    ) -> PromotionPolicyEngine:
        if policy is None:
            return default
        if isinstance(policy, PromotionPolicy):
            from openminion.modules.memory.runtime.policies import (
                DefaultPromotionPolicyEngine,
            )

            return DefaultPromotionPolicyEngine(policy=policy)
        if hasattr(policy, "evaluate"):
            return cast(PromotionPolicyEngine, policy)
        return default

    def _record_policy_decision(
        self,
        *,
        lane: str,
        decision: PolicyDecision | Any,
    ) -> None:
        reason_code = str(getattr(decision, "reason_code", "") or "").strip()
        reason = str(getattr(decision, "reason", "") or "").strip()
        allowed = bool(getattr(decision, "allowed", True))
        metadata = getattr(decision, "metadata", {}) or {}
        if not isinstance(metadata, dict):
            metadata = {}
        self._policy_decisions.append(
            {
                "lane": lane,
                "allowed": allowed,
                "reason_code": reason_code,
                "reason": reason,
                "metadata": dict(metadata),
                "at": datetime.now(timezone.utc).isoformat(),
            }
        )
        if len(self._policy_decisions) > 64:
            self._policy_decisions = self._policy_decisions[-64:]

    def last_policy_decisions(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._policy_decisions]

    def set_vector_adapter(self, vector_adapter: Any) -> None:
        """Set or update the vector adapter for semantic search."""
        self._vector_adapter = vector_adapter

    def set_ranking_config(self, ranking_config: Any | None) -> None:
        self._ranking_config = ranking_config

    def set_candidate_learning_config(self, config: Any | None) -> None:
        """Attach the candidate-learning config used by `reinforce_candidate`."""
        self._candidate_learning_config = config

    def set_tiering_config(self, config: Any | None) -> None:
        self._tiering_config = config
