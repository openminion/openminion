from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

from openminion.base.config.action_policy import (
    ACTION_POLICY_SESSION_OVERRIDE_KEY,
    normalize_action_policy_mode_override,
)
from openminion.base.config.runtime.profile import (
    PERMISSION_MODE_DEFAULT,
    PERMISSION_MODE_VALUES,
    next_permission_mode,
)
from openminion.cli.status import TokenUsageTotals

_PROVIDER_CONFIG_ALIASES = {
    "claude": "anthropic",
}


class RuntimeControlsMixin:
    _agent_id: str | None
    _agent_id_override: str | None
    _completed_session_usage: TokenUsageTotals
    _current_turn_usage: TokenUsageTotals | None
    _memory_provider: Any
    _model_override_model: str
    _model_override_provider: str
    _action_policy_mode_override: str
    _permission_mode: str
    _permission_overrides: dict[str, str]
    _read_only_mode: bool
    _rt: Any
    _statusline_command: str
    _target: str
    _working_dir: str | None

    if TYPE_CHECKING:

        @property
        def agent_id(self) -> str: ...

        @property
        def is_bound(self) -> bool: ...

        @property
        def session_id(self) -> str: ...

        def bind_session(self, session_id: str) -> None: ...

        def create_new_session(self) -> str: ...

        def token_usage_snapshot(self) -> Any: ...

    def list_models(self) -> list[tuple[str, str, bool]]:
        result: list[tuple[str, str, bool]] = []
        providers_cfg = getattr(getattr(self._rt, "config", None), "providers", None)
        if providers_cfg is None:
            return result
        active_provider, active_model = self._provider_model_identity()
        for provider_name in (
            "anthropic",
            "openai",
            "openrouter",
            "cerebras",
            "groq",
            "ollama",
            "cortensor",
        ):
            provider_cfg = getattr(providers_cfg, provider_name, None)
            if provider_cfg is None:
                continue
            configured_model = str(getattr(provider_cfg, "model", "") or "").strip()
            is_active = provider_name == active_provider and (
                not active_model or configured_model == active_model
            )
            result.append((provider_name, configured_model, is_active))
        return result

    def switch_model(self, target: str) -> tuple[str, str]:
        raw = str(target or "").strip()
        if not raw or raw.lower() == "default":
            self._model_override_provider = ""
            self._model_override_model = ""
            provider, model = self._provider_model_identity()
            return provider, model
        if "/" in raw:
            provider_part, _, model_part = raw.partition("/")
        else:
            provider_part, model_part = raw, ""
        provider_part = provider_part.strip().lower()
        model_part = model_part.strip()
        provider_key = _PROVIDER_CONFIG_ALIASES.get(provider_part, provider_part)
        providers_cfg = getattr(getattr(self._rt, "config", None), "providers", None)
        provider_cfg = (
            getattr(providers_cfg, provider_key, None)
            if providers_cfg is not None
            else None
        )
        if provider_cfg is None:
            valid = ", ".join(name for name, _, _ in self.list_models())
            raise ValueError(
                f"unknown provider {provider_part!r}; "
                f"valid options: {valid or '(none configured)'}"
            )
        self._model_override_provider = provider_key
        self._model_override_model = model_part
        provider, model = self._provider_model_identity()
        return provider, model

    @property
    def read_only_mode(self) -> bool:
        return self.permission_mode == "readonly"

    def set_read_only_mode(self, enabled: bool) -> bool:
        self.set_permission_mode("readonly" if enabled else PERMISSION_MODE_DEFAULT)
        return self.read_only_mode

    @property
    def permission_mode(self) -> str:
        explicit = str(getattr(self, "_permission_mode", "") or "").strip().lower()
        if explicit:
            return explicit
        if bool(getattr(self, "_read_only_mode", False)):
            return "readonly"
        return PERMISSION_MODE_DEFAULT

    def set_permission_mode(self, mode: str) -> str:
        normalized = str(mode or "").strip().lower() or PERMISSION_MODE_DEFAULT
        if normalized not in PERMISSION_MODE_VALUES:
            valid = ", ".join(sorted(PERMISSION_MODE_VALUES))
            raise ValueError(f"unknown permission mode {mode!r}; valid modes: {valid}")
        self._permission_mode = (
            "" if normalized == PERMISSION_MODE_DEFAULT else normalized
        )
        self._read_only_mode = normalized == "readonly"
        return self.permission_mode

    def cycle_permission_mode(self) -> str:
        return self.set_permission_mode(next_permission_mode(self.permission_mode))

    @property
    def action_policy_mode_override(self) -> str:
        return str(getattr(self, "_action_policy_mode_override", "") or "").strip()

    def set_session_action_policy_mode(self, mode: str) -> str:
        normalized = normalize_action_policy_mode_override(mode)
        if normalized is None:
            raise ValueError("unknown action policy mode; valid modes: ask, auto, bypass")
        self._action_policy_mode_override = normalized
        self._persist_session_action_policy_mode(normalized)
        return normalized

    def _persist_session_action_policy_mode(self, mode: str) -> None:
        if not self.is_bound or not self.session_id:
            return
        sessions = getattr(getattr(self, "_rt", None), "sessions", None)
        update = getattr(sessions, "update_session_metadata", None)
        if not callable(update):
            return
        try:
            update(
                session_id=self.session_id,
                patch={ACTION_POLICY_SESSION_OVERRIDE_KEY: mode},
            )
        except Exception:
            return

    @property
    def permission_overrides(self) -> dict[str, str]:
        return dict(getattr(self, "_permission_overrides", {}) or {})

    def set_permission_override(self, tool_name: str, mode: str) -> str:
        tool = str(tool_name or "").strip().lower()
        if not tool:
            raise ValueError("tool name is required")
        normalized = str(mode or "").strip().lower()
        if normalized in {"default", "reset", "clear"}:
            self._permission_overrides.pop(tool, None)
            return PERMISSION_MODE_DEFAULT
        allowed = {"ask", "auto", "bypass", "readonly"}
        if normalized not in allowed:
            valid = ", ".join(sorted(allowed | {PERMISSION_MODE_DEFAULT}))
            raise ValueError(
                f"unknown per-tool permission mode {mode!r}; valid modes: {valid}"
            )
        self._permission_overrides[tool] = normalized
        return normalized

    def clear_permission_override(self, tool_name: str) -> bool:
        tool = str(tool_name or "").strip().lower()
        if not tool:
            raise ValueError("tool name is required")
        return self._permission_overrides.pop(tool, None) is not None

    @property
    def effort_level(self) -> str:
        return str(getattr(self, "_effort_level", "") or "").strip()

    def set_effort_level(self, level: str) -> str:
        normalized = str(level or "").strip().lower()
        if normalized in {"", "default", "reset", "clear"}:
            self._effort_level = ""
            return "default"
        allowed = {"low", "medium", "high", "xhigh", "max"}
        if normalized not in allowed:
            valid = ", ".join((*sorted(allowed), "default"))
            raise ValueError(f"unknown effort level {level!r}; valid levels: {valid}")
        self._effort_level = normalized
        return normalized

    @property
    def statusline_command(self) -> str:
        return str(getattr(self, "_statusline_command", "") or "").strip()

    def set_statusline_command(self, command: str) -> str:
        normalized = str(command or "").strip()
        if normalized.lower() in {"", "default", "off", "reset", "clear"}:
            self._statusline_command = ""
            return "default"
        self._statusline_command = normalized
        return normalized

    def statusline_label(self) -> str:
        command = self.statusline_command
        if not command:
            return ""
        import subprocess

        try:
            proc = subprocess.run(
                command,
                cwd=self._working_dir or None,
                shell=True,
                text=True,
                capture_output=True,
                timeout=0.5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        output = (proc.stdout or proc.stderr or "").strip().splitlines()
        return output[0][:80] if output else ""

    def list_memory_records(self) -> list[dict[str, Any]]:
        provider = getattr(self, "_memory_provider", None)
        if provider is not None and callable(getattr(provider, "list_records", None)):
            try:
                return list(provider.list_records(limit=50) or [])
            except Exception:
                return []
        return []

    def list_memory_candidates(self) -> list[dict[str, Any]]:
        provider = getattr(self, "_memory_provider", None)
        if provider is not None and callable(
            getattr(provider, "list_candidates", None)
        ):
            try:
                return list(provider.list_candidates() or [])
            except Exception:
                return []
        return []

    def memory_report(self) -> str:
        promoted = self.list_memory_records()
        candidates = self.list_memory_candidates()
        if not promoted and not candidates:
            return "(no memory)"
        lines = [
            "Memory:",
            f"  promoted   {len(promoted)}",
            f"  candidates {len(candidates)}",
        ]
        for row in promoted[:8]:
            title = str(
                row.get("title") or row.get("content_preview") or row.get("id") or ""
            ).strip()
            if title:
                lines.append(f"  - {title[:96]}")
        return "\n".join(lines)

    def list_skill_rows(self) -> list[dict[str, Any]]:
        config = getattr(self._rt, "config", None)
        agents = getattr(config, "agents", {}) if config is not None else {}
        profile = agents.get(self.agent_id) if isinstance(agents, Mapping) else None
        rows: list[dict[str, Any]] = []
        configured = []
        if profile is not None:
            raw_skill = getattr(profile, "skill", None)
            if isinstance(raw_skill, str) and raw_skill.strip():
                configured.append(raw_skill.strip())
            elif isinstance(raw_skill, (list, tuple)):
                configured.extend(
                    str(item).strip() for item in raw_skill if str(item).strip()
                )
            configured.extend(
                str(item).strip()
                for item in list(getattr(profile, "skill_catalog", []) or [])
                if str(item).strip()
            )
        for skill_id in dict.fromkeys(configured):
            rows.append({"id": skill_id, "source": "config"})
        return rows

    def skills_report(self) -> str:
        rows = self.list_skill_rows()
        if not rows:
            return "(no skills)"
        lines = ["Skills:"]
        for row in rows[:20]:
            lines.append(f"  - {row.get('id')} · {row.get('source', 'config')}")
        return "\n".join(lines)

    def undo_last_turn(self) -> dict[str, Any]:
        if not self.is_bound or not self.session_id:
            return {"ok": False, "message": "(no undoable action)"}
        turns = list(self._rt.sessions.list_turns(self.session_id, limit=500) or [])
        if len(turns) <= 1:
            return {"ok": False, "message": "(no undoable action)"}
        cut = len(turns)
        while cut > 0 and str(turns[cut - 1].get("role", "")).lower() != "user":
            cut -= 1
        if cut <= 0:
            return {"ok": False, "message": "(no undoable action)"}
        kept = turns[: cut - 1]
        old_session = self.session_id
        new_session = self.create_new_session()
        for turn in kept:
            role = str(turn.get("role", "") or "").strip().lower()
            text = str(turn.get("content") or turn.get("text") or "")
            if role in {"user", "assistant", "system", "tool"} and text:
                self._rt.sessions.append_turn(new_session, role, text)
        self.bind_session(new_session)
        return {
            "ok": True,
            "message": f"rewound latest turn into {new_session} (from {old_session})",
            "session_id": new_session,
        }

    def compact_history(self) -> dict[str, Any]:
        if not self.is_bound or not self.session_id:
            return {
                "compacted_count": 0,
                "summary_updated": False,
                "reason": "no_session",
            }
        from openminion.services.runtime.bootstrap import (
            build_session_context_service,
        )

        service = build_session_context_service(
            config=self._rt.config,
            sessions=self._rt.sessions,
            logger=self._rt.logger.getChild("focus.compact"),
            config_path=Path(self._rt.config_path),
            storage_path=self._rt.storage_path,
            memory_root=self._rt.memory_root,
            data_root=self._rt.data_root,
            retrieve_ctl=getattr(self._rt, "retrieve_ctl", None),
        )
        result = service.compact_session(session_id=self.session_id)
        payload: dict[str, Any] = {
            "compacted_count": int(getattr(result, "compacted_count", 0) or 0),
            "summary_updated": bool(getattr(result, "summary_updated", False)),
            "archive_relative_path": str(
                getattr(result, "archive_relative_path", "") or ""
            ),
        }
        try:
            snap = self.token_usage_snapshot()
            session_total = getattr(snap, "session_total_tokens", None)
            if session_total is not None:
                payload["session_total_tokens"] = session_total
        except Exception:
            pass
        return payload

    @property
    def provider_name(self) -> str:
        provider_name, _ = self._provider_model_identity()
        return provider_name

    @property
    def model_name(self) -> str:
        _, model_name = self._provider_model_identity()
        return model_name

    def _provider_model_identity(self) -> tuple[str, str]:
        provider_name = ""
        model_name = ""
        try:
            profile = self._rt.resolve_agent_profile(self.agent_id)
        except (AttributeError, TypeError, ValueError):
            config = getattr(self._rt, "config", None)
            agents = getattr(config, "agents", None)
            profile = agents.get(self.agent_id) if isinstance(agents, Mapping) else None
        provider_name = str(getattr(profile, "provider", "") or "").strip()
        model_name = str(getattr(profile, "model", "") or "").strip()
        if not provider_name and profile is None:
            if self._model_override_provider or self._model_override_model:
                return (
                    self._model_override_provider,
                    self._model_override_model,
                )
            return "", ""
        if self._model_override_provider:
            if self._model_override_provider != provider_name:
                model_name = ""
            provider_name = self._model_override_provider
        if self._model_override_model:
            model_name = self._model_override_model

        providers_cfg = getattr(getattr(self._rt, "config", None), "providers", None)
        provider_key = _PROVIDER_CONFIG_ALIASES.get(
            str(provider_name or "").strip().lower(),
            str(provider_name or "").strip().lower(),
        )
        provider_cfg = (
            getattr(providers_cfg, provider_key, None)
            if providers_cfg is not None and provider_key
            else None
        )
        if not model_name:
            model_name = str(getattr(provider_cfg, "model", "") or "").strip()
        return provider_name, model_name
