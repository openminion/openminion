"""Cache metadata helpers for context segments."""

from ..schemas import ContextSegment


def segment_cache_fields(bucket: str, content_hash: str) -> dict[str, object]:
    if bucket != "static_prefix" or not content_hash:
        return {"cache_key": "", "cache_invalidation_refs": []}
    return {
        "cache_key": f"{bucket}:{content_hash}",
        "cache_invalidation_refs": [f"content_hash:{content_hash}"],
    }


def segment_render_cache_metadata(segment: ContextSegment) -> dict[str, object]:
    return {
        "cache_key": segment.cache_key,
        "cache_invalidation_refs": list(segment.cache_invalidation_refs),
    }
