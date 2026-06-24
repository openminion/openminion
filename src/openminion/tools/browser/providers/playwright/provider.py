from typing import Any, Mapping

from openminion.base.config.env import EnvironmentConfig, resolve_environment_config
from openminion.modules.tool.runtime.resource_selectors import ResourceSelectors
from openminion.tools.browser import BrowserCapabilities, BrowserProviderContext
from openminion.tools.browser.models import (
    BrowserAction,
    InstanceSpec,
    NavigateOptions,
    OutputOptions,
    SnapshotOptions,
    TextOptions,
)

from .artifacts import ArtifactWriter
from .config import PlaywrightProviderConfig, provider_config_from_mapping
from .instances import PlaywrightInstanceManager, PlaywrightTabManager
from .locks import BrowserTabLockedError, PlaywrightLockManager
from . import actions as _action_ops
from . import outputs as _output_ops
from . import runtime as _runtime_ops
from . import snapshot_text as _snapshot_text_ops
from .coercion import normalize_mode, response_status, safe_title, safe_url
from .selectors import SelectorAdapter
from .snapshots import SnapshotAdapter


class PlaywrightProvider:
    provider_id = "playwright"
    provider_version = "1"

    def __init__(
        self,
        config: PlaywrightProviderConfig | None = None,
        *,
        playwright_factory: Any | None = None,
        env: EnvironmentConfig | Mapping[str, Any] | None = None,
    ) -> None:
        self._env = resolve_environment_config(env=env)
        self.config = config or provider_config_from_mapping(env=self._env)
        self._playwright_factory = playwright_factory
        self._playwright_manager: Any | None = None
        self._playwright: Any | None = None

        self._instances = PlaywrightInstanceManager()
        self._tabs = PlaywrightTabManager()
        self._locks = PlaywrightLockManager(
            default_ttl_s=max(60, int(self.config.timeouts.action_ms / 1000) * 10)
        )

        self._selector_adapter = SelectorAdapter()
        self._snapshot_adapter = SnapshotAdapter()
        self._artifact_writer = ArtifactWriter(
            workspace_root=self.config.workspace_root,
            downloads_dir=self.config.downloads.dir,
            screenshots_dir=self.config.artifacts.screenshots_dir,
            pdf_dir=self.config.artifacts.pdf_dir,
            traces_dir=self.config.artifacts.traces_dir,
            allowed_roots=(
                self.config.workspace_root,
                self.config.downloads.dir,
                self.config.persistent.user_data_dir,
                self.config.artifacts.root_dir,
                self.config.artifacts.screenshots_dir,
                self.config.artifacts.pdf_dir,
                self.config.artifacts.traces_dir,
            ),
        )
        self._artifact_writer.ensure_dirs()

        self.capabilities = BrowserCapabilities(
            snapshot_refs=False,
            tab_locking=True,
            pdf_export=self.config.browser_is_chromium,
            screenshot=True,
            text=True,
            selectors=True,
            role_selectors=True,
            trace=True,
            network_intercept=True,
            persistent_profiles=True,
        )

    @classmethod
    def from_config(
        cls,
        cfg: Mapping[str, Any],
        *,
        env: EnvironmentConfig | Mapping[str, Any] | None = None,
    ) -> "PlaywrightProvider":
        return cls(provider_config_from_mapping(cfg, env=env), env=env)

    def resource_selectors(self, args: Mapping[str, Any]) -> ResourceSelectors:
        return _runtime_ops.resource_selectors(self, args)

    def ensure_ready(self, ctx: BrowserProviderContext | None = None) -> dict[str, Any]:
        del ctx
        return _runtime_ops.ensure_ready(self)

    def instance_start(
        self,
        ctx: BrowserProviderContext | None = None,
        spec: InstanceSpec | Mapping[str, Any] | None = None,
        *,
        profile: str | None = None,
        mode: str | None = None,
        port: int | None = None,
    ) -> dict[str, Any]:
        del ctx
        instance_spec = self._instance_spec(
            spec=spec, profile=profile, mode=mode, port=port
        )
        profile = instance_spec.profile
        mode = instance_spec.mode
        port = instance_spec.port
        del port

        browser_type = self._browser_type()
        headless, normalized_mode = normalize_mode(
            mode, default_headless=self.config.headless_default
        )
        browser_name = self.config.browser

        viewport = {
            "width": self.config.viewport_width,
            "height": self.config.viewport_height,
        }
        # `downloads_path` is only accepted by `launch_persistent_context`;
        common_context: dict[str, Any] = {
            "locale": self.config.locale,
            "timezone_id": self.config.timezone_id,
            "viewport": viewport,
            "accept_downloads": self.config.downloads.accept_downloads,
        }

        if self.config.persistent.enabled:
            user_data_dir = self._resolve_profile_dir(profile)
            context = browser_type.launch_persistent_context(
                user_data_dir=str(user_data_dir),
                headless=headless,
                slow_mo=self.config.slow_mo_ms,
                downloads_path=str(self._artifact_writer.downloads_dir),
                **common_context,
            )
            browser = getattr(context, "browser", None)
        else:
            browser = browser_type.launch(
                headless=headless,
                slow_mo=self.config.slow_mo_ms,
                downloads_path=str(self._artifact_writer.downloads_dir),
            )
            context = browser.new_context(**common_context)

        context.set_default_timeout(self.config.timeouts.action_ms)
        context.set_default_navigation_timeout(self.config.timeouts.navigation_ms)

        inst = self._instances.add(
            context=context,
            browser=browser,
            persistent=self.config.persistent.enabled,
            profile=profile,
            mode=normalized_mode,
            browser_name=browser_name,
        )
        return {
            "instance": {
                "id": inst.id,
                "profile": profile,
                "mode": normalized_mode,
            }
        }

    def instance_stop(
        self, ctx: BrowserProviderContext | None = None, instance_id: str = ""
    ) -> dict[str, Any]:
        del ctx
        inst = self._instances.remove(instance_id)
        for tab in self._tabs.clear_for_instance(instance_id):
            try:
                tab.page.close()
            except Exception:
                pass

        try:
            inst.context.close()
        except Exception:
            pass
        if inst.browser is not None:
            try:
                inst.browser.close()
            except Exception:
                pass

        if len(self._instances) == 0:
            self._shutdown_playwright()

        return {
            "instance": {
                "id": inst.id,
                "profile": inst.profile,
                "mode": inst.mode,
            },
            "stopped": True,
        }

    def instance_list(
        self, ctx: BrowserProviderContext | None = None
    ) -> dict[str, Any]:
        del ctx
        rows = self._instances.list()
        instances: list[dict[str, str | bool | None]] = []
        for row in rows:
            instances.append(
                {
                    "id": row.id,
                    "profile": row.profile,
                    "mode": row.mode,
                    "browser": row.browser_name,
                    "persistent": row.persistent,
                }
            )
        return {"instances": instances}

    def instance_kill(
        self, ctx: BrowserProviderContext | None = None, instance_id: str = ""
    ) -> dict[str, Any]:
        payload = self.instance_stop(ctx=ctx, instance_id=instance_id)
        payload["killed"] = True
        return payload

    def tab_new(
        self,
        ctx: BrowserProviderContext | None = None,
        instance_id: str = "",
        url: str | None = None,
    ) -> dict[str, Any]:
        del ctx
        inst = self._instances.get(instance_id)
        page = inst.context.new_page()
        if url:
            self._enforce_network_policy(url)
            page.goto(url, timeout=self.config.timeouts.navigation_ms)
        tab = self._tabs.add(instance_id=instance_id, page=page)
        return {
            "tab": {
                "id": tab.id,
                "url": safe_url(page),
                "title": safe_title(page),
            }
        }

    def tab_list(
        self, ctx: BrowserProviderContext | None = None, instance_id: str | None = None
    ) -> dict[str, Any]:
        del ctx
        if instance_id:
            self._instances.get(instance_id)
        rows = self._tabs.list(instance_id=instance_id)
        tabs: list[dict[str, str]] = []
        for row in rows:
            tabs.append(
                {"id": row.id, "url": safe_url(row.page), "title": safe_title(row.page)}
            )
        return {"tabs": tabs}

    def tab_close(
        self, ctx: BrowserProviderContext | None = None, tab_id: str = ""
    ) -> dict[str, Any]:
        del ctx
        tab = self._tabs.remove(tab_id)
        try:
            tab.page.close()
        except Exception:
            pass
        return {
            "tab": {
                "id": tab.id,
                "url": "",
                "title": "",
            },
            "closed": True,
        }

    def navigate(self, *, tab_id: str, url: str) -> dict[str, Any]:
        tab = self._tabs.get(tab_id)
        self._enforce_network_policy(url)
        key = self._lock_key(tab_id)
        with self._locks.action_lock(key):
            response = tab.page.goto(url, timeout=self.config.timeouts.navigation_ms)
        return {
            "tab": {
                "id": tab.id,
                "url": safe_url(tab.page),
                "title": safe_title(tab.page),
            },
            "response_status": response_status(response),
        }

    def tab_navigate(
        self,
        ctx: BrowserProviderContext | None = None,
        tab_id: str = "",
        url: str = "",
        options: NavigateOptions | Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        del ctx, options
        return self.navigate(tab_id=tab_id, url=url)

    def snapshot(
        self,
        *,
        tab_id: str,
        mode: str = "a11y",
        max_nodes: int = 800,
        max_text_chars: int = 20000,
        interactive: bool = True,
        compact: bool = True,
        depth: int | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        return _snapshot_text_ops.snapshot(
            self,
            tab_id=tab_id,
            mode=mode,
            max_nodes=max_nodes,
            max_text_chars=max_text_chars,
            interactive=interactive,
            compact=compact,
            depth=depth,
            max_tokens=max_tokens,
        )

    def tab_snapshot(
        self,
        ctx: BrowserProviderContext | None = None,
        tab_id: str = "",
        options: SnapshotOptions | Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        del ctx
        return _snapshot_text_ops.tab_snapshot(self, tab_id=tab_id, options=options)

    def text(self, *, tab_id: str, mode: str = "visible_text") -> dict[str, Any]:
        return _snapshot_text_ops.text(self, tab_id=tab_id, mode=mode)

    def tab_text(
        self,
        ctx: BrowserProviderContext | None = None,
        tab_id: str = "",
        options: TextOptions | Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        del ctx
        return _snapshot_text_ops.tab_text(self, tab_id=tab_id, options=options)

    def screenshot(
        self, *, tab_id: str, output_path: str | None = None
    ) -> dict[str, Any]:
        return _output_ops.screenshot(self, tab_id=tab_id, output_path=output_path)

    def tab_screenshot(
        self,
        ctx: BrowserProviderContext | None = None,
        tab_id: str = "",
        options: OutputOptions | Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        del ctx
        return _output_ops.tab_screenshot(self, tab_id=tab_id, options=options)

    def pdf(self, *, tab_id: str, output_path: str | None = None) -> dict[str, Any]:
        return _output_ops.pdf(self, tab_id=tab_id, output_path=output_path)

    def tab_pdf(
        self,
        ctx: BrowserProviderContext | None = None,
        tab_id: str = "",
        options: OutputOptions | Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        del ctx
        return _output_ops.tab_pdf(self, tab_id=tab_id, options=options)

    def action(self, *, tab_id: str, action: Mapping[str, Any]) -> dict[str, Any]:
        return _action_ops.action(self, tab_id=tab_id, action=action)

    def tab_action(
        self,
        ctx: BrowserProviderContext | None = None,
        tab_id: str = "",
        action: BrowserAction | Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        del ctx
        return _action_ops.tab_action(self, tab_id=tab_id, action=action)

    def actions(
        self, *, tab_id: str, actions: list[Mapping[str, Any]]
    ) -> dict[str, Any]:
        return _action_ops.actions(self, tab_id=tab_id, actions=actions)

    def tab_actions(
        self,
        ctx: BrowserProviderContext | None = None,
        tab_id: str = "",
        actions: list[BrowserAction] | list[Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        del ctx
        return _action_ops.tab_actions(self, tab_id=tab_id, actions=actions)

    def lock(self, *, tab_id: str) -> dict[str, Any]:
        self._tabs.get(tab_id)
        locked = self._locks.lock(self._lock_key(tab_id))
        return {"tab": {"id": tab_id, "url": "", "title": ""}, "locked": locked}

    def tab_lock(
        self,
        ctx: BrowserProviderContext | None = None,
        tab_id: str = "",
        owner: str | None = None,
        ttl_s: int | None = None,
    ) -> dict[str, Any]:
        del ctx, owner, ttl_s
        return self.lock(tab_id=tab_id)

    def unlock(self, *, tab_id: str) -> dict[str, Any]:
        unlocked = self._locks.unlock(self._lock_key(tab_id))
        return {
            "tab": {"id": tab_id, "url": "", "title": ""},
            "locked": False,
            "unlocked": unlocked,
        }

    def tab_unlock(
        self,
        ctx: BrowserProviderContext | None = None,
        tab_id: str = "",
        owner: str | None = None,
    ) -> dict[str, Any]:
        del ctx, owner
        return self.unlock(tab_id=tab_id)

    def upload(
        self,
        *,
        tab_id: str,
        files: list[str],
        selector: str | None = None,
        role: Mapping[str, Any] | None = None,
        node_id: str | None = None,
    ) -> dict[str, Any]:
        return _output_ops.upload(
            self,
            tab_id=tab_id,
            files=files,
            selector=selector,
            role=role,
            node_id=node_id,
        )

    # Internal compatibility wrappers -------------------------------------------------
    def _apply_action(self, *, tab: Any, action: Mapping[str, Any]) -> dict[str, Any]:
        return _action_ops.apply_action(self, tab=tab, action=action)

    def _extract_text(self, page: Any, *, mode: str) -> str:
        return _snapshot_text_ops.extract_text(self, page, mode=mode)

    def _enforce_network_policy(self, url: str) -> None:
        _runtime_ops.enforce_network_policy(self, url)

    def _resolve_workspace_file(self, raw_path: str) -> str:
        return _output_ops.resolve_workspace_file(self, raw_path)

    def _to_workspace_relative(self, path: str) -> str:
        return _output_ops.to_workspace_relative(self, path)

    def _resolve_profile_dir(self, profile: str | None) -> str:
        return _output_ops.resolve_profile_dir(self, profile)

    def _instance_spec(
        self,
        *,
        spec: InstanceSpec | Mapping[str, Any] | None,
        profile: str | None,
        mode: str | None,
        port: int | None,
    ) -> InstanceSpec:
        return _runtime_ops.instance_spec(
            spec=spec, profile=profile, mode=mode, port=port
        )

    def _snapshot_options(
        self, options: SnapshotOptions | Mapping[str, Any] | None
    ) -> SnapshotOptions:
        return _snapshot_text_ops.snapshot_options(options)

    def _text_options(
        self, options: TextOptions | Mapping[str, Any] | None
    ) -> TextOptions:
        return _snapshot_text_ops.text_options(options)

    def _output_options(
        self, options: OutputOptions | Mapping[str, Any] | None
    ) -> OutputOptions:
        return _output_ops.output_options(options)

    def _action_payload(
        self, action: BrowserAction | Mapping[str, Any] | None
    ) -> dict[str, Any]:
        return _action_ops.action_payload(action)

    def _browser_type(self) -> Any:
        return _runtime_ops.browser_type(self)

    def _ensure_playwright(self) -> Any:
        return _runtime_ops.ensure_playwright(self)

    def _shutdown_playwright(self) -> None:
        _runtime_ops.shutdown_playwright(self)

    def _provider_version(self) -> str:
        return _runtime_ops.provider_version(self)

    def _lock_key(self, tab_id: str) -> str:
        return _runtime_ops.lock_key(self, tab_id)


__all__ = ["PlaywrightProvider", "PlaywrightProviderConfig", "BrowserTabLockedError"]
