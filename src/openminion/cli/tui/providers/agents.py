from __future__ import annotations

from typing import Any

from openminion.cli.parser.contracts import CLI_INTERFACE_VERSION


class RuntimeAgentsProvider:
    contract_version: str = CLI_INTERFACE_VERSION

    def __init__(self, runtime: Any) -> None:
        self._runtime = runtime

    def _identityctl(self) -> Any | None:
        rt = self._runtime
        hot = []
        if callable(getattr(rt, "list_hot_agents", None)):
            hot = rt.list_hot_agents()
        if not hot:
            if callable(getattr(rt, "list_registered_agents", None)):
                hot = rt.list_registered_agents()[:1]
        for aid in hot:
            try:
                svc = rt.resolve_agent_service(aid)
                ctl = getattr(svc, "_identityctl", None)
                if ctl is not None:
                    return ctl
            except AttributeError:
                continue
        return None

    def list_agents(self) -> list[dict[str, Any]]:
        rt = self._runtime
        registered: list[str] = []
        if callable(getattr(rt, "list_registered_agents", None)):
            registered = rt.list_registered_agents()
        hot: set[str] = set()
        if callable(getattr(rt, "list_hot_agents", None)):
            hot = set(rt.list_hot_agents())

        ctl = self._identityctl()
        profiles: dict[str, dict] = {}
        if ctl is not None and callable(getattr(ctl, "list_profiles", None)):
            for summary in ctl.list_profiles():
                aid = getattr(summary, "agent_id", "") or ""
                profiles[aid] = {
                    "display_name": getattr(summary, "display_name", aid),
                    "revision": getattr(summary, "profile_revision", 0),
                }

        result = []
        seen = set()
        for aid in registered:
            if aid in seen:
                continue
            seen.add(aid)
            provider = ""
            try:
                cfg = rt.resolve_agent_profile(aid)
                provider = getattr(cfg, "provider", "")
            except (AttributeError, TypeError, ValueError):
                pass
            prof = profiles.get(aid, {})
            result.append(
                {
                    "id": aid,
                    "display_name": prof.get("display_name", aid),
                    "provider": provider,
                    "is_hot": aid in hot,
                    "revision": prof.get("revision", 0),
                }
            )
        for aid, prof in profiles.items():
            if aid not in seen:
                result.append(
                    {
                        "id": aid,
                        "display_name": prof.get("display_name", aid),
                        "provider": "",
                        "is_hot": aid in hot,
                        "revision": prof.get("revision", 0),
                    }
                )
        return result

    def get_agent_detail(self, agent_id: str) -> dict[str, Any]:
        rt = self._runtime
        detail: dict[str, Any] = {"agent_id": agent_id}

        try:
            cfg = rt.resolve_agent_profile(agent_id)
            detail["provider"] = getattr(cfg, "provider", "")
            detail["thinking"] = getattr(cfg, "thinking", "")
            detail["channel"] = getattr(cfg, "default_channel", "")
        except (AttributeError, TypeError, ValueError):
            pass

        hot = set()
        if callable(getattr(rt, "list_hot_agents", None)):
            hot = set(rt.list_hot_agents())
        detail["is_hot"] = agent_id in hot

        try:
            info = rt.get_agent_runtime_info(agent_id)
            detail["runtime_mode"] = info.get("runtime_mode", "")
            detail["fallback_reason"] = info.get("fallback_reason", "")
        except Exception:
            pass

        ctl = self._identityctl()
        if ctl is not None and callable(getattr(ctl, "get_profile", None)):
            profile = ctl.get_profile(agent_id)
            if profile is not None:
                detail["profile"] = _profile_to_dict(profile)

        return detail

    def get_agent_tools(self, agent_id: str) -> list[dict[str, Any]]:
        rt = self._runtime
        tools_registry = getattr(rt, "tools", None)
        if tools_registry is None:
            return []

        all_tools: list[dict[str, Any]] = []
        catalog = {}
        if callable(getattr(tools_registry, "list", None)):
            catalog = tools_registry.list()

        # Get tool posture from identity
        posture: dict[str, Any] = {}
        ctl = self._identityctl()
        if ctl is not None and callable(getattr(ctl, "get_profile", None)):
            profile = ctl.get_profile(agent_id)
            if profile is not None:
                tp = getattr(profile, "tool_posture", None)
                if tp is not None:
                    posture = {
                        "tool_use": getattr(tp, "tool_use", "allowed"),
                        "allowed_tools": list(getattr(tp, "allowed_tools", [])),
                        "blocked_patterns": list(getattr(tp, "blocked_patterns", [])),
                    }

        import fnmatch

        allowed_list = posture.get("allowed_tools", [])
        blocked_patterns = posture.get("blocked_patterns", [])
        tool_use = posture.get("tool_use", "allowed")
        enforce_allowlist = tool_use in ("restricted", "read_only") and allowed_list

        for name in sorted(catalog.keys()):
            blocked = any(
                fnmatch.fnmatch(name, pattern) for pattern in blocked_patterns
            )
            if enforce_allowlist and name not in allowed_list:
                blocked = True

            all_tools.append(
                {
                    "name": name,
                    "allowed": not blocked,
                }
            )
        return all_tools

    def render_identity_preview(
        self,
        agent_id: str,
        *,
        purpose: str = "act",
        max_tokens: int = 256,
    ) -> str:
        ctl = self._identityctl()
        if ctl is None:
            return ""
        render = getattr(ctl, "render", None)
        if not callable(render):
            return ""
        try:
            snippet = render(
                agent_id,
                purpose=purpose,
                max_tokens=max_tokens,
            )
        except Exception:
            return ""
        return str(getattr(snippet, "text", "") or "").strip()

    def upsert_profile(self, profile_dict: dict[str, Any]) -> str:
        ctl = self._identityctl()
        if ctl is None:
            return ""
        from openminion.modules.identity.models import AgentProfile

        profile = AgentProfile(**profile_dict)
        return ctl.upsert_profile(profile, actor="tui", reason="TUI edit")

    def delete_profile(self, agent_id: str) -> None:
        ctl = self._identityctl()
        if ctl is not None:
            ctl.delete_profile(agent_id)

    def create_default_profile(
        self, agent_id: str, display_name: str
    ) -> dict[str, Any]:
        from openminion.modules.identity.models import (
            AgentProfile,
            PersonalitySpec,
            RiskSpec,
            RoleSpec,
            ToolPostureSpec,
        )

        profile = AgentProfile(
            agent_id=agent_id,
            display_name=display_name or agent_id,
            profile_revision=1,
            role=RoleSpec(
                mission="A helpful assistant.",
                responsibilities=["Answer questions", "Follow instructions"],
            ),
            personality=PersonalitySpec(tone="professional"),
            risk=RiskSpec(risk_level="medium"),
            tool_posture=ToolPostureSpec(tool_use="allowed"),
        )
        ctl = self._identityctl()
        if ctl is not None:
            ctl.upsert_profile(profile, actor="tui", reason="Created from TUI")
        return _profile_to_dict(profile)


def _profile_to_dict(profile: Any) -> dict[str, Any]:
    d: dict[str, Any] = {
        "agent_id": getattr(profile, "agent_id", ""),
        "display_name": getattr(profile, "display_name", ""),
        "profile_revision": getattr(profile, "profile_revision", 0),
        "inherits": getattr(profile, "inherits", None),
    }
    role = getattr(profile, "role", None)
    if role:
        d["role"] = {
            "mission": getattr(role, "mission", ""),
            "responsibilities": list(getattr(role, "responsibilities", [])),
            "hard_constraints": list(getattr(role, "hard_constraints", [])),
            "domain": list(getattr(role, "domain", [])),
        }
    personality = getattr(profile, "personality", None)
    if personality:
        d["personality"] = {
            "tone": getattr(personality, "tone", ""),
            "verbosity": getattr(personality, "verbosity", "normal"),
            "formatting": list(getattr(personality, "formatting", [])),
            "interaction_style": list(getattr(personality, "interaction_style", [])),
        }
    risk = getattr(profile, "risk", None)
    if risk:
        d["risk"] = {
            "risk_level": getattr(risk, "risk_level", "medium"),
            "confirm_before": list(getattr(risk, "confirm_before", [])),
        }
    tp = getattr(profile, "tool_posture", None)
    if tp:
        d["tool_posture"] = {
            "tool_use": getattr(tp, "tool_use", "allowed"),
            "allowed_tools": list(getattr(tp, "allowed_tools", [])),
            "blocked_patterns": list(getattr(tp, "blocked_patterns", [])),
        }
    return d
