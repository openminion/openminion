from typing import Any, Mapping

from openminion.base.time import utc_now_iso as _utc_now


_ACTIONABLE_ROLES = {
    "button",
    "link",
    "textbox",
    "combobox",
    "searchbox",
    "checkbox",
    "radio",
    "menuitem",
    "tab",
    "option",
    "switch",
    "spinbutton",
    "input",
    "select",
    "textarea",
}


class SnapshotAdapter:
    def build(
        self,
        *,
        page: Any,
        mode: str,
        max_nodes: int,
        max_text_chars: int,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        normalized_mode = str(mode or "a11y").strip().lower()
        if normalized_mode == "a11y":
            try:
                return self._from_a11y(
                    page=page, max_nodes=max_nodes, max_text_chars=max_text_chars
                )
            except Exception:
                return self._from_dom(
                    page=page, max_nodes=max_nodes, max_text_chars=max_text_chars
                )
        if normalized_mode == "dom":
            return self._from_dom(
                page=page, max_nodes=max_nodes, max_text_chars=max_text_chars
            )
        return self._from_min(
            page=page, max_nodes=max_nodes, max_text_chars=max_text_chars
        )

    def _from_a11y(
        self, *, page: Any, max_nodes: int, max_text_chars: int
    ) -> tuple[dict[str, Any], dict[str, str]]:
        root = page.accessibility.snapshot(interesting_only=True)
        if not isinstance(root, Mapping):
            return self._from_min(
                page=page, max_nodes=max_nodes, max_text_chars=max_text_chars
            )

        nodes: list[dict[str, Any]] = []
        action_candidates: list[str] = []
        hints: dict[str, str] = {}
        text_chars = 0
        truncated = False

        def walk(node: Any) -> None:
            nonlocal text_chars, truncated
            if not isinstance(node, Mapping):
                return
            if len(nodes) >= max_nodes or text_chars >= max_text_chars:
                truncated = True
                return

            role = str(node.get("role") or "").strip().lower()
            name = str(node.get("name") or "").strip()
            value = node.get("value")
            value_text = (
                str(value).strip() if isinstance(value, (str, int, float, bool)) else ""
            )
            visible = not bool(node.get("hidden", False))
            selector_hint = _selector_hint(role=role, name=name)

            node_id = f"n{len(nodes) + 1}"
            row: dict[str, Any] = {
                "id": node_id,
                "role": role,
                "name": name,
                "visible": visible,
            }
            if value_text:
                row["value"] = value_text
            if selector_hint:
                row["selector_hint"] = selector_hint
                hints[node_id] = selector_hint
            nodes.append(row)

            if visible and role in _ACTIONABLE_ROLES:
                action_candidates.append(node_id)

            text_chars += len(name) + len(value_text)
            for child in (
                node.get("children", [])
                if isinstance(node.get("children"), list)
                else []
            ):
                walk(child)

        walk(root)
        snapshot = {
            "nodes": nodes,
            "action_candidates": action_candidates,
            "meta": self._meta(page=page, mode="a11y", truncated=truncated),
        }
        return snapshot, hints

    def _from_dom(
        self, *, page: Any, max_nodes: int, max_text_chars: int
    ) -> tuple[dict[str, Any], dict[str, str]]:
        payload = page.evaluate(
            r"""
            () => {
              const list = [];
              const items = Array.from(document.querySelectorAll('a,button,input,textarea,select,[role]'));
              for (const el of items) {
                const style = window.getComputedStyle(el);
                const visible = !!(style && style.visibility !== 'hidden' && style.display !== 'none' && el.getClientRects().length);
                if (!visible) continue;
                const role = (el.getAttribute('role') || el.tagName || '').toLowerCase();
                const name = (el.getAttribute('aria-label') || el.innerText || el.value || '').trim();
                let hint = '';
                if (el.id) {
                  hint = `#${el.id}`;
                } else {
                  const cls = typeof el.className === 'string' ? el.className.trim().split(/\s+/)[0] : '';
                  hint = cls ? `${el.tagName.toLowerCase()}.${cls}` : el.tagName.toLowerCase();
                }
                list.push({ role, name, visible, hint });
              }
              const text = document.body && document.body.innerText ? document.body.innerText : '';
              return {
                nodes: list,
                visible_text: text,
                title: document.title || '',
                url: location.href || ''
              };
            }
            """
        )

        raw_nodes = (
            payload.get("nodes")
            if isinstance(payload, Mapping) and isinstance(payload.get("nodes"), list)
            else []
        )
        nodes: list[dict[str, Any]] = []
        action_candidates: list[str] = []
        hints: dict[str, str] = {}
        text_chars = 0
        truncated = False

        for raw in raw_nodes:
            if not isinstance(raw, Mapping):
                continue
            if len(nodes) >= max_nodes or text_chars >= max_text_chars:
                truncated = True
                break
            role = str(raw.get("role") or "").strip().lower()
            name = str(raw.get("name") or "").strip()
            visible = bool(raw.get("visible", True))
            hint = str(raw.get("hint") or "").strip()
            node_id = f"n{len(nodes) + 1}"
            row: dict[str, Any] = {
                "id": node_id,
                "role": role,
                "name": name,
                "visible": visible,
            }
            if hint:
                row["selector_hint"] = hint
                hints[node_id] = hint
            nodes.append(row)
            if visible and role in _ACTIONABLE_ROLES:
                action_candidates.append(node_id)
            text_chars += len(name)

        snapshot = {
            "nodes": nodes,
            "action_candidates": action_candidates,
            "meta": self._meta(page=page, mode="dom", truncated=truncated),
        }
        return snapshot, hints

    def _from_min(
        self, *, page: Any, max_nodes: int, max_text_chars: int
    ) -> tuple[dict[str, Any], dict[str, str]]:
        title = _safe_title(page)
        url = _safe_url(page)
        visible_text = ""
        try:
            visible_text = str(page.inner_text("body"))
        except Exception:
            visible_text = ""
        text = visible_text[:max_text_chars]
        nodes = [
            {
                "id": "n1",
                "role": "document",
                "name": title,
                "value": text,
                "visible": True,
                "selector_hint": "body",
            }
        ]
        if max_nodes <= 0:
            nodes = []
        snapshot = {
            "nodes": nodes,
            "action_candidates": [],
            "meta": {
                "mode": "min",
                "truncated": len(visible_text) > len(text),
                "url": url,
                "title": title,
                "timestamp": _utc_now(),
            },
        }
        hints = {"n1": "body"} if nodes else {}
        return snapshot, hints

    def _meta(self, *, page: Any, mode: str, truncated: bool) -> dict[str, Any]:
        return {
            "mode": mode,
            "truncated": bool(truncated),
            "url": _safe_url(page),
            "title": _safe_title(page),
            "timestamp": _utc_now(),
        }


def _selector_hint(*, role: str, name: str) -> str:
    if role and name:
        escaped = name.replace("'", "\\'")
        return f"[role='{role}'][name='{escaped}']"
    if role:
        return f"[role='{role}']"
    return ""


def _safe_title(page: Any) -> str:
    try:
        return str(page.title() or "")
    except Exception:
        return ""


def _safe_url(page: Any) -> str:
    try:
        return str(page.url or "")
    except Exception:
        return ""
