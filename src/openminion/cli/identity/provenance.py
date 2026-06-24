from __future__ import annotations

from typing import Any


def classify_profile_source(profile: Any) -> str:
    if profile is None:
        return "missing"
    meta = dict(getattr(profile, "meta", {}) or {})
    explicit_source = str(meta.get("source", "") or "").strip().lower()
    if explicit_source:
        return explicit_source
    if str(meta.get("bundle_fingerprint", "") or "").strip():
        return "legacy-bundle"
    return "legacy-protected"


def build_identity_provenance(profile: Any) -> dict[str, Any]:
    meta = dict(getattr(profile, "meta", {}) or {}) if profile is not None else {}
    explicit_source = str(meta.get("source", "") or "").strip().lower()
    source_classification = classify_profile_source(profile)
    return {
        "meta_source": explicit_source,
        "source_classification": source_classification,
        "source_refreshable_by_bundle": source_classification
        in {"bundle", "legacy-bundle"},
    }
