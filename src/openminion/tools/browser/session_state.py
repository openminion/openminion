import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from openminion.base.config.env import EnvironmentConfig
from openminion.base.config.paths import ensure_under_data_root
from openminion.tools.config import resolve_tool_data_root

from .constants import DEFAULT_BROWSER_SESSION_STATE_RELATIVE_PATH
from .models import BrowserCallArgs, BrowserResult, BrowserOp, normalize_op


@dataclass
class SessionBrowserState:
    instance_id: str = ""
    tab_id: str = ""


class BrowserSessionStateStore:
    """Persist and hydrate browser instance/tab affinity per workspace+provider+session."""

    def __init__(
        self,
        *,
        state_relative_path: str = DEFAULT_BROWSER_SESSION_STATE_RELATIVE_PATH,
    ) -> None:
        self._state_relative_path = str(
            state_relative_path or DEFAULT_BROWSER_SESSION_STATE_RELATIVE_PATH
        )
        self._session_state: dict[tuple[str, str, str], SessionBrowserState] = {}
        self._loaded_workspace_roots: set[str] = set()

    @property
    def session_state(self) -> dict[tuple[str, str, str], SessionBrowserState]:
        return self._session_state

    @property
    def loaded_workspace_roots(self) -> set[str]:
        return self._loaded_workspace_roots

    def state_key(
        self,
        *,
        provider_id: str,
        session_id: str,
        workspace_root: str | None,
    ) -> tuple[str, str, str] | None:
        provider = str(provider_id).strip()
        session = str(session_id).strip()
        workspace = str(workspace_root or "").strip()
        if not provider or not session or not workspace:
            return None
        return (workspace, provider, session)

    def state_for(
        self,
        *,
        provider_id: str,
        session_id: str,
        workspace_root: str | None,
        extras: Mapping[str, Any] | None = None,
        env: EnvironmentConfig | Mapping[str, Any] | None = None,
    ) -> SessionBrowserState:
        key = self.state_key(
            provider_id=provider_id,
            session_id=session_id,
            workspace_root=workspace_root,
        )
        if key is None:
            return SessionBrowserState(
                instance_id=str(
                    (extras or {}).get("session_browser_instance_id")
                    or (extras or {}).get("session.browser.instance_id")
                    or ""
                ).strip(),
                tab_id=str(
                    (extras or {}).get("session_browser_tab_id")
                    or (extras or {}).get("session.browser.tab_id")
                    or ""
                ).strip(),
            )

        workspace_key = key[0]
        self.load_persisted_session_state(workspace_root=workspace_key, env=env)
        state = self._session_state.get(key)
        if state is None:
            state = SessionBrowserState()
            self._session_state[key] = state

        seeded_instance = str(
            (extras or {}).get("session_browser_instance_id")
            or (extras or {}).get("session.browser.instance_id")
            or ""
        ).strip()
        if seeded_instance and not state.instance_id:
            state.instance_id = seeded_instance

        seeded_tab = str(
            (extras or {}).get("session_browser_tab_id")
            or (extras or {}).get("session.browser.tab_id")
            or ""
        ).strip()
        if seeded_tab and not state.tab_id:
            state.tab_id = seeded_tab

        if state.instance_id or state.tab_id:
            self.persist_session_state(workspace_root=workspace_key, env=env)
        return state

    def hydrate_call_with_session_state(
        self,
        *,
        provider_id: str,
        session_id: str,
        workspace_root: str | None,
        extras: Mapping[str, Any] | None,
        call: BrowserCallArgs,
        ops_require_instance: set[str],
        ops_require_tab: set[str],
        env: EnvironmentConfig | Mapping[str, Any] | None = None,
    ) -> BrowserCallArgs:
        op = normalize_op(call.op)
        updates: dict[str, Any] = {}
        state = self.state_for(
            provider_id=provider_id,
            session_id=session_id,
            workspace_root=workspace_root,
            extras=extras,
            env=env,
        )
        if not call.instance_id and op in ops_require_instance and state.instance_id:
            updates["instance_id"] = state.instance_id
        if not call.tab_id and op in ops_require_tab and state.tab_id:
            updates["tab_id"] = state.tab_id
        if updates:
            return call.model_copy(update=updates)
        return call

    def remember_session_state(
        self,
        *,
        provider_id: str,
        session_id: str,
        workspace_root: str | None,
        call: BrowserCallArgs,
        result: BrowserResult,
        env: EnvironmentConfig | Mapping[str, Any] | None = None,
    ) -> None:
        key = self.state_key(
            provider_id=provider_id,
            session_id=session_id,
            workspace_root=workspace_root,
        )
        if key is None:
            return
        workspace_key = key[0]
        self.load_persisted_session_state(workspace_root=workspace_key, env=env)
        state = self._session_state.setdefault(key, SessionBrowserState())
        op = normalize_op(call.op)

        if call.instance_id:
            state.instance_id = str(call.instance_id)
        if result.instance and result.instance.id:
            state.instance_id = result.instance.id
        if result.instances:
            active_instances = {row.id for row in result.instances if row.id}
            if state.instance_id and state.instance_id not in active_instances:
                state.instance_id = ""
                state.tab_id = ""
            if not state.instance_id and len(active_instances) == 1:
                state.instance_id = next(iter(active_instances))

        if result.tab and result.tab.id:
            state.tab_id = result.tab.id
        if result.tabs:
            active_tabs = {tab.id for tab in result.tabs if tab.id}
            if state.tab_id and state.tab_id not in active_tabs:
                state.tab_id = ""
            if not state.tab_id and len(active_tabs) == 1:
                state.tab_id = next(iter(active_tabs))

        target_instance = str(
            call.instance_id or (result.instance.id if result.instance else "")
        ).strip()
        if (
            op in {BrowserOp.INSTANCE_STOP.value, BrowserOp.INSTANCE_KILL.value}
            and target_instance
        ):
            if state.instance_id == target_instance:
                state.instance_id = ""
                state.tab_id = ""
        if op == BrowserOp.TAB_CLOSE.value:
            closed_tab_id = str(
                call.tab_id or (result.tab.id if result.tab else "")
            ).strip()
            if closed_tab_id and state.tab_id == closed_tab_id:
                state.tab_id = ""
        self.persist_session_state(workspace_root=workspace_key, env=env)

    def clear_session_state(
        self,
        *,
        provider_id: str,
        session_id: str,
        workspace_root: str | None,
        env: EnvironmentConfig | Mapping[str, Any] | None = None,
    ) -> None:
        key = self.state_key(
            provider_id=provider_id,
            session_id=session_id,
            workspace_root=workspace_root,
        )
        if key is None:
            return
        workspace_key = key[0]
        self.load_persisted_session_state(workspace_root=workspace_key, env=env)
        if key in self._session_state:
            self._session_state.pop(key, None)
            self.persist_session_state(workspace_root=workspace_key, env=env)

    def load_persisted_session_state(
        self,
        *,
        workspace_root: str,
        env: EnvironmentConfig | Mapping[str, Any] | None = None,
    ) -> None:
        workspace_key = str(workspace_root or "").strip()
        if not workspace_key:
            return
        if workspace_key in self._loaded_workspace_roots:
            return
        self._loaded_workspace_roots.add(workspace_key)
        path = self._session_state_path(workspace_root=workspace_key, env=env)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except Exception:
            return
        if not isinstance(payload, Mapping):
            return
        sessions = payload.get("sessions")
        if not isinstance(sessions, Mapping):
            return
        for session_key, row in sessions.items():
            if not isinstance(session_key, str) or not isinstance(row, Mapping):
                continue
            provider_id, session_id = self._parse_persisted_session_key(session_key)
            if not provider_id or not session_id:
                continue
            state = SessionBrowserState(
                instance_id=str(row.get("instance_id", "")).strip(),
                tab_id=str(row.get("tab_id", "")).strip(),
            )
            if not state.instance_id and not state.tab_id:
                continue
            self._session_state[(workspace_key, provider_id, session_id)] = state

    def persist_session_state(
        self,
        *,
        workspace_root: str,
        env: EnvironmentConfig | Mapping[str, Any] | None = None,
    ) -> None:
        workspace_key = str(workspace_root or "").strip()
        if not workspace_key:
            return
        path = self._session_state_path(workspace_root=workspace_key, env=env)
        sessions: dict[str, dict[str, str]] = {}
        for state_key, state in self._session_state.items():
            state_workspace, provider_id, session_id = state_key
            if state_workspace != workspace_key:
                continue
            if not state.instance_id and not state.tab_id:
                continue
            sessions[
                self._persisted_session_key(
                    provider_id=provider_id, session_id=session_id
                )
            ] = {
                "instance_id": state.instance_id,
                "tab_id": state.tab_id,
            }

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not sessions:
                if path.exists():
                    path.unlink()
                return
            payload = {"version": 1, "sessions": sessions}
            tmp_path = path.with_suffix(path.suffix + ".tmp")
            tmp_path.write_text(
                json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            tmp_path.replace(path)
        except Exception:
            return

    def _session_state_path(
        self,
        *,
        workspace_root: str,
        env: EnvironmentConfig | Mapping[str, Any] | None = None,
    ) -> Path:
        del workspace_root
        data_root = resolve_tool_data_root(env=env)
        path = (data_root / self._state_relative_path).resolve(strict=False)
        return ensure_under_data_root(path, data_root, label="browser_session_state")

    def _persisted_session_key(self, *, provider_id: str, session_id: str) -> str:
        return f"{provider_id}::{session_id}"

    def _parse_persisted_session_key(self, value: str) -> tuple[str, str]:
        token = str(value).strip()
        if not token or "::" not in token:
            return "", ""
        provider_id, session_id = token.split("::", 1)
        return provider_id.strip(), session_id.strip()
