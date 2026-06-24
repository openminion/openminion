import logging
import os
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

from openminion.base.config.paths import (
    ensure_under_data_root,
)
from openminion.modules.tool.runtime.resource_selectors import ResourceSelectors
from openminion.modules.tool.constants import OPENMINION_CONFIG_PATH_ENV
from openminion.modules.tool.runtime import RuntimeContext
from openminion.tools.config import (
    ToolEnv,
    resolve_tool_data_root,
    resolve_tool_env,
    resolve_tool_workspace_root,
)
from openminion.tools.browser import BrowserCapabilities, BrowserProviderContext
from openminion.tools.browser.models import (
    BrowserAction,
    InstanceSpec,
    NavigateOptions,
    OutputOptions,
    SnapshotOptions,
    TextOptions,
)

from .client import (
    PinchTabClient,
    PinchTabClientConfig,
    PinchTabClientError,
    parse_base_url_targets,
)
from .constants import (
    DEFAULT_PINCHTAB_API_TIMEOUT_SECONDS,
    DEFAULT_PINCHTAB_AUTOSTART_TIMEOUT_SECONDS,
    DEFAULT_PINCHTAB_BASE_URL,
    DEFAULT_PINCHTAB_NAV_TIMEOUT_SECONDS,
    DEFAULT_PINCHTAB_OUTPUTS_SUBPATH,
    DEFAULT_PINCHTAB_RUNTIME_SUBPATH,
    PINCHTAB_ALLOW_REMOTE_ENV,
    PINCHTAB_AUTOSTART_ENV,
    PINCHTAB_LAUNCH_CMD_ENV,
    PINCHTAB_LAUNCH_ENV_ENV,
    PINCHTAB_LAUNCH_TIMEOUT_SECONDS_ENV,
    PINCHTAB_NAV_TIMEOUT_SECONDS_ENV,
    PINCHTAB_TIMEOUT_SECONDS_ENV,
    PINCHTAB_TOKEN_ENV,
    PINCHTAB_TOKEN_REF_ENV,
    PINCHTAB_URL_ENV,
)
from .daemon import build_daemon_config, ensure_daemon
from openminion.services.lifecycle.sidecars import ensure_pinchtab_autostart

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


@dataclass(frozen=True)
class PinchTabProviderConfig:
    base_url: str = DEFAULT_PINCHTAB_BASE_URL
    token: str | None = None
    token_ref: str | None = None
    api_timeout_s: int = DEFAULT_PINCHTAB_API_TIMEOUT_SECONDS
    navigate_timeout_s: int = DEFAULT_PINCHTAB_NAV_TIMEOUT_SECONDS
    outputs_root_dir: str = "${data_root}/browser"
    allow_remote_base_url: bool = False
    autostart: bool = False
    autostart_timeout_s: int = DEFAULT_PINCHTAB_AUTOSTART_TIMEOUT_SECONDS
    autostart_cmd: tuple[str, ...] = ()
    home_root_dir: str = ""
    autostart_env: tuple[tuple[str, str], ...] = ()

    def resolved_token(
        self, *, env: ToolEnv | Mapping[str, Any] | None = None
    ) -> str | None:
        if self.token:
            return self.token
        if self.token_ref and self.token_ref.startswith("secret:"):
            env_name = self.token_ref.split(":", 1)[1].strip()
            if env_name:
                value = str(resolve_tool_env(env=env).get(env_name, "")).strip()
                if value:
                    return value
        return None

    @classmethod
    def from_env(
        cls,
        *,
        workspace_root: str | None = None,
        data_root: str | None = None,
        env: ToolEnv | Mapping[str, Any] | None = None,
    ) -> "PinchTabProviderConfig":
        resolved_env = resolve_tool_env(env=env)
        workspace_root = str(
            resolve_tool_workspace_root(
                workspace_root=workspace_root,
                env=resolved_env,
                fallback=os.getcwd(),
            )
        )
        data_root_path = resolve_tool_data_root(
            data_root=data_root,
            env=resolved_env,
        )
        default_outputs = str(
            (data_root_path / DEFAULT_PINCHTAB_OUTPUTS_SUBPATH).resolve(strict=False)
        )
        home_root_dir = str(
            (data_root_path / DEFAULT_PINCHTAB_RUNTIME_SUBPATH).resolve(strict=False)
        )
        autostart_cmd = _parse_launch_cmd(resolved_env.get(PINCHTAB_LAUNCH_CMD_ENV, ""))
        return cls(
            base_url=str(resolved_env.get(PINCHTAB_URL_ENV, DEFAULT_PINCHTAB_BASE_URL)),
            token=str(resolved_env.get(PINCHTAB_TOKEN_ENV, "")).strip() or None,
            token_ref=str(resolved_env.get(PINCHTAB_TOKEN_REF_ENV, "")).strip() or None,
            api_timeout_s=_int_env(
                PINCHTAB_TIMEOUT_SECONDS_ENV,
                DEFAULT_PINCHTAB_API_TIMEOUT_SECONDS,
                env=resolved_env,
            ),
            navigate_timeout_s=_int_env(
                PINCHTAB_NAV_TIMEOUT_SECONDS_ENV,
                DEFAULT_PINCHTAB_NAV_TIMEOUT_SECONDS,
                env=resolved_env,
            ),
            outputs_root_dir=default_outputs,
            allow_remote_base_url=_bool_env(
                PINCHTAB_ALLOW_REMOTE_ENV, False, env=resolved_env
            ),
            autostart=_bool_env(PINCHTAB_AUTOSTART_ENV, False, env=resolved_env),
            autostart_timeout_s=_int_env(
                PINCHTAB_LAUNCH_TIMEOUT_SECONDS_ENV,
                DEFAULT_PINCHTAB_AUTOSTART_TIMEOUT_SECONDS,
                env=resolved_env,
            ),
            autostart_cmd=autostart_cmd,
            home_root_dir=home_root_dir,
            autostart_env=_parse_env_pairs(
                resolved_env.get(PINCHTAB_LAUNCH_ENV_ENV, "")
            ),
        )


def _bool_env(
    name: str,
    default: bool,
    *,
    env: ToolEnv | Mapping[str, Any] | None = None,
) -> bool:
    raw = str(resolve_tool_env(env=env).get(name, "")).strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _int_env(
    name: str,
    default: int,
    *,
    env: ToolEnv | Mapping[str, Any] | None = None,
) -> int:
    raw = str(resolve_tool_env(env=env).get(name, "")).strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _parse_launch_cmd(value: str | Sequence[str] | None) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        tokens = [str(item) for item in value if str(item).strip()]
        return tuple(tokens)
    raw = str(value).strip()
    if not raw:
        return ()
    return tuple(shlex.split(raw))


def _parse_env_pairs(
    raw: str | Sequence[tuple[str, str]] | None,
) -> tuple[tuple[str, str], ...]:
    if raw is None:
        return ()
    if isinstance(raw, (list, tuple)):
        pairs: list[tuple[str, str]] = []
        for item in raw:
            if not item:
                continue
            key = str(item[0])
            value = str(item[1]) if len(item) > 1 else ""
            if key:
                pairs.append((key, value))
        return tuple(pairs)
    cleaned = str(raw).strip()
    if not cleaned:
        return ()
    pairs: list[tuple[str, str]] = []
    for chunk in cleaned.split(","):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        key, value = chunk.split("=", 1)
        key = key.strip()
        if key:
            pairs.append((key, value.strip()))
    return tuple(pairs)


def _resolve_outputs_root(value: str, *, data_root: Path) -> str:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = data_root / candidate
    resolved = ensure_under_data_root(
        candidate, data_root, label="pinchtab_outputs_root"
    )
    return str(resolved.resolve(strict=False))


class PinchTabProvider:
    provider_id = "pinchtab"
    provider_version = "2"
    capabilities = BrowserCapabilities(
        snapshot_refs=True,
        selector_actions=False,
        batch_actions=True,
        pdf_export=True,
        cookies=False,
        js_evaluate=False,
        tab_locking=True,
        persistent_profiles=True,
        headed_mode=True,
        downloads=False,
    )

    def __init__(
        self,
        config: PinchTabProviderConfig | None = None,
        *,
        env: ToolEnv | Mapping[str, Any] | None = None,
    ) -> None:
        self._env = resolve_tool_env(env=env)
        self.config = config or PinchTabProviderConfig.from_env(env=self._env)
        self._enforce_localhost_policy()

    @classmethod
    def from_config(
        cls,
        cfg: Mapping[str, Any],
        *,
        env: ToolEnv | Mapping[str, Any] | None = None,
    ) -> "PinchTabProvider":
        resolved_env = resolve_tool_env(env=env)
        str(
            resolve_tool_workspace_root(
                workspace_root=cfg.get("workspace_root"),
                env=resolved_env,
                fallback=os.getcwd(),
            )
        )
        data_root_path = resolve_tool_data_root(
            data_root=cfg.get("data_root"),
            env=resolved_env,
        )
        default_outputs = str(
            (data_root_path / DEFAULT_PINCHTAB_OUTPUTS_SUBPATH).resolve(strict=False)
        )
        home_root_dir = str(
            (data_root_path / DEFAULT_PINCHTAB_RUNTIME_SUBPATH).resolve(strict=False)
        )
        autostart_cmd = _parse_launch_cmd(
            cfg.get("autostart_cmd") or resolved_env.get(PINCHTAB_LAUNCH_CMD_ENV, "")
        )
        autostart_env = _parse_env_pairs(
            cfg.get("autostart_env") or resolved_env.get(PINCHTAB_LAUNCH_ENV_ENV, "")
        )
        return cls(
            PinchTabProviderConfig(
                base_url=str(cfg.get("base_url", DEFAULT_PINCHTAB_BASE_URL)),
                token=str(cfg.get("token", "")).strip() or None,
                token_ref=str(cfg.get("token_ref", "")).strip() or None,
                api_timeout_s=int(
                    cfg.get("timeouts", {}).get(
                        "api_s", DEFAULT_PINCHTAB_API_TIMEOUT_SECONDS
                    )
                )
                if isinstance(cfg.get("timeouts"), Mapping)
                else DEFAULT_PINCHTAB_API_TIMEOUT_SECONDS,
                navigate_timeout_s=int(
                    cfg.get("timeouts", {}).get(
                        "navigate_s", DEFAULT_PINCHTAB_NAV_TIMEOUT_SECONDS
                    )
                )
                if isinstance(cfg.get("timeouts"), Mapping)
                else DEFAULT_PINCHTAB_NAV_TIMEOUT_SECONDS,
                outputs_root_dir=_resolve_outputs_root(
                    str(cfg.get("outputs", {}).get("root_dir", default_outputs))
                    if isinstance(cfg.get("outputs"), Mapping)
                    else default_outputs,
                    data_root=data_root_path,
                ),
                allow_remote_base_url=bool(cfg.get("allow_remote_base_url", False)),
                autostart=bool(cfg.get("autostart", False))
                or _bool_env(PINCHTAB_AUTOSTART_ENV, False, env=resolved_env),
                autostart_timeout_s=int(
                    cfg.get(
                        "autostart_timeout_s",
                        DEFAULT_PINCHTAB_AUTOSTART_TIMEOUT_SECONDS,
                    )
                ),
                autostart_cmd=autostart_cmd,
                home_root_dir=_resolve_outputs_root(
                    str(cfg.get("home_root_dir", home_root_dir)),
                    data_root=data_root_path,
                ),
                autostart_env=autostart_env,
            ),
            env=resolved_env,
        )

    def _enforce_localhost_policy(self) -> None:
        host, _, _ = parse_base_url_targets(self.config.base_url)
        if host in _LOCAL_HOSTS:
            return
        if self.config.allow_remote_base_url:
            return
        raise ValueError(
            "PinchTab base_url must be localhost unless allow_remote_base_url=true"
        )

    def _runtime_env(self, ctx: BrowserProviderContext | None = None) -> ToolEnv:
        if isinstance(ctx, BrowserProviderContext) and isinstance(
            getattr(ctx, "tool_context", None), RuntimeContext
        ):
            return ctx.tool_context.env
        return self._env

    def _client(
        self,
        *,
        for_navigate: bool = False,
        ensure: bool = True,
        ctx: BrowserProviderContext | None = None,
    ) -> PinchTabClient:
        runtime_env = self._runtime_env(ctx)
        if ensure and self._should_autostart(ctx=ctx):
            self._ensure_daemon_ready(ctx=ctx)
        timeout = (
            self.config.navigate_timeout_s
            if for_navigate
            else self.config.api_timeout_s
        )
        return PinchTabClient(
            PinchTabClientConfig(
                base_url=self.config.base_url,
                token=self.config.resolved_token(env=runtime_env),
                timeout_s=max(1, int(timeout)),
            )
        )

    def _should_autostart(self, *, ctx: BrowserProviderContext | None = None) -> bool:
        runtime_env = self._runtime_env(ctx)
        if self.config.autostart or _bool_env(
            PINCHTAB_AUTOSTART_ENV, False, env=runtime_env
        ):
            return True
        config_path = (
            str(runtime_env.get(OPENMINION_CONFIG_PATH_ENV, "")).strip() or None
        )
        autostart = ensure_pinchtab_autostart(
            config_path=config_path,
            runtime_env=runtime_env.snapshot(),
            interactive=bool(sys.stdin.isatty()),
            logger=logging.getLogger("openminion.sidecars"),
        )
        return bool(autostart.get("enabled"))

    def _ensure_daemon_ready(
        self, *, ctx: BrowserProviderContext | None = None
    ) -> None:
        host, _, _ = parse_base_url_targets(self.config.base_url)
        if host and host not in _LOCAL_HOSTS:
            return
        if not self.config.home_root_dir:
            return
        try:
            client = self._client(ensure=False, ctx=ctx)
            client.health()
            return
        except PinchTabClientError:
            pass
        daemon_cfg = build_daemon_config(
            base_url=self.config.base_url,
            runtime_dir=Path(self.config.home_root_dir),
            launch_cmd=self.config.autostart_cmd,
            launch_timeout_s=self.config.autostart_timeout_s,
            env=dict(self.config.autostart_env),
        )
        ensure_daemon(
            daemon_cfg, check_fn=lambda: self._client(ensure=False, ctx=ctx).health()
        )

    def resource_selectors(self, args: Mapping[str, Any]) -> ResourceSelectors:
        del args
        host, port, scheme = parse_base_url_targets(self.config.base_url)
        env_keys: tuple[str, ...] = ()
        if self.config.token_ref and self.config.token_ref.startswith("secret:"):
            env_key = self.config.token_ref.split(":", 1)[1].strip()
            if env_key:
                env_keys = (env_key,)
        return ResourceSelectors(
            hosts=(host,) if host else (),
            ports=(port,),
            protocols=(scheme,),
            env_keys_requested=env_keys,
        )

    def ensure_ready(self, ctx: BrowserProviderContext | None = None) -> Dict[str, Any]:
        payload = self._client(ctx=ctx).health()
        return {"ok": bool(payload.get("ok", True)), "raw": payload}

    def instance_start(
        self,
        ctx: BrowserProviderContext | None = None,
        spec: InstanceSpec | Mapping[str, Any] | None = None,
        *,
        profile: str | None = None,
        mode: str | None = None,
        port: int | None = None,
    ) -> Dict[str, Any]:
        instance_spec = self._instance_spec(
            spec=spec, profile=profile, mode=mode, port=port
        )
        payload = self._client(ctx=ctx).instance_start(
            profile_id=instance_spec.profile,
            mode=instance_spec.mode,
            port=instance_spec.port,
        )
        instance_id = str(
            payload.get("id")
            or payload.get("instanceId")
            or payload.get("instance_id")
            or ""
        )
        return {
            "instance": {
                "id": instance_id,
                "profile": instance_spec.profile,
                "mode": instance_spec.mode or str(payload.get("mode", "default")),
            },
            "raw": payload,
        }

    def instance_stop(
        self, ctx: BrowserProviderContext | None = None, instance_id: str = ""
    ) -> Dict[str, Any]:
        payload = self._client(ctx=ctx).instance_stop(instance_id=instance_id)
        return {
            "instance": {"id": instance_id, "profile": None, "mode": None},
            "stopped": bool(payload.get("stopped", True)),
            "raw": payload,
        }

    def instance_list(
        self, ctx: BrowserProviderContext | None = None
    ) -> Dict[str, Any]:
        payload = self._client(ctx=ctx).instance_list()
        rows: list[Any] = []
        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, Mapping):
            for key in ("instances", "items", "data", "result"):
                value = payload.get(key)
                if isinstance(value, list):
                    rows = value
                    break
                if isinstance(value, Mapping):
                    nested_items = value.get("items")
                    if isinstance(nested_items, list):
                        rows = nested_items
                        break
        instances: list[dict[str, str]] = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            instance_id = str(
                row.get("id") or row.get("instanceId") or row.get("instance_id") or ""
            ).strip()
            if not instance_id:
                continue
            instances.append(
                {
                    "id": instance_id,
                    "profile": str(row.get("profile") or row.get("profileId") or ""),
                    "mode": str(row.get("mode") or ""),
                    "status": str(row.get("status") or ""),
                }
            )
        return {"instances": instances, "raw": payload}

    def instance_kill(
        self, ctx: BrowserProviderContext | None = None, instance_id: str = ""
    ) -> Dict[str, Any]:
        payload = self._client(ctx=ctx).instance_kill(instance_id=instance_id)
        return {
            "instance": {"id": instance_id, "profile": None, "mode": None},
            "killed": bool(payload.get("killed", payload.get("stopped", True))),
            "raw": payload,
        }

    def tab_new(
        self,
        ctx: BrowserProviderContext | None = None,
        instance_id: str = "",
        url: str | None = None,
    ) -> Dict[str, Any]:
        payload = self._client(ctx=ctx).tab_new(instance_id=instance_id, url=url)
        tab_id = str(
            payload.get("id") or payload.get("tabId") or payload.get("tab_id") or ""
        )
        return {
            "tab": {
                "id": tab_id,
                "url": str(payload.get("url") or url or ""),
                "title": str(payload.get("title") or ""),
            },
            "raw": payload,
        }

    def tab_list(
        self, ctx: BrowserProviderContext | None = None, instance_id: str | None = None
    ) -> Dict[str, Any]:
        payload = self._client(ctx=ctx).tab_list(instance_id=instance_id)
        rows: list[Any] = []
        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, Mapping):
            for key in ("tabs", "items", "data", "result"):
                value = payload.get(key)
                if isinstance(value, list):
                    rows = value
                    break
                if isinstance(value, Mapping):
                    nested_items = value.get("items")
                    if isinstance(nested_items, list):
                        rows = nested_items
                        break
        tabs: list[dict[str, str]] = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            tabs.append(
                {
                    "id": str(
                        row.get("id") or row.get("tabId") or row.get("tab_id") or ""
                    ),
                    "url": str(row.get("url") or ""),
                    "title": str(row.get("title") or ""),
                }
            )
        return {"tabs": tabs, "raw": payload}

    def tab_close(
        self, ctx: BrowserProviderContext | None = None, tab_id: str = ""
    ) -> Dict[str, Any]:
        payload = self._client(ctx=ctx).tab_close(tab_id=tab_id)
        return {
            "tab": {"id": tab_id, "url": "", "title": ""},
            "closed": bool(payload.get("closed", True)),
            "raw": payload,
        }

    def tab_navigate(
        self,
        ctx: BrowserProviderContext | None = None,
        tab_id: str = "",
        url: str = "",
        options: NavigateOptions | Mapping[str, Any] | None = None,
    ) -> Dict[str, Any]:
        del options
        payload = self._client(for_navigate=True, ctx=ctx).navigate(
            tab_id=tab_id, url=url
        )
        return {
            "tab": {
                "id": tab_id,
                "url": str(payload.get("url") or url),
                "title": str(payload.get("title") or ""),
            },
            "raw": payload,
        }

    def tab_snapshot(
        self,
        ctx: BrowserProviderContext | None = None,
        tab_id: str = "",
        options: SnapshotOptions | Mapping[str, Any] | None = None,
    ) -> Dict[str, Any]:
        snapshot_options = self._snapshot_options(options)
        payload = self._client(ctx=ctx).snapshot(
            tab_id=tab_id,
            interactive=snapshot_options.interactive,
            compact=snapshot_options.compact,
            depth=snapshot_options.depth,
            max_tokens=snapshot_options.max_tokens,
        )
        root = (
            payload.get("root") if isinstance(payload.get("root"), Mapping) else payload
        )
        refs = _extract_interactive_refs(root)
        return {
            "snapshot": {
                "format": "refs",
                "nodes": [root],
                "interactive_refs": refs,
                "meta": {
                    "interactive": snapshot_options.interactive,
                    "compact": snapshot_options.compact,
                    "depth": snapshot_options.depth,
                    "max_tokens": snapshot_options.max_tokens,
                    "mode": snapshot_options.mode,
                },
            },
            "raw": payload,
        }

    def tab_text(
        self,
        ctx: BrowserProviderContext | None = None,
        tab_id: str = "",
        options: TextOptions | Mapping[str, Any] | None = None,
    ) -> Dict[str, Any]:
        text_options = self._text_options(options)
        payload = self._client(ctx=ctx).text(tab_id=tab_id, mode=text_options.mode)
        content = _extract_text(payload)
        return {
            "text": {"content": content, "truncated": False, "chars": len(content)},
            "raw": payload,
        }

    def tab_screenshot(
        self,
        ctx: BrowserProviderContext | None = None,
        tab_id: str = "",
        options: OutputOptions | Mapping[str, Any] | None = None,
    ) -> Dict[str, Any]:
        del options
        blob = self._client(ctx=ctx).screenshot(tab_id=tab_id)
        return {"kind": "screenshot", "content": blob}

    def tab_pdf(
        self,
        ctx: BrowserProviderContext | None = None,
        tab_id: str = "",
        options: OutputOptions | Mapping[str, Any] | None = None,
    ) -> Dict[str, Any]:
        del options
        blob = self._client(ctx=ctx).pdf(tab_id=tab_id)
        return {"kind": "pdf", "content": blob}

    def tab_action(
        self,
        ctx: BrowserProviderContext | None = None,
        tab_id: str = "",
        action: BrowserAction | Mapping[str, Any] | None = None,
    ) -> Dict[str, Any]:
        payload = self._client(ctx=ctx).action(
            tab_id=tab_id, action=self._action_payload(action)
        )
        return {"tab": {"id": tab_id, "url": "", "title": ""}, "raw": payload}

    def tab_actions(
        self,
        ctx: BrowserProviderContext | None = None,
        tab_id: str = "",
        actions: list[BrowserAction] | list[Mapping[str, Any]] | None = None,
    ) -> Dict[str, Any]:
        rows: list[Dict[str, Any]] = []
        for action in actions or []:
            rows.append(
                self._client(ctx=ctx).action(
                    tab_id=tab_id, action=self._action_payload(action)
                )
            )
        return {"tab": {"id": tab_id, "url": "", "title": ""}, "actions": rows}

    def tab_lock(
        self,
        ctx: BrowserProviderContext | None = None,
        tab_id: str = "",
        owner: str | None = None,
        ttl_s: int | None = None,
    ) -> Dict[str, Any]:
        payload = self._client(ctx=ctx).lock(tab_id=tab_id)
        return {
            "tab": {"id": tab_id, "url": "", "title": ""},
            "locked": True,
            "owner": owner,
            "ttl_s": ttl_s,
            "raw": payload,
        }

    def tab_unlock(
        self,
        ctx: BrowserProviderContext | None = None,
        tab_id: str = "",
        owner: str | None = None,
    ) -> Dict[str, Any]:
        del owner
        payload = self._client(ctx=ctx).unlock(tab_id=tab_id)
        return {
            "tab": {"id": tab_id, "url": "", "title": ""},
            "locked": False,
            "raw": payload,
        }

    def _instance_spec(
        self,
        *,
        spec: InstanceSpec | Mapping[str, Any] | None,
        profile: str | None,
        mode: str | None,
        port: int | None,
    ) -> InstanceSpec:
        if isinstance(spec, InstanceSpec):
            return spec
        if isinstance(spec, Mapping):
            return InstanceSpec.model_validate(dict(spec))
        return InstanceSpec(profile=profile, mode=mode, port=port)

    def _snapshot_options(
        self, options: SnapshotOptions | Mapping[str, Any] | None
    ) -> SnapshotOptions:
        if isinstance(options, SnapshotOptions):
            return options
        if isinstance(options, Mapping):
            return SnapshotOptions.model_validate(dict(options))
        return SnapshotOptions()

    def _text_options(
        self, options: TextOptions | Mapping[str, Any] | None
    ) -> TextOptions:
        if isinstance(options, TextOptions):
            return options
        if isinstance(options, Mapping):
            return TextOptions.model_validate(dict(options))
        return TextOptions()

    def _action_payload(
        self, action: BrowserAction | Mapping[str, Any] | None
    ) -> Dict[str, Any]:
        if isinstance(action, BrowserAction):
            return action.model_dump(exclude_none=True)
        if isinstance(action, Mapping):
            return dict(action)
        return {}


def _extract_interactive_refs(value: Any) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, Mapping):
            ref = node.get("ref") or node.get("id")
            if ref is not None:
                token = str(ref).strip()
                if token and token not in seen:
                    seen.add(token)
                    out.append(token)
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    return out


def _extract_text(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    if isinstance(payload, Mapping):
        for key in ("text", "content", "readability", "body", "markdown"):
            value = payload.get(key)
            if isinstance(value, str):
                return value
    return str(payload)
