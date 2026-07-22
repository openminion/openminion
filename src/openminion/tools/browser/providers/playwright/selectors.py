from typing import Any
from collections.abc import Mapping


class SelectorAdapter:
    def resolve_locator(
        self,
        *,
        page: Any,
        selector: str | None,
        role: Mapping[str, Any] | None,
        node_id: str | None,
        snapshot_hints: Mapping[str, str] | None,
        require_target: bool = True,
    ) -> Any | None:
        selector_token = str(selector or "").strip()
        if selector_token:
            return page.locator(self._normalize_selector(selector_token))

        if role:
            role_name = str(role.get("role", "")).strip()
            if role_name:
                name = role.get("name")
                exact = bool(role.get("exact", False))
                kwargs: dict[str, Any] = {}
                if isinstance(name, str) and name:
                    kwargs["name"] = name
                if exact:
                    kwargs["exact"] = True
                return page.get_by_role(role_name, **kwargs)

        node_token = str(node_id or "").strip()
        if node_token and snapshot_hints:
            hint = str(snapshot_hints.get(node_token, "")).strip()
            if hint:
                return page.locator(self._normalize_selector(hint))

        if require_target:
            raise ValueError(
                "action target required: provide selector, role, or snapshot node_id"
            )
        return None

    def selector_from_action(
        self, action: Mapping[str, Any]
    ) -> tuple[str | None, Mapping[str, Any] | None, str | None]:
        selector = str(action.get("selector", "")).strip() or None
        role = action.get("role") if isinstance(action.get("role"), Mapping) else None
        node_id = (
            str(action.get("node_id", action.get("snapshot_node_id", ""))).strip()
            or None
        )
        return selector, role, node_id

    @staticmethod
    def _normalize_selector(selector: str | None) -> str | None:
        if selector is None:
            return None
        token = str(selector).strip()
        if not token:
            return None
        if token.startswith("//") or token.startswith("("):
            return f"xpath={token}"
        return token
