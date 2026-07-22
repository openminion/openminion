"""Tools browser providers playwright snapshot text."""

from typing import Any
from collections.abc import Mapping

from openminion.tools.browser.models import SnapshotOptions, TextOptions

from .coercion import tab_metadata


def snapshot(
    provider: Any,
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
    del interactive, compact, depth, max_tokens
    tab = provider._tabs.get(tab_id)
    key = provider._lock_key(tab_id)
    with provider._locks.action_lock(key):
        snapshot_payload, hints = provider._snapshot_adapter.build(
            page=tab.page,
            mode=mode or provider.config.snapshot.mode,
            max_nodes=max(50, min(5000, int(max_nodes))),
            max_text_chars=max(1000, min(200000, int(max_text_chars))),
        )
    tab.last_snapshot_hints = hints
    return {
        "tab": tab_metadata(tab),
        "snapshot": snapshot_payload,
    }


def tab_snapshot(
    provider: Any,
    *,
    tab_id: str = "",
    options: SnapshotOptions | Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    snapshot_opts = snapshot_options(options)
    return snapshot(
        provider,
        tab_id=tab_id,
        mode=snapshot_opts.mode,
        max_nodes=snapshot_opts.max_nodes,
        max_text_chars=snapshot_opts.max_text_chars,
        interactive=snapshot_opts.interactive,
        compact=snapshot_opts.compact,
        depth=snapshot_opts.depth,
        max_tokens=snapshot_opts.max_tokens,
    )


def extract_text(provider: Any, page: Any, *, mode: str) -> str:
    normalized = str(mode or "visible_text").strip().lower()
    if normalized in {"readability", "visible_text", "visible"}:
        try:
            return str(page.inner_text("body", timeout=provider.config.timeouts.api_ms))
        except Exception:
            return str(
                page.evaluate(
                    "() => document.body && document.body.innerText ? document.body.innerText : ''"
                )
            )
    return str(page.inner_text("body", timeout=provider.config.timeouts.api_ms))


def text(provider: Any, *, tab_id: str, mode: str = "visible_text") -> dict[str, Any]:
    tab = provider._tabs.get(tab_id)
    key = provider._lock_key(tab_id)
    with provider._locks.action_lock(key):
        content = extract_text(provider, tab.page, mode=mode)
    max_chars = provider.config.snapshot.max_text_chars
    truncated = len(content) > max_chars
    payload = content[:max_chars]
    return {
        "text": {
            "mode": mode,
            "content": payload,
            "stats": {
                "chars": len(payload),
                "blocks": len([line for line in payload.splitlines() if line.strip()]),
                "truncated": truncated,
            },
        }
    }


def tab_text(
    provider: Any,
    *,
    tab_id: str = "",
    options: TextOptions | Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    text_opts = text_options(options)
    payload = text(provider, tab_id=tab_id, mode=text_opts.mode)
    text_payload = payload.get("text") if isinstance(payload, Mapping) else None
    if not isinstance(text_payload, Mapping):
        return {"text": {"content": "", "truncated": False, "chars": 0}}
    content = str(text_payload.get("content", ""))
    stats = (
        text_payload.get("stats")
        if isinstance(text_payload.get("stats"), Mapping)
        else {}
    )
    return {
        "text": {
            "content": content,
            "truncated": bool(stats.get("truncated", False)),
            "chars": int(stats.get("chars", len(content))),
        }
    }


def snapshot_options(
    options: SnapshotOptions | Mapping[str, Any] | None,
) -> SnapshotOptions:
    if isinstance(options, SnapshotOptions):
        return options
    if isinstance(options, Mapping):
        return SnapshotOptions.model_validate(dict(options))
    return SnapshotOptions()


def text_options(options: TextOptions | Mapping[str, Any] | None) -> TextOptions:
    if isinstance(options, TextOptions):
        return options
    if isinstance(options, Mapping):
        return TextOptions.model_validate(dict(options))
    return TextOptions()


__all__ = [
    "extract_text",
    "snapshot",
    "snapshot_options",
    "tab_snapshot",
    "tab_text",
    "text",
    "text_options",
]
