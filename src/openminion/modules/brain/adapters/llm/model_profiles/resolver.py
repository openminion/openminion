import logging
from dataclasses import replace
from typing import Any, Mapping

from .defaults import _DEFAULT_PROFILES, _PROFILE_FIELD_NAMES
from .capabilities import DecisionStrategy, ModelCapabilityProfile

logger = logging.getLogger(__name__)


def default_capability_profiles() -> tuple[ModelCapabilityProfile, ...]:
    return _DEFAULT_PROFILES


def resolve_capability_profile(
    *,
    model_name: str,
    overrides: tuple[ModelCapabilityProfile, ...] = (),
) -> ModelCapabilityProfile:
    normalized_model = str(model_name or "").strip().lower()
    for profile in (*overrides, *_DEFAULT_PROFILES):
        if _profile_matches_model(profile=profile, normalized_model=normalized_model):
            return profile
    return _DEFAULT_PROFILES[-1]


def build_overrides_from_config(
    raw: dict[str, dict[str, Any]],
) -> tuple[ModelCapabilityProfile, ...]:
    if not isinstance(raw, dict):
        return ()

    default_by_id = {profile.profile_id: profile for profile in _DEFAULT_PROFILES}
    built: list[ModelCapabilityProfile] = []
    for profile_id, payload in raw.items():
        normalized_profile_id = str(profile_id or "").strip()
        if not normalized_profile_id:
            logger.warning(
                "model_capability_overrides: skipping empty profile id entry"
            )
            continue
        if not isinstance(payload, dict):
            logger.warning(
                "model_capability_overrides[%s]: expected object payload",
                normalized_profile_id,
            )
            continue
        normalized = _normalize_override_payload(
            profile_id=normalized_profile_id,
            payload=payload,
        )
        if normalized is None:
            continue

        base = default_by_id.get(normalized_profile_id)
        try:
            if base is not None:
                built.append(replace(base, **normalized))
                continue
            if not normalized.get("model_fragments"):
                logger.warning(
                    "model_capability_overrides[%s]: custom profiles must declare "
                    "model_fragments",
                    normalized_profile_id,
                )
                continue
            built.append(
                ModelCapabilityProfile(
                    profile_id=normalized_profile_id,
                    **normalized,
                )
            )
        except Exception as exc:
            logger.warning(
                "model_capability_overrides[%s]: invalid override skipped (%s)",
                normalized_profile_id,
                exc,
            )
    return tuple(built)


def resolve_capability_profile_for_context(
    *,
    model_name: str,
    context: Mapping[str, Any] | None,
) -> ModelCapabilityProfile:
    hints = (
        dict(context.get("hints") or {})
        if isinstance(context, Mapping) and isinstance(context.get("hints"), Mapping)
        else {}
    )
    raw_overrides = (
        dict(hints.get("model_capability_overrides") or {})
        if isinstance(hints.get("model_capability_overrides"), Mapping)
        else {}
    )
    return resolve_capability_profile(
        model_name=model_name,
        overrides=build_overrides_from_config(raw_overrides),
    )


def capability_profile_id_for_model_name(
    *,
    model_name: str,
    overrides: tuple[ModelCapabilityProfile, ...] = (),
) -> str:
    return resolve_capability_profile(
        model_name=model_name,
        overrides=overrides,
    ).profile_id


def _normalize_override_payload(
    *,
    profile_id: str,
    payload: Mapping[str, Any],
) -> dict[str, Any] | None:
    unknown_fields = sorted(set(payload.keys()).difference(_PROFILE_FIELD_NAMES))
    if unknown_fields:
        logger.warning(
            "model_capability_overrides[%s]: unknown fields skipped: %s",
            profile_id,
            ", ".join(unknown_fields),
        )
        return None

    normalized = dict(payload)
    normalized.pop("profile_id", None)
    if "model_fragments" in normalized:
        normalized["model_fragments"] = tuple(
            fragment
            for fragment in (
                str(item or "").strip().lower()
                for item in (normalized.get("model_fragments") or [])
            )
            if fragment
        )
    if "extraction_chain" in normalized:
        normalized["extraction_chain"] = tuple(
            item
            for item in (
                str(entry or "").strip()
                for entry in (normalized.get("extraction_chain") or [])
            )
            if item
        )
    if "decision_strategy" in normalized:
        normalized["decision_strategy"] = (
            str(normalized.get("decision_strategy") or "").strip()
            or DecisionStrategy.FULL_SCHEMA
        )
    if "retry_strategy" in normalized:
        normalized["retry_strategy"] = str(
            normalized.get("retry_strategy") or ""
        ).strip()
    if "retry_nudge_style" in normalized:
        normalized["retry_nudge_style"] = str(
            normalized.get("retry_nudge_style") or ""
        ).strip()
    if "max_structured_retries" in normalized:
        try:
            normalized["max_structured_retries"] = max(
                1, int(normalized.get("max_structured_retries") or 1)
            )
        except Exception:
            logger.warning(
                "model_capability_overrides[%s]: invalid max_structured_retries",
                profile_id,
            )
            return None
    return normalized


def _profile_matches_model(
    *,
    profile: ModelCapabilityProfile,
    normalized_model: str,
) -> bool:
    if not profile.model_fragments:
        return profile.profile_id == "fallback"
    if not normalized_model:
        return False
    return any(fragment in normalized_model for fragment in profile.model_fragments)
