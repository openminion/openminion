import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from openminion.modules.llm.errors import LLMCtlError
from openminion.modules.llm.providers.base import ProviderToolSpec

_SAFE_EXTERNAL_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_UNSAFE_EXTERNAL_TOOL_CHARS_RE = re.compile(r"[^A-Za-z0-9_-]+")


@dataclass(frozen=True)
class ToolSchemaCapability:
    id: str = "identity"
    requires_external_name_normalization: bool = False
    allowed_name_pattern: str | None = None
    max_external_name_length: int | None = None


@dataclass
class ToolSchemaNameMap:
    capability: ToolSchemaCapability
    canonical_to_external: dict[str, str] = field(default_factory=dict)
    external_to_canonical: dict[str, str] = field(default_factory=dict)

    @property
    def active(self) -> bool:
        return bool(self.external_to_canonical)

    def external_name_for(self, canonical_name: str) -> str:
        token = str(canonical_name or "").strip()
        return self.canonical_to_external.get(token, token)

    def expand_allowed_tool_names(
        self,
        allowed_tool_names: Iterable[str] | None,
    ) -> list[str]:
        ordered: list[str] = []
        seen: set[str] = set()
        for raw_name in allowed_tool_names or ():
            canonical_name = str(raw_name or "").strip()
            if not canonical_name:
                continue
            for candidate in (
                canonical_name,
                self.canonical_to_external.get(canonical_name, ""),
            ):
                token = str(candidate or "").strip()
                if token and token not in seen:
                    seen.add(token)
                    ordered.append(token)
        return ordered


def resolve_tool_schema_capability(
    *,
    provider_name: str | None,
    model_name: str | None,
) -> ToolSchemaCapability:
    provider = str(provider_name or "").strip().lower()
    if provider in {"openai", "openrouter"}:
        return ToolSchemaCapability(
            id="openai_dialect_safe_names",
            requires_external_name_normalization=True,
            allowed_name_pattern=r"^[A-Za-z0-9_-]{1,128}$",
            max_external_name_length=128,
        )
    return ToolSchemaCapability()


def build_tool_schema_name_map(
    tools: Sequence[ProviderToolSpec],
    *,
    provider_name: str | None,
    model_name: str | None,
    capability: ToolSchemaCapability | None = None,
) -> ToolSchemaNameMap:
    """Build tool schema name map helper."""

    if capability is None:
        capability = resolve_tool_schema_capability(
            provider_name=provider_name,
            model_name=model_name,
        )
    if not capability.requires_external_name_normalization:
        return ToolSchemaNameMap(capability=capability)

    unique_canonical_names: list[str] = []
    seen_canonical: set[str] = set()
    for tool in tools:
        canonical_name = str(getattr(tool, "name", "") or "").strip()
        if canonical_name and canonical_name not in seen_canonical:
            seen_canonical.add(canonical_name)
            unique_canonical_names.append(canonical_name)

    canonical_to_external: dict[str, str] = {}
    used_external_names: set[str] = set()

    for canonical_name in unique_canonical_names:
        if _is_safe_external_tool_name(canonical_name, capability=capability):
            canonical_to_external[canonical_name] = canonical_name
            used_external_names.add(canonical_name)

    for canonical_name in unique_canonical_names:
        if canonical_name in canonical_to_external:
            continue
        external_name = _build_external_tool_name(
            canonical_name,
            capability=capability,
        )
        if external_name in used_external_names:
            colliding_name = next(
                existing_canonical
                for existing_canonical, existing_external in canonical_to_external.items()
                if existing_external == external_name
            )
            raise LLMCtlError(
                "INVALID_ARGUMENT",
                "OpenAI-dialect tool-name sanitization collision: "
                f"{canonical_name!r} and {colliding_name!r} both map to "
                f"{external_name!r}",
                {
                    "canonical_name": canonical_name,
                    "colliding_name": colliding_name,
                    "external_name": external_name,
                },
            )
        canonical_to_external[canonical_name] = external_name
        used_external_names.add(external_name)

    external_to_canonical = {
        external_name: canonical_name
        for canonical_name, external_name in canonical_to_external.items()
        if external_name != canonical_name
    }
    canonical_to_external = {
        canonical_name: external_name
        for canonical_name, external_name in canonical_to_external.items()
        if external_name != canonical_name
    }
    return ToolSchemaNameMap(
        capability=capability,
        canonical_to_external=canonical_to_external,
        external_to_canonical=external_to_canonical,
    )


def remap_provider_tool_call_name(
    tool_name: str,
    *,
    external_to_canonical: dict[str, str] | None,
) -> str:
    token = str(tool_name or "").strip()
    if not token or not external_to_canonical:
        return token
    return external_to_canonical.get(token, token)


def _is_safe_external_tool_name(
    tool_name: str,
    *,
    capability: ToolSchemaCapability,
) -> bool:
    token = str(tool_name or "").strip()
    if not token:
        return False
    max_length = capability.max_external_name_length
    if isinstance(max_length, int) and max_length > 0 and len(token) > max_length:
        return False
    return bool(_SAFE_EXTERNAL_TOOL_NAME_RE.fullmatch(token))


def _build_external_tool_name(
    canonical_name: str,
    *,
    capability: ToolSchemaCapability,
) -> str:
    candidate = _sanitize_external_tool_name(canonical_name)
    return _fit_external_tool_name_length(candidate, capability=capability)


def _sanitize_external_tool_name(canonical_name: str) -> str:
    token = _UNSAFE_EXTERNAL_TOOL_CHARS_RE.sub("_", str(canonical_name or "").strip())
    token = token.strip("_-")
    return token or "tool"


def _fit_external_tool_name_length(
    candidate: str,
    *,
    capability: ToolSchemaCapability,
) -> str:
    max_length = capability.max_external_name_length
    if (
        not isinstance(max_length, int)
        or max_length <= 0
        or len(candidate) <= max_length
    ):
        return candidate
    return candidate[:max_length].rstrip("_-") or candidate[:max_length]
