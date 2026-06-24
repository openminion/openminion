from typing import Any, Protocol, runtime_checkable

from openminion.modules.skill.models import SkillMatch, ToolRecipe


@runtime_checkable
class SkillJITClient(Protocol):
    """Protocol consumed by the brain for JIT skill access."""

    def match(
        self,
        intent_text: str,
        step_hint: dict[str, Any] | None,
        agent_id: str,
        k: int = 3,
        status_filter: list[str] | str | None = None,
    ) -> list[SkillMatch]: ...

    def render_snippet(
        self,
        skill_id: str,
        version_hash: str | None,
        purpose: str,
        max_tokens: int,
        mode_name: str | None = None,
    ) -> tuple[str, str]: ...

    def get_recipe(
        self,
        skill_id: str,
        version_hash: str | None = None,
    ) -> ToolRecipe | None: ...

    def log_run(
        self,
        session_id: str,
        agent_id: str,
        skill_id: str,
        version_hash: str,
        used_for: str,
        outcome: str,
        evidence_refs: list[str] | None = None,
    ) -> str: ...


class ContextCtlSkillAdapter:
    """Adapter over a ``Skill`` service instance."""

    def __init__(self, skill: Any) -> None:
        self._skill = skill

    def match(
        self,
        intent_text: str,
        step_hint: dict[str, Any] | None,
        agent_id: str,
        k: int = 3,
        status_filter: list[str] | str | None = None,
    ) -> list[SkillMatch]:
        return self._skill.match(
            intent_text=intent_text,
            step_hint=step_hint,
            agent_id=agent_id,
            k=k,
            status_filter=status_filter,
        )

    def render_snippet(
        self,
        skill_id: str,
        version_hash: str | None,
        purpose: str,
        max_tokens: int,
        mode_name: str | None = None,
    ) -> tuple[str, str]:
        return self._skill.render_snippet(
            skill_id=skill_id,
            version_hash=version_hash,
            purpose=purpose,
            max_tokens=max_tokens,
            mode_name=mode_name,
        )

    def get_recipe(
        self,
        skill_id: str,
        version_hash: str | None = None,
    ) -> ToolRecipe | None:
        return self._skill.get_recipe(skill_id=skill_id, version_hash=version_hash)

    def log_run(
        self,
        session_id: str,
        agent_id: str,
        skill_id: str,
        version_hash: str,
        used_for: str,
        outcome: str,
        evidence_refs: list[str] | None = None,
    ) -> str:
        return self._skill.log_run(
            session_id=session_id,
            agent_id=agent_id,
            skill_id=skill_id,
            version_hash=version_hash,
            used_for=used_for,
            outcome=outcome,
            evidence_refs=evidence_refs,
        )
