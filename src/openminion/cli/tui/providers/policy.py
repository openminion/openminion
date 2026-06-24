from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from openminion.cli.parser.contracts import CLI_INTERFACE_VERSION


class RuntimePolicyProvider:
    contract_version: str = CLI_INTERFACE_VERSION

    def __init__(self, policy_ctl: Any | None, *, recent_limit: int = 20) -> None:
        self._policy_ctl = policy_ctl
        self._recent_limit = max(1, int(recent_limit))

    def list_pending_decisions(self) -> list[dict[str, Any]]:
        pending: list[dict[str, Any]] = []
        for entry in self._load_decisions(limit=max(50, self._recent_limit * 2)):
            decision = (
                str(entry.get("decision") or entry.get("outcome") or "").strip().upper()
            )
            if decision not in {"REQUIRE_CONFIRM", "PENDING"}:
                continue
            risk_spec = entry.get("risk_spec_json")
            risk = ""
            if isinstance(risk_spec, dict):
                risk = str(risk_spec.get("risk_class") or "").upper()
            tool = self._tool_name(entry)
            pending.append(
                {
                    "id": str(entry.get("decision_id") or ""),
                    "tool": tool,
                    "reason": str(
                        entry.get("reason_code") or entry.get("reason") or ""
                    ),
                    "risk": risk,
                    "outcome": "pending",
                    "ts": self._time_hhmm(str(entry.get("created_at") or "")),
                }
            )
        return pending[: self._recent_limit]

    def list_active_grants(self) -> list[dict[str, Any]]:
        if self._policy_ctl is None:
            return []
        list_grants = getattr(self._policy_ctl, "list_grants", None)
        if not callable(list_grants):
            return []
        try:
            grants = list_grants(active_only=True)
        except Exception:
            return []
        if not isinstance(grants, list):
            return []

        output: list[dict[str, Any]] = []
        for grant in grants:
            grant_id = str(self._value(grant, "grant_id") or "").strip()
            if not grant_id:
                continue
            max_uses_raw = self._value(grant, "max_uses")
            uses_count_raw = self._value(grant, "uses_count", 0)
            max_uses = (
                int(max_uses_raw)
                if isinstance(max_uses_raw, int) and max_uses_raw >= 0
                else 0
            )
            uses_count = (
                int(uses_count_raw)
                if isinstance(uses_count_raw, int) and uses_count_raw >= 0
                else 0
            )
            uses_left = max(0, max_uses - uses_count) if max_uses > 0 else 0
            output.append(
                {
                    "id": grant_id,
                    "scope": self._grant_scope(grant),
                    "ttl": self._ttl_text(str(self._value(grant, "expires_at") or "")),
                    "max_uses": max_uses,
                    "uses_left": uses_left,
                }
            )
        return output

    def list_recent_decisions(self, limit: int = 20) -> list[dict[str, Any]]:
        safe_limit = max(1, int(limit))
        decisions = self._load_decisions(limit=safe_limit)
        output: list[dict[str, Any]] = []
        for entry in decisions:
            decision = (
                str(entry.get("decision") or entry.get("outcome") or "").strip().upper()
            )
            if not decision:
                continue
            outcome = {
                "ALLOW": "allow",
                "DENY": "deny",
                "REQUIRE_CONFIRM": "pending",
                "PENDING": "pending",
            }.get(decision, decision.lower())
            output.append(
                {
                    "id": str(entry.get("decision_id") or ""),
                    "tool": self._tool_name(entry),
                    "outcome": outcome,
                    "ts": self._time_hhmm(str(entry.get("created_at") or "")),
                }
            )
        return output[:safe_limit]

    def revoke_grant(self, grant_id: str) -> bool:
        if self._policy_ctl is None:
            return False
        revoke_grant = getattr(self._policy_ctl, "revoke_grant", None)
        if not callable(revoke_grant):
            return False
        normalized_grant_id = str(grant_id or "").strip()
        if not normalized_grant_id:
            return False
        try:
            return bool(revoke_grant(normalized_grant_id))
        except Exception:
            return False

    def _load_decisions(self, *, limit: int) -> list[dict[str, Any]]:
        if self._policy_ctl is None:
            return []
        list_decisions = getattr(self._policy_ctl, "list_decisions", None)
        if not callable(list_decisions):
            return []
        try:
            rows = list_decisions(limit=max(1, int(limit)))
        except Exception:
            return []
        if not isinstance(rows, list):
            return []
        return [row for row in rows if isinstance(row, dict)]

    @staticmethod
    def _tool_name(entry: dict[str, Any]) -> str:
        tool = str(entry.get("tool") or "").strip()
        method = str(entry.get("method") or "").strip()
        if tool and method and method != "*":
            return f"{tool}.{method}"
        return tool or "unknown"

    @staticmethod
    def _value(obj: Any, key: str, default: Any = None) -> Any:
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    @staticmethod
    def _grant_scope(grant: Any) -> str:
        tool = str(RuntimePolicyProvider._value(grant, "tool") or "*").strip() or "*"
        method = (
            str(RuntimePolicyProvider._value(grant, "method") or "*").strip() or "*"
        )
        if method == "*":
            return tool
        return f"{tool}.{method}"

    @staticmethod
    def _time_hhmm(raw_iso: str) -> str:
        text = str(raw_iso or "").strip()
        if len(text) >= 16:
            return text[11:16]
        return ""

    @staticmethod
    def _ttl_text(expires_at: str) -> str:
        value = str(expires_at or "").strip()
        if not value:
            return "forever"
        try:
            expires = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            delta = int((expires - datetime.now(timezone.utc)).total_seconds())
            if delta <= 0:
                return "expired"
            minutes = max(1, delta // 60)
            if minutes < 60:
                return f"expires in {minutes}m"
            hours = max(1, minutes // 60)
            return f"expires in {hours}h"
        except ValueError:
            return value
