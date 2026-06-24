"""Modules brain adapters context bridges skill."""

from typing import Any

from .shared import (
    BRAIN_ADAPTER_INTERFACE_VERSION,
    _lazy_resolve_service,
    _normalized_string_list,
)


class BridgeSkillClient:
    """Bridge adapter to wrap Skill service for ContextCtlService and runner."""

    contract_version = BRAIN_ADAPTER_INTERFACE_VERSION

    def __init__(
        self,
        backing_store: Any,
        skill_config: Any | None = None,
        skill_home_root: Any | None = None,
    ) -> None:
        self._store = backing_store
        self._skill_config = skill_config
        self._skill_home_root = skill_home_root
        self._skill_svc: Any | None = None

    def _resolve_skillctl(self) -> Any | None:
        return _lazy_resolve_service(
            self,
            cache_attr="_skill_svc",
            import_loader=_import_skill_service,
            factory=self._build_skill_service,
        )

    def _build_skill_service(self, skill_cls: Any) -> Any | None:
        return skill_cls(
            config=self._skill_config if self._skill_config is not None else {},
            home_root=self._skill_home_root,
        )

    def match(
        self,
        intent_text: str,
        step_hint: dict[str, Any] | None = None,
        agent_id: str | None = None,
        k: int = 3,
        status_filter: list[str] | str | None = None,
    ) -> list[dict[str, Any]]:
        skill_svc = self._resolve_skillctl()
        if skill_svc is None:
            return []
        try:
            matches = skill_svc.match(
                intent_text=intent_text,
                step_hint=step_hint,
                agent_id=agent_id,
                k=k,
                status_filter=status_filter,
            )
            return (
                [
                    {
                        "skill_id": m.skill_id,
                        "skill_name": getattr(m, "skill_name", ""),
                        "version_hash": getattr(m, "version_hash", ""),
                        "score": getattr(m, "score", 0.0),
                    }
                    for m in matches
                ]
                if matches
                else []
            )
        except Exception:
            return []

    def catalog_summaries(
        self,
        agent_id: str,
        status_filter: list[str] | str | None = None,
    ) -> list[dict[str, Any]]:
        skill_svc = self._resolve_skillctl()
        if skill_svc is None:
            return []
        try:
            summaries = skill_svc.catalog_summaries(
                agent_id=agent_id,
                status_filter=status_filter,
            )
            return [
                {
                    "id": str(item.get("id", "") or ""),
                    "name": str(item.get("name", "") or ""),
                    "display_name": str(item.get("display_name", "") or ""),
                    "canonical_name": str(item.get("canonical_name", "") or ""),
                    "short_description": str(item.get("short_description", "") or ""),
                    "one_liner": str(item.get("one_liner", "") or ""),
                    "version_hash": str(item.get("version_hash", "") or ""),
                    "tags": _normalized_string_list(item.get("tags", [])),
                    "tools": _normalized_string_list(item.get("tools", [])),
                    "reference_hints": _normalized_string_list(
                        item.get("reference_hints", [])
                    ),
                }
                for item in summaries
                if isinstance(item, dict)
            ]
        except Exception:
            return []

    def render_snippet(
        self,
        skill_id: str,
        version_hash: str | None = None,
        purpose: str = "act",
        max_tokens: int = 500,
        mode_name: str | None = None,
    ) -> tuple[str, str]:
        skill_svc = self._resolve_skillctl()
        if skill_svc is None:
            return ("", "")
        request_variants = (
            {
                "skill_id": skill_id,
                "version_hash": version_hash,
                "purpose": purpose,
                "max_tokens": max_tokens,
                "mode_name": mode_name,
            },
            {
                "skill_id": skill_id,
                "version_hash": version_hash,
                "purpose": purpose,
                "mode_name": mode_name,
            },
        )
        for render_kwargs in request_variants:
            try:
                text, hash_val = skill_svc.render_snippet(**render_kwargs)
                return str(text), str(hash_val)
            except Exception:
                continue
        return ("", "")


def _import_skill_service() -> Any | None:
    try:
        from openminion.modules.skill.runtime.skill import Skill
    except Exception:
        return None
    return Skill


__all__ = ["BridgeSkillClient"]
