from dataclasses import dataclass, field
from typing import Sequence

from openminion.base.config.env import resolve_environment_config

from .constants import (
    COMPRESS_PROVIDER_PREFERENCE_ALLOWED,
    COMPRESS_PROVIDER_PREFERENCE_DEFAULT,
    COMPRESS_PROVIDER_PREFERENCE_ENV,
    COMPRESS_PROVIDER_PREFERENCE_TO_METHOD_ID,
    DEFAULT_QUALITY_OVERRIDES,
)
from .registry import MethodRegistry
from .schemas import CompressionPolicy, CompressionRequest


def resolve_compress_provider_preference() -> str:
    """Return the operator's compress-provider preference."""

    env = resolve_environment_config()
    raw = env.get(COMPRESS_PROVIDER_PREFERENCE_ENV, "").strip().lower()
    if raw in COMPRESS_PROVIDER_PREFERENCE_ALLOWED:
        return raw
    return COMPRESS_PROVIDER_PREFERENCE_DEFAULT


@dataclass(frozen=True)
class MethodResolution:
    """Result of resolving prepass/main/fallback methods."""

    prepass_method: str | None
    main_method: str
    fallback_method: str
    fallback_used: bool
    attempted_methods: Sequence[str] = field(default_factory=tuple)
    unavailable_methods: Sequence[str] = field(default_factory=tuple)
    warnings: Sequence[str] = field(default_factory=tuple)


class PolicyResolver:
    """Deterministically resolve compressor methods per policy rules."""

    def __init__(
        self,
        registry: MethodRegistry,
        *,
        quality_overrides: dict[str, str | None] | None = None,
    ) -> None:
        self._registry = registry
        self._quality_overrides = {
            **DEFAULT_QUALITY_OVERRIDES,
            **(quality_overrides or {}),
        }

    def resolve(
        self,
        request: CompressionRequest,
        *,
        override_main: str | None = None,
    ) -> MethodResolution:
        policy = request.policy
        prepass_method, warnings = self._resolve_prepass(policy)
        main_method, fallback_used, attempted, unavailable = self._resolve_main(
            policy,
            request.retrieval_quality_hint,
            override_main,
        )
        fallback_method = self._resolve_fallback(policy)
        return MethodResolution(
            prepass_method=prepass_method,
            main_method=main_method,
            fallback_method=fallback_method,
            fallback_used=fallback_used,
            attempted_methods=tuple(attempted),
            unavailable_methods=tuple(unavailable),
            warnings=tuple(warnings),
        )

    # Internal helpers -----------------------------------------------------
    def _resolve_prepass(self, policy: CompressionPolicy) -> tuple[str | None, list[str]]:
        warnings: list[str] = []
        method_id = policy.method_prepass
        if not method_id or method_id.lower() == "none":
            return None, warnings
        if not self._registry.is_prepass_available(method_id):
            warnings.append(f"prepass_unavailable:{method_id}")
            return None, warnings
        return method_id, warnings

    def _resolve_main(
        self,
        policy: CompressionPolicy,
        quality_hint: str | None,
        override_main: str | None,
    ) -> tuple[str, bool, list[str], list[str]]:
        attempted: list[str] = []
        unavailable: list[str] = []
        chain: list[tuple[str, str | None]] = []
        if override_main:
            chain.append(("override", override_main))
        preference = resolve_compress_provider_preference()
        preference_method = COMPRESS_PROVIDER_PREFERENCE_TO_METHOD_ID.get(preference)
        if preference_method:
            chain.append(("operator_preference", preference_method))
        if policy.method_main:
            chain.append(("policy", policy.method_main))
        tier_candidate = self._quality_overrides.get(quality_hint or "")
        if tier_candidate:
            chain.append(("quality_tier", tier_candidate))
        fallback_candidate = (
            policy.fallback_method_id or self._registry.baseline_method_id
        )
        chain.append(("fallback", fallback_candidate))
        chain.append(("baseline", self._registry.baseline_method_id))

        seen: set[str] = set()
        for source, candidate in chain:
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            attempted.append(candidate)
            if self._registry.is_main_available(candidate):
                fallback_used = source not in ("override", "policy")
                return candidate, fallback_used, attempted, unavailable
            unavailable.append(candidate)

        baseline = self._registry.baseline_method_id
        if baseline not in seen:
            attempted.append(baseline)
        return baseline, True, attempted, unavailable

    def _resolve_fallback(self, policy: CompressionPolicy) -> str:
        fallback_candidate = (
            policy.fallback_method_id or self._registry.baseline_method_id
        )
        if self._registry.is_main_available(fallback_candidate):
            return fallback_candidate
        return self._registry.baseline_method_id
