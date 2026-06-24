from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from .models import (
    BrowserCallArgs,
    BrowserOp,
    InstanceInfo,
    TabInfo,
    normalize_op,
)
from .providers import BrowserProvider, BrowserProviderContext
from .payloads import to_payload

StateFor = Callable[[str, Any], Any]
InstanceSpecFactory = Callable[[BrowserCallArgs, Any], Any]
ResolveTab = Callable[
    [BrowserProvider, BrowserProviderContext, BrowserCallArgs, bool],
    tuple[str, TabInfo | None, dict[str, Any]],
]
FilterTabs = Callable[
    [list[TabInfo], Mapping[str, Any]], tuple[list[TabInfo], dict[str, Any]]
]
AsBool = Callable[[Any, bool], bool]
ClearSessionState = Callable[[str, Any], None]
ExtractTabs = Callable[[Any], list[TabInfo]]
ExtractInstances = Callable[[Any], list[InstanceInfo]]
ExtractIdentifier = Callable[[Mapping[str, Any]], str]
IsStaleRecoverableError = Callable[[Exception], bool]
ErrorFactory = Callable[[str, str, Mapping[str, Any] | None], Exception]
DispatchHandler = Callable[
    [BrowserProvider, BrowserProviderContext, BrowserCallArgs], dict[str, Any]
]


@dataclass
class BrowserDispatch:
    state_for: StateFor
    instance_spec: InstanceSpecFactory
    resolve_tab: ResolveTab
    filter_tabs: FilterTabs
    as_bool: AsBool
    clear_session_state: ClearSessionState
    extract_tabs: ExtractTabs
    extract_instances: ExtractInstances
    extract_instance_id: ExtractIdentifier
    extract_tab_id: ExtractIdentifier
    is_stale_recoverable_error: IsStaleRecoverableError
    error_factory: ErrorFactory
    _handlers: dict[str, DispatchHandler] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._handlers = {
            BrowserOp.DAEMON_ENSURE.value: self._handle_daemon_ensure,
            BrowserOp.INSTANCE_START.value: self._handle_instance_start,
            BrowserOp.INSTANCE_LIST.value: self._handle_instance_list,
            BrowserOp.INSTANCE_STOP.value: self._handle_instance_stop,
            BrowserOp.INSTANCE_KILL.value: self._handle_instance_kill,
            BrowserOp.TAB_NEW.value: self._handle_tab_new,
            BrowserOp.TAB_LIST.value: self._handle_tab_list,
            BrowserOp.TAB_SELECT.value: self._handle_tab_select,
            BrowserOp.TAB_CLOSE.value: self._handle_tab_close,
            BrowserOp.TAB_NAVIGATE.value: self._handle_tab_navigate,
            BrowserOp.TAB_SNAPSHOT.value: self._handle_tab_snapshot,
            BrowserOp.TAB_TEXT.value: self._handle_tab_text,
            BrowserOp.TAB_SCREENSHOT.value: self._handle_tab_screenshot,
            BrowserOp.TAB_PDF.value: self._handle_tab_pdf,
            BrowserOp.TAB_ACTION.value: self._handle_tab_action,
            BrowserOp.TAB_ACTIONS.value: self._handle_tab_actions,
            BrowserOp.TAB_LOCK.value: self._handle_tab_lock,
            BrowserOp.TAB_UNLOCK.value: self._handle_tab_unlock,
        }

    def dispatch(
        self,
        *,
        provider: BrowserProvider,
        provider_ctx: BrowserProviderContext,
        call: BrowserCallArgs,
    ) -> dict[str, Any]:
        op = normalize_op(call.op)
        handler = self._handlers.get(op)
        if handler is None:
            raise self._error("INVALID_ARGUMENT", f"unsupported browser op: {op}")
        return handler(provider, provider_ctx, call)

    def _error(
        self,
        code: str,
        message: str,
        details: Mapping[str, Any] | None = None,
    ) -> Exception:
        return self.error_factory(code, message, details)

    def _state_instance_id(
        self,
        *,
        provider_id: str,
        provider_ctx: BrowserProviderContext,
        call: BrowserCallArgs,
    ) -> str:
        state = self.state_for(provider_id, provider_ctx)
        return str(call.instance_id or state.instance_id or "").strip()

    def _state_tab_id(
        self,
        *,
        provider_id: str,
        provider_ctx: BrowserProviderContext,
        call: BrowserCallArgs,
    ) -> str:
        state = self.state_for(provider_id, provider_ctx)
        return str(call.tab_id or state.tab_id or "").strip()

    def _resolve_required_tab_id(
        self,
        *,
        provider: BrowserProvider,
        provider_ctx: BrowserProviderContext,
        call: BrowserCallArgs,
        error_message: str,
    ) -> str:
        tab_id = str(call.tab_id or "").strip()
        if not tab_id:
            tab_id, _, _ = self.resolve_tab(provider, provider_ctx, call, False)
        if not tab_id:
            raise self._error("INVALID_ARGUMENT", error_message)
        return tab_id

    def _handle_daemon_ensure(
        self,
        provider: BrowserProvider,
        provider_ctx: BrowserProviderContext,
        call: BrowserCallArgs,
    ) -> dict[str, Any]:
        del call
        return to_payload(provider.ensure_ready(provider_ctx))

    def _handle_instance_start(
        self,
        provider: BrowserProvider,
        provider_ctx: BrowserProviderContext,
        call: BrowserCallArgs,
    ) -> dict[str, Any]:
        spec = self.instance_spec(call, provider_ctx)
        return to_payload(provider.instance_start(provider_ctx, spec))

    def _handle_instance_list(
        self,
        provider: BrowserProvider,
        provider_ctx: BrowserProviderContext,
        call: BrowserCallArgs,
    ) -> dict[str, Any]:
        del call
        payload = provider.instance_list(provider_ctx)
        payload_map = to_payload(payload)
        payload_map["instances"] = [
            row.model_dump(exclude_none=True)
            for row in self.extract_instances(payload_map)
        ]
        payload_map["resolution"] = {
            "strategy": "instance.list",
            "instance_count": len(payload_map["instances"]),
        }
        return payload_map

    def _handle_instance_stop(
        self,
        provider: BrowserProvider,
        provider_ctx: BrowserProviderContext,
        call: BrowserCallArgs,
    ) -> dict[str, Any]:
        instance_id = self._state_instance_id(
            provider_id=provider.provider_id,
            provider_ctx=provider_ctx,
            call=call,
        )
        if not instance_id:
            raise self._error(
                "INVALID_ARGUMENT", "instance_id is required for instance.stop"
            )
        return to_payload(provider.instance_stop(provider_ctx, instance_id))

    def _handle_instance_kill(
        self,
        provider: BrowserProvider,
        provider_ctx: BrowserProviderContext,
        call: BrowserCallArgs,
    ) -> dict[str, Any]:
        instance_id = self._state_instance_id(
            provider_id=provider.provider_id,
            provider_ctx=provider_ctx,
            call=call,
        )
        if not instance_id:
            raise self._error(
                "INVALID_ARGUMENT", "instance_id is required for instance.kill"
            )
        return to_payload(provider.instance_kill(provider_ctx, instance_id))

    def _handle_tab_new(
        self,
        provider: BrowserProvider,
        provider_ctx: BrowserProviderContext,
        call: BrowserCallArgs,
    ) -> dict[str, Any]:
        instance_id = self._state_instance_id(
            provider_id=provider.provider_id,
            provider_ctx=provider_ctx,
            call=call,
        )
        payload: dict[str, Any] = {}
        if not instance_id:
            spec = self.instance_spec(call, provider_ctx)
            started = to_payload(provider.instance_start(provider_ctx, spec))
            instance_id = self.extract_instance_id(started)
            payload.update(started)
        if not instance_id:
            raise self._error("INVALID_ARGUMENT", "instance_id is required for tab.new")
        opened = to_payload(provider.tab_new(provider_ctx, instance_id, url=call.url))
        if not payload:
            return opened
        payload.update(opened)
        payload.setdefault("instance", {"id": instance_id})
        payload.setdefault("instance_id", instance_id)
        return payload

    def _handle_tab_list(
        self,
        provider: BrowserProvider,
        provider_ctx: BrowserProviderContext,
        call: BrowserCallArgs,
    ) -> dict[str, Any]:
        instance_id = (
            self._state_instance_id(
                provider_id=provider.provider_id,
                provider_ctx=provider_ctx,
                call=call,
            )
            or None
        )
        payload = provider.tab_list(provider_ctx, instance_id=instance_id)
        payload_map = to_payload(payload)
        tabs = self.extract_tabs(payload_map)
        options = call.options if isinstance(call.options, Mapping) else {}
        filtered_tabs, filter_meta = self.filter_tabs(tabs, options)
        payload_map["tabs"] = [
            tab.model_dump(exclude_none=True) for tab in filtered_tabs
        ]
        payload_map["resolution"] = {
            "strategy": "tab.list",
            "tab_count": len(tabs),
            "filtered_count": len(filtered_tabs),
            **filter_meta,
        }
        if self.as_bool(options.get("select"), False) and len(filtered_tabs) == 1:
            payload_map["tab"] = filtered_tabs[0].model_dump(exclude_none=True)
        return payload_map

    def _handle_tab_select(
        self,
        provider: BrowserProvider,
        provider_ctx: BrowserProviderContext,
        call: BrowserCallArgs,
    ) -> dict[str, Any]:
        if call.tab_id:
            return {
                "tab": {"id": call.tab_id, "url": "", "title": ""},
                "resolution": {"strategy": "call.tab_id"},
            }
        tab_id, tab, details = self.resolve_tab(
            provider, provider_ctx, call, bool(call.url)
        )
        if not tab_id:
            raise self._error(
                "NOT_FOUND",
                "unable to resolve tab from current provider tabs",
                {"resolution": details},
            )
        payload: dict[str, Any] = {
            "tab": tab.model_dump(exclude_none=True)
            if tab is not None
            else {"id": tab_id, "url": "", "title": ""},
            "resolution": details,
        }
        if call.instance_id:
            payload["instance"] = {"id": call.instance_id}
        return payload

    def _handle_tab_close(
        self,
        provider: BrowserProvider,
        provider_ctx: BrowserProviderContext,
        call: BrowserCallArgs,
    ) -> dict[str, Any]:
        tab_id = self._resolve_required_tab_id(
            provider=provider,
            provider_ctx=provider_ctx,
            call=call,
            error_message="tab_id is required for tab.close",
        )
        return to_payload(provider.tab_close(provider_ctx, tab_id))

    def _handle_tab_navigate(
        self,
        provider: BrowserProvider,
        provider_ctx: BrowserProviderContext,
        call: BrowserCallArgs,
    ) -> dict[str, Any]:
        if not call.url:
            raise self._error("INVALID_ARGUMENT", "url is required for tab.navigate")
        if call.tab_id:
            tab_id = call.tab_id
            matched_tab = None
            details: dict[str, Any] = {"strategy": "call.tab_id"}
        else:
            tab_id, matched_tab, details = self.resolve_tab(
                provider, provider_ctx, call, True
            )
        if tab_id:
            try:
                payload = to_payload(
                    provider.tab_navigate(
                        provider_ctx, tab_id, call.url, options=call.navigation
                    )
                )
            except Exception as exc:
                if not self.is_stale_recoverable_error(exc):
                    raise
                self.clear_session_state(provider.provider_id, provider_ctx)
                recovered = self._recover_stale_navigation(
                    provider=provider,
                    provider_ctx=provider_ctx,
                    call=call,
                    original_error=exc,
                )
                recovered["resolution"] = {
                    **details,
                    "source_strategy": str(details.get("strategy", "")),
                    "strategy": "stale_recover_bootstrap",
                }
                return recovered
            payload["resolution"] = details
            if matched_tab is not None and "tab" not in payload:
                payload["tab"] = matched_tab.model_dump(exclude_none=True)
            return payload

        bootstrap_call = call
        if self.as_bool(details.get("stale_context_hint"), False):
            self.clear_session_state(provider.provider_id, provider_ctx)
            bootstrap_call = call.model_copy(
                update={"instance_id": None, "tab_id": None}
            )

        payload = self._bootstrap_navigate(
            provider=provider, provider_ctx=provider_ctx, call=bootstrap_call
        )
        payload["resolution"] = {
            **details,
            "source_strategy": str(details.get("strategy", "")),
            "strategy": "bootstrap_new_tab",
        }
        return payload

    def _handle_tab_snapshot(
        self,
        provider: BrowserProvider,
        provider_ctx: BrowserProviderContext,
        call: BrowserCallArgs,
    ) -> dict[str, Any]:
        tab_id = self._resolve_required_tab_id(
            provider=provider,
            provider_ctx=provider_ctx,
            call=call,
            error_message="tab_id is required for tab.snapshot",
        )
        return to_payload(
            provider.tab_snapshot(provider_ctx, tab_id, options=call.snapshot)
        )

    def _handle_tab_text(
        self,
        provider: BrowserProvider,
        provider_ctx: BrowserProviderContext,
        call: BrowserCallArgs,
    ) -> dict[str, Any]:
        tab_id = self._resolve_required_tab_id(
            provider=provider,
            provider_ctx=provider_ctx,
            call=call,
            error_message="tab_id is required for tab.text",
        )
        return to_payload(provider.tab_text(provider_ctx, tab_id, options=call.text))

    def _handle_tab_screenshot(
        self,
        provider: BrowserProvider,
        provider_ctx: BrowserProviderContext,
        call: BrowserCallArgs,
    ) -> dict[str, Any]:
        tab_id = self._resolve_required_tab_id(
            provider=provider,
            provider_ctx=provider_ctx,
            call=call,
            error_message="tab_id is required for tab.screenshot",
        )
        return to_payload(
            provider.tab_screenshot(provider_ctx, tab_id, options=call.output)
        )

    def _handle_tab_pdf(
        self,
        provider: BrowserProvider,
        provider_ctx: BrowserProviderContext,
        call: BrowserCallArgs,
    ) -> dict[str, Any]:
        tab_id = self._resolve_required_tab_id(
            provider=provider,
            provider_ctx=provider_ctx,
            call=call,
            error_message="tab_id is required for tab.pdf",
        )
        return to_payload(provider.tab_pdf(provider_ctx, tab_id, options=call.output))

    def _handle_tab_action(
        self,
        provider: BrowserProvider,
        provider_ctx: BrowserProviderContext,
        call: BrowserCallArgs,
    ) -> dict[str, Any]:
        tab_id = self._resolve_required_tab_id(
            provider=provider,
            provider_ctx=provider_ctx,
            call=call,
            error_message="tab_id and action are required for tab.action",
        )
        if call.action is None:
            raise self._error(
                "INVALID_ARGUMENT", "tab_id and action are required for tab.action"
            )
        return to_payload(provider.tab_action(provider_ctx, tab_id, call.action))

    def _handle_tab_actions(
        self,
        provider: BrowserProvider,
        provider_ctx: BrowserProviderContext,
        call: BrowserCallArgs,
    ) -> dict[str, Any]:
        tab_id = self._resolve_required_tab_id(
            provider=provider,
            provider_ctx=provider_ctx,
            call=call,
            error_message="tab_id is required for tab.actions",
        )
        return to_payload(provider.tab_actions(provider_ctx, tab_id, call.actions))

    def _handle_tab_lock(
        self,
        provider: BrowserProvider,
        provider_ctx: BrowserProviderContext,
        call: BrowserCallArgs,
    ) -> dict[str, Any]:
        tab_id = self._resolve_required_tab_id(
            provider=provider,
            provider_ctx=provider_ctx,
            call=call,
            error_message="tab_id is required for tab.lock",
        )
        return to_payload(
            provider.tab_lock(provider_ctx, tab_id, owner=call.owner, ttl_s=call.ttl_s)
        )

    def _handle_tab_unlock(
        self,
        provider: BrowserProvider,
        provider_ctx: BrowserProviderContext,
        call: BrowserCallArgs,
    ) -> dict[str, Any]:
        tab_id = self._resolve_required_tab_id(
            provider=provider,
            provider_ctx=provider_ctx,
            call=call,
            error_message="tab_id is required for tab.unlock",
        )
        return to_payload(provider.tab_unlock(provider_ctx, tab_id, owner=call.owner))

    def _bootstrap_navigate(
        self,
        *,
        provider: BrowserProvider,
        provider_ctx: BrowserProviderContext,
        call: BrowserCallArgs,
    ) -> dict[str, Any]:
        instance_id = str(call.instance_id or "").strip()
        payload: dict[str, Any] = {}
        if not instance_id:
            spec = self.instance_spec(call, provider_ctx)
            started = to_payload(provider.instance_start(provider_ctx, spec))
            payload.update(started)
            instance_id = self.extract_instance_id(started)
        if not instance_id:
            raise self._error(
                "INVALID_ARGUMENT",
                "unable to resolve or create browser instance for tab.navigate",
            )

        try:
            opened = to_payload(
                provider.tab_new(provider_ctx, instance_id, url=call.url)
            )
        except Exception as exc:
            if not self.is_stale_recoverable_error(exc):
                raise
            spec = self.instance_spec(call, provider_ctx)
            started = to_payload(provider.instance_start(provider_ctx, spec))
            payload.update(started)
            instance_id = self.extract_instance_id(started)
            if not instance_id:
                raise self._error(
                    "INVALID_ARGUMENT",
                    "unable to recover browser instance for tab.navigate",
                ) from exc
            opened = to_payload(
                provider.tab_new(provider_ctx, instance_id, url=call.url)
            )

        if not payload:
            return opened
        payload.update(opened)
        payload.setdefault("instance", {"id": instance_id})
        payload.setdefault("instance_id", instance_id)
        if "tab_id" not in payload:
            tab_id = self.extract_tab_id(payload)
            if tab_id:
                payload["tab_id"] = tab_id
        return payload

    def _recover_stale_navigation(
        self,
        *,
        provider: BrowserProvider,
        provider_ctx: BrowserProviderContext,
        call: BrowserCallArgs,
        original_error: Exception,
    ) -> dict[str, Any]:
        try:
            recovered_call = call.model_copy(
                update={"instance_id": None, "tab_id": None}
            )
            payload = self._bootstrap_navigate(
                provider=provider, provider_ctx=provider_ctx, call=recovered_call
            )
            payload["recovered_from"] = {
                "error": f"{type(original_error).__name__}: {original_error}"
            }
            return payload
        except Exception as recovery_error:
            raise self._error(
                "EXEC_ERROR",
                "failed to recover stale browser context during tab.navigate",
                {
                    "original_error": f"{type(original_error).__name__}: {original_error}",
                    "recovery_error": f"{type(recovery_error).__name__}: {recovery_error}",
                },
            ) from recovery_error
