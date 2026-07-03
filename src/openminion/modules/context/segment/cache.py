"""Cache metadata helpers for context segments."""

from typing import Any


def segment_cache_fields(bucket: str, content_hash: str) -> dict[str, Any]:
    if bucket != "static_prefix" or not content_hash:
        return {"cache_key": "", "cache_invalidation_refs": []}
    return {
        "cache_key": f"{bucket}:{content_hash}",
        "cache_invalidation_refs": [f"content_hash:{content_hash}"],
    }


def segment_render_cache_metadata(segment: Any) -> dict[str, Any]:
    return {
        "cache_key": str(getattr(segment, "cache_key", "") or ""),
        "cache_invalidation_refs": list(
            getattr(segment, "cache_invalidation_refs", []) or []
        ),
    }
