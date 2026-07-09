import hashlib
import json
from typing import Any, Iterable


DEFAULT_SAFETY_TEXT = "Follow safety policies. Refuse unsafe or disallowed operations."

_IDENTITY_DIRECTIVE = (
    "You are the agent described above. Apply this persona to all responses — "
    "voice, tone, and name — unconditionally. "
    "Do not describe yourself using information outside this profile."
)

_TOOL_RESULT_FORMAT_TEXT = (
    "When presenting tool results, apply your identity tone and style.\n"
    "Surface only what the user needs. Never expose raw JSON, provider metadata,\n"
    "source URLs, or license information in your response. Specific guidance:\n"
    "- weather: temperature, condition, and location only\n"
    "- time: time and timezone in one sentence\n"
    "- web.search: brief summary, cite the source\n"
    "- file/exec: confirm the action or surface the output directly\n"
    "- Default: respond naturally in your established voice"
)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _normalize_lines(text: str) -> str:
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").split("\n")]
    compact: list[str] = []
    previous_blank = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if not previous_blank:
                compact.append("")
            previous_blank = True
            continue
        compact.append(stripped)
        previous_blank = False
    return "\n".join(compact).strip()


class PinnedPrefixBuilder:
    """Deterministic builder for cache-friendly static prompt prefix."""

    def __init__(self, safety_text: str = DEFAULT_SAFETY_TEXT) -> None:
        self._safety_text = _normalize_lines(safety_text)

    def build(
        self,
        *,
        identity_text: str,
        tool_schemas: Iterable[Any] | None = None,
        policy_rules: Iterable[str] | None = None,
    ) -> str:
        sections: list[str] = [
            "[SYSTEM SAFETY]\n" + self._safety_text,
            "[IDENTITY]\n"
            + _normalize_lines(identity_text)
            + "\n\n"
            + _IDENTITY_DIRECTIVE,
        ]

        normalized_tools = self._normalize_tools(tool_schemas or [])
        if normalized_tools:
            sections.append("[TOOL SCHEMAS]\n" + "\n".join(normalized_tools))

        sections.append("[TOOL RESULT FORMAT]\n" + _TOOL_RESULT_FORMAT_TEXT)

        normalized_rules = sorted(
            normalized
            for rule in (policy_rules or [])
            if (normalized := _normalize_lines(rule))
        )
        if normalized_rules:
            sections.append(
                "[POLICY]\n" + "\n".join(f"- {rule}" for rule in normalized_rules)
            )

        return "\n\n".join(section for section in sections if section.strip())

    @staticmethod
    def hash(prefix_text: str) -> str:
        return hashlib.sha256(prefix_text.encode("utf-8")).hexdigest()

    def _normalize_tools(self, tool_schemas: Iterable[Any]) -> list[str]:
        prepared: list[tuple[str, str]] = []
        for entry in tool_schemas:
            if isinstance(entry, dict):
                name = str(entry.get("name", "")).strip()
                payload = dict(entry)
                payload.pop("name", None)
                canonical = _canonical_json(payload if payload else entry)
                key = name or canonical
                line = f"- {name}: {canonical}" if name else f"- {canonical}"
                prepared.append((key, line))
            else:
                canonical = _canonical_json(entry)
                prepared.append((canonical, f"- {canonical}"))
        prepared.sort(key=lambda item: item[0])
        return [item[1] for item in prepared]


class PrefixCacheAdapter:
    """Provider-aware stable cache-key builder for prompt prefix caching."""

    SUPPORTED_PROVIDERS: frozenset[str] = frozenset({"openai", "anthropic", "generic"})

    def __init__(self, provider: str = "generic") -> None:
        if provider not in self.SUPPORTED_PROVIDERS:
            raise ValueError(
                f"Unsupported provider {provider!r}. "
                f"Must be one of {sorted(self.SUPPORTED_PROVIDERS)}."
            )
        self._provider = provider

    @property
    def provider(self) -> str:
        return self._provider

    def build_cache_key(
        self,
        *,
        agent_id: str,
        static_prefix_hash: str,
        tool_schema_hash: str,
        policy_hash: str,
        model_hint: str = "",
    ) -> str:
        """Return a stable, provider-aware cache key string."""
        payload: dict[str, str] = {
            "provider": self._provider,
            "agent_id": agent_id,
            "static_prefix_hash": static_prefix_hash,
            "tool_schema_hash": tool_schema_hash,
            "policy_hash": policy_hash,
        }
        if self._provider in {"openai", "anthropic"} and model_hint:
            payload["model"] = model_hint

        canonical = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return f"{self._provider}:{digest}"

    def cache_control_blocks(self, *, prefix_hash: str) -> dict[str, Any]:
        """Return provider-specific cache control metadata for a given prefix hash.

        This dict is intended to be merged into the API request payload so the
        provider knows which prefix boundary to cache up to.
        """
        if self._provider == "anthropic":
            return {
                "cache_control": {"type": "ephemeral"},
                "prefix_hash": prefix_hash,
            }
        if self._provider == "openai":
            return {
                "cache_control": "auto",
                "prefix_hash": prefix_hash,
            }
        return {"prefix_hash": prefix_hash}
