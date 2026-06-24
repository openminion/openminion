from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from openminion.modules.memory.interfaces import MEMORY_INTERFACE_VERSION
from openminion.modules.memory.models import MemoryCandidate
from openminion.modules.memory.runtime.promotion import PromotionPolicy


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason_code: str
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RetrievalDecision:
    limit: int | None
    reason_code: str
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class PromotionPolicyEngine(Protocol):
    contract_version: str

    def evaluate(
        self, candidate: MemoryCandidate, target_scope: str
    ) -> PolicyDecision: ...


@runtime_checkable
class RetrievalPolicyEngine(Protocol):
    contract_version: str

    def resolve_limit(self, *, requested_limit: int | None) -> RetrievalDecision: ...


@runtime_checkable
class CapsuleRefreshPolicyEngine(Protocol):
    contract_version: str

    def should_refresh(
        self,
        *,
        strategy: str,
        has_cached_capsule: bool,
        memory_changed: bool,
    ) -> PolicyDecision: ...


@runtime_checkable
class RetentionPolicyEngine(Protocol):
    contract_version: str

    def should_collect_garbage(
        self,
        *,
        gc_enabled: bool,
        pending_records: int,
    ) -> PolicyDecision: ...


class DefaultPromotionPolicyEngine:
    contract_version = MEMORY_INTERFACE_VERSION

    def __init__(self, policy: PromotionPolicy | None = None) -> None:
        self._policy = policy or PromotionPolicy()

    def evaluate(self, candidate: MemoryCandidate, target_scope: str) -> PolicyDecision:
        result = self._policy.evaluate(candidate, target_scope)
        if result.allowed:
            return PolicyDecision(
                allowed=True,
                reason_code="promotion_allowed",
                reason=str(result.reason or ""),
                metadata={
                    "source": str(candidate.source or ""),
                    "target_scope": str(target_scope or ""),
                },
            )
        if str(target_scope or "").startswith("global:"):
            reason_code = "promotion_denied_global_scope_requires_approval"
        elif str(target_scope or "").startswith("project:"):
            reason_code = "promotion_denied_project_scope_requires_approval"
        else:
            reason_code = "promotion_denied_source_requires_approval"
        return PolicyDecision(
            allowed=False,
            reason_code=reason_code,
            reason=str(result.reason or "Promotion denied by policy"),
            metadata={
                "source": str(candidate.source or ""),
                "target_scope": str(target_scope or ""),
            },
        )


class DefaultRetrievalPolicyEngine:
    contract_version = MEMORY_INTERFACE_VERSION

    def __init__(self, *, max_results: int = 0) -> None:
        self._max_results = max(0, int(max_results))

    def resolve_limit(self, *, requested_limit: int | None) -> RetrievalDecision:
        if requested_limit is not None:
            resolved = max(1, int(requested_limit))
        else:
            resolved = None
        if self._max_results <= 0:
            return RetrievalDecision(
                limit=resolved,
                reason_code="retrieval_limit_passthrough",
                metadata={
                    "requested_limit": requested_limit,
                    "max_results": self._max_results,
                },
            )
        if resolved is None:
            return RetrievalDecision(
                limit=self._max_results,
                reason_code="retrieval_limit_defaulted",
                metadata={
                    "requested_limit": requested_limit,
                    "max_results": self._max_results,
                },
            )
        if resolved > self._max_results:
            return RetrievalDecision(
                limit=self._max_results,
                reason_code="retrieval_limit_capped",
                metadata={
                    "requested_limit": requested_limit,
                    "max_results": self._max_results,
                },
            )
        return RetrievalDecision(
            limit=resolved,
            reason_code="retrieval_limit_passthrough",
            metadata={
                "requested_limit": requested_limit,
                "max_results": self._max_results,
            },
        )


class DefaultCapsuleRefreshPolicyEngine:
    contract_version = MEMORY_INTERFACE_VERSION

    def should_refresh(
        self,
        *,
        strategy: str,
        has_cached_capsule: bool,
        memory_changed: bool,
    ) -> PolicyDecision:
        normalized = str(strategy or "").strip().lower()
        if normalized == "off":
            return PolicyDecision(
                allowed=False,
                reason_code="refresh_disabled",
                reason="Capsule refresh policy is disabled",
            )
        if normalized == "dynamic_turn":
            return PolicyDecision(
                allowed=True,
                reason_code="refresh_each_turn",
                reason="Capsule should refresh every turn",
            )
        if normalized == "refresh_on_write":
            if not has_cached_capsule:
                return PolicyDecision(
                    allowed=True,
                    reason_code="refresh_missing_capsule",
                    reason="Capsule missing; refresh required",
                )
            if memory_changed:
                return PolicyDecision(
                    allowed=True,
                    reason_code="refresh_on_memory_change",
                    reason="Memory changed during session",
                )
            return PolicyDecision(
                allowed=False,
                reason_code="refresh_skipped_no_change",
                reason="No memory changes since last capsule",
            )
        # frozen_session default
        if has_cached_capsule:
            return PolicyDecision(
                allowed=False,
                reason_code="refresh_skipped_frozen_session",
                reason="Frozen session capsule should remain stable",
            )
        return PolicyDecision(
            allowed=True,
            reason_code="refresh_missing_capsule",
            reason="No capsule cached for session",
        )


class DefaultRetentionPolicyEngine:
    contract_version = MEMORY_INTERFACE_VERSION

    def should_collect_garbage(
        self,
        *,
        gc_enabled: bool,
        pending_records: int,
    ) -> PolicyDecision:
        if not gc_enabled:
            return PolicyDecision(
                allowed=False,
                reason_code="retention_gc_disabled",
                reason="Garbage collection disabled by policy",
            )
        if int(pending_records) <= 0:
            return PolicyDecision(
                allowed=False,
                reason_code="retention_gc_noop",
                reason="No records pending retention cleanup",
            )
        return PolicyDecision(
            allowed=True,
            reason_code="retention_gc_eligible",
            reason="Retention cleanup is eligible",
            metadata={"pending_records": max(0, int(pending_records))},
        )


@dataclass(frozen=True)
class PolicyEngineBundle:
    promotion: PromotionPolicyEngine
    retrieval: RetrievalPolicyEngine
    capsule_refresh: CapsuleRefreshPolicyEngine
    retention: RetentionPolicyEngine


def build_default_policy_engine_bundle(config: Any | None = None) -> PolicyEngineBundle:
    retrieval_cfg: dict[str, Any] = {}
    promotion_cfg: dict[str, Any] = {}
    if isinstance(config, dict):
        retrieval_cfg = (
            config.get("retrieval") if isinstance(config.get("retrieval"), dict) else {}
        )
        promotion_cfg = (
            config.get("promotion") if isinstance(config.get("promotion"), dict) else {}
        )
        if isinstance(config.get("policy"), dict):
            policy_cfg = config.get("policy") or {}
            if isinstance(policy_cfg.get("retrieval"), dict):
                retrieval_cfg = policy_cfg.get("retrieval") or retrieval_cfg
            if isinstance(policy_cfg.get("promotion"), dict):
                promotion_cfg = policy_cfg.get("promotion") or promotion_cfg
    else:
        retrieval = getattr(config, "retrieval", None)
        if retrieval is not None:
            retrieval_cfg = {
                "max_results": getattr(retrieval, "max_results", None),
            }
        promotion = getattr(config, "promotion", None)
        if promotion is not None:
            promotion_cfg = {
                "auto_promote_agent_procedures": getattr(
                    promotion, "auto_promote_agent_procedures", False
                )
            }

    max_results_raw = retrieval_cfg.get("max_results")
    max_results = 0
    if max_results_raw is not None:
        try:
            max_results = max(0, int(max_results_raw))
        except (TypeError, ValueError):
            max_results = 0

    auto_promote_sources = {"system", "validated"}
    if bool(promotion_cfg.get("auto_promote_agent_procedures", False)):
        auto_promote_sources.add("agent_inferred")

    return PolicyEngineBundle(
        promotion=DefaultPromotionPolicyEngine(
            policy=PromotionPolicy(auto_promote_sources=auto_promote_sources)
        ),
        retrieval=DefaultRetrievalPolicyEngine(max_results=max_results),
        capsule_refresh=DefaultCapsuleRefreshPolicyEngine(),
        retention=DefaultRetentionPolicyEngine(),
    )


__all__ = [
    "PolicyDecision",
    "RetrievalDecision",
    "PromotionPolicyEngine",
    "RetrievalPolicyEngine",
    "CapsuleRefreshPolicyEngine",
    "RetentionPolicyEngine",
    "DefaultPromotionPolicyEngine",
    "DefaultRetrievalPolicyEngine",
    "DefaultCapsuleRefreshPolicyEngine",
    "DefaultRetentionPolicyEngine",
    "PolicyEngineBundle",
    "build_default_policy_engine_bundle",
]
