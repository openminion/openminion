from typing import Any, Mapping

from openminion.tools.browser.models import BrowserAction

from .coercion import safe_int, tab_metadata


def apply_action(
    provider: Any, *, tab: Any, action: Mapping[str, Any]
) -> dict[str, Any]:
    kind = str(action.get("kind") or action.get("type") or "").strip().lower()
    if not kind:
        raise ValueError("action.kind is required")

    selector, role, node_id = provider._selector_adapter.selector_from_action(action)
    require_target = kind not in {"press", "wait_for"}
    locator = provider._selector_adapter.resolve_locator(
        page=tab.page,
        selector=selector,
        role=role,
        node_id=node_id,
        snapshot_hints=tab.last_snapshot_hints,
        require_target=require_target,
    )

    if kind == "click":
        locator.click(timeout=provider.config.timeouts.action_ms)
    elif kind == "type":
        text = str(action.get("text", action.get("value", "")))
        if not text:
            raise ValueError("type action requires non-empty text")
        locator.click(timeout=provider.config.timeouts.action_ms)
        locator.fill(text, timeout=provider.config.timeouts.action_ms)
    elif kind == "press":
        key = str(action.get("key", action.get("text", "Enter"))).strip() or "Enter"
        if locator is not None:
            locator.press(key, timeout=provider.config.timeouts.action_ms)
        else:
            tab.page.keyboard.press(key)
    elif kind == "select":
        value = action.get("value", action.get("option"))
        if value is None:
            raise ValueError("select action requires value")
        locator.select_option(value=value, timeout=provider.config.timeouts.action_ms)
    elif kind == "scroll":
        if locator is not None:
            locator.scroll_into_view_if_needed(
                timeout=provider.config.timeouts.action_ms
            )
        dy = safe_int(action.get("dy", action.get("amount", 600)), 600)
        dx = safe_int(action.get("dx", 0), 0)
        tab.page.mouse.wheel(dx, dy)
    elif kind == "wait_for":
        timeout_ms = safe_int(
            action.get("timeout_ms"), provider.config.timeouts.action_ms
        )
        state = str(action.get("state", "visible")).strip() or "visible"
        if locator is not None:
            locator.wait_for(timeout=timeout_ms, state=state)
        else:
            tab.page.wait_for_timeout(timeout_ms)
    elif kind == "hover":
        locator.hover(timeout=provider.config.timeouts.action_ms)
    else:
        raise ValueError(f"unsupported action kind: {kind}")

    return {
        "kind": kind,
        "target": {
            "selector": selector,
            "role": dict(role) if isinstance(role, Mapping) else None,
            "node_id": node_id,
        },
    }


def perform_action(
    provider: Any, *, tab_id: str, action: Mapping[str, Any]
) -> dict[str, Any]:
    tab = provider._tabs.get(tab_id)
    key = provider._lock_key(tab_id)
    with provider._locks.action_lock(key):
        row = apply_action(provider, tab=tab, action=action)
    return {
        "tab": tab_metadata(tab),
        "action": row,
    }


def action(provider: Any, *, tab_id: str, action: Mapping[str, Any]) -> dict[str, Any]:
    return perform_action(provider, tab_id=tab_id, action=action)


def tab_action(
    provider: Any,
    *,
    tab_id: str = "",
    action: BrowserAction | Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload = action_payload(action)
    return perform_action(provider, tab_id=tab_id, action=payload)


def perform_actions(
    provider: Any, *, tab_id: str, actions: list[Mapping[str, Any]]
) -> dict[str, Any]:
    tab = provider._tabs.get(tab_id)
    key = provider._lock_key(tab_id)
    steps: list[dict[str, Any]] = []
    failure_index: int | None = None
    with provider._locks.action_lock(key):
        for idx, item in enumerate(actions):
            try:
                result = apply_action(provider, tab=tab, action=item)
                steps.append({"ok": True, "result": result})
            except Exception as exc:
                failure_index = idx
                steps.append(
                    {
                        "ok": False,
                        "error": {
                            "message": f"{type(exc).__name__}: {exc}",
                        },
                    }
                )
                break

    return {
        "tab": tab_metadata(tab),
        "steps": steps,
        "failure_index": failure_index,
    }


def actions(
    provider: Any, *, tab_id: str, actions: list[Mapping[str, Any]]
) -> dict[str, Any]:
    return perform_actions(provider, tab_id=tab_id, actions=actions)


def tab_actions(
    provider: Any,
    *,
    tab_id: str = "",
    actions: list[BrowserAction] | list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    payload = [action_payload(item) for item in (actions or [])]
    return perform_actions(provider, tab_id=tab_id, actions=payload)


def action_payload(action: BrowserAction | Mapping[str, Any] | None) -> dict[str, Any]:
    if isinstance(action, BrowserAction):
        payload: dict[str, Any] = action.model_dump(exclude_none=True)
    elif isinstance(action, Mapping):
        payload = dict(action)
    else:
        return {}

    target = (
        payload.get("target") if isinstance(payload.get("target"), Mapping) else None
    )
    if target:
        if "selector" not in payload and target.get("selector") is not None:
            payload["selector"] = target.get("selector")
        if "role" not in payload and isinstance(target.get("role"), Mapping):
            payload["role"] = dict(target["role"])
        if "node_id" not in payload and target.get("ref") is not None:
            payload["node_id"] = target.get("ref")
    return payload


__all__ = [
    "action",
    "action_payload",
    "actions",
    "apply_action",
    "perform_action",
    "perform_actions",
    "tab_action",
    "tab_actions",
]
