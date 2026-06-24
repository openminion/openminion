from typing import Any

from openminion.modules.llm.schemas import UsageInfo


def build_prompt_cache_observation_payload(
    *,
    provider: str,
    model: str,
    usage: UsageInfo | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "provider": str(provider or "").strip(),
        "model": str(model or "").strip(),
        "supported": False,
    }
    if usage is None:
        return payload

    for field_name in (
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cached_tokens",
        "cache_creation_tokens",
    ):
        value = getattr(usage, field_name)
        if value is not None:
            payload[field_name] = int(value)
    payload["supported"] = (
        "cached_tokens" in payload or "cache_creation_tokens" in payload
    )
    return payload


__all__ = ["build_prompt_cache_observation_payload"]
