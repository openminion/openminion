"""Brain-facing adapter over the policy controller."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from openminion.base.config import ActionPolicyConfig
from openminion.base.config.action_policy import (
    ACTION_POLICY_SESSION_OVERRIDE_KEY,
    map_action_policy_mode,
    normalize_action_policy_mode_override,
    overlay_action_policy_mode,
)

from ..runtime.action_policy import policy_config_from_action_policy
from ..constants import (
    POLICY_DECISION_ALLOW,
    POLICY_DECISION_DENY,
    POLICY_DECISION_REQUIRE_CONFIRM,
    POLICY_GRANT_EFFECT_ALLOW,
    POLICY_GRANT_EFFECT_DENY,
    POLICY_MODE_DISABLED,
    POLICY_RISK_DESTRUCTIVE,
    POLICY_RISK_READ,
    POLICY_RISK_SECURITY,
    POLICY_RISK_WRITE,
    POLICY_REVERSIBILITY_UNKNOWN,
    POLICY_SIDE_EFFECT_NONE,
    POLICY_SUBJECT_ID_LOCAL,
)
from ..interfaces import POLICY_INTERFACE_VERSION
from ..models import PolicyConfig, RiskSpec
from ..runtime.service import PolicyCtl

_LOGGER = logging.getLogger(__name__)
_CLARIFICATION_DECISIONS = {"REQUIRE_CLARIFICATION", "CLARIFY", "ASK"}
_ALLOW_DECISIONS = {"ALLOW", "LOG_ONLY_ALLOW", "DISABLED"}


class PolicyCheckResult:
    """Normalized result for brain consumers."""

    __slots__ = ("action", "code", "reason", "confirm_request", "modifications")

    def __init__(
        self,
        action: str,
        code: str,
        reason: str,
        confirm_request: dict[str, Any] | None = None,
        modifications: dict[str, Any] | None = None,
    ) -> None:
        self.action = action
        self.code = code
        self.reason = reason
        self.confirm_request = confirm_request
        self.modifications = modifications

    def is_allowed(self) -> bool:
        return self.action == POLICY_GRANT_EFFECT_ALLOW

    def requires_confirmation(self) -> bool:
        return self.action == POLICY_DECISION_REQUIRE_CONFIRM.lower()

    def is_denied(self) -> bool:
        return self.action == POLICY_GRANT_EFFECT_DENY

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "code": self.code,
            "reason": self.reason,
            "confirm_request": self.confirm_request,
            "modifications": self.modifications,
        }


class PolicyCtlBrainAdapter:
    """Adapter from brain command surfaces to ``PolicyCtl``."""

    contract_version = POLICY_INTERFACE_VERSION

    def __init__(
        self,
        policyctl: PolicyCtl,
        *,
        action_policy_config: ActionPolicyConfig | None = None,
    ) -> None:
        self._ctl = policyctl
        self._action_policy_config = action_policy_config

    @classmethod
    def with_sqlite(
        cls,
        database_path: str | Path,
        *,
        config: PolicyConfig | None = None,
        action_policy_config: ActionPolicyConfig | None = None,
    ) -> "PolicyCtlBrainAdapter":
        ctl = PolicyCtl.with_sqlite(database_path, config=config)
        return cls(ctl, action_policy_config=action_policy_config)

    def close(self) -> None:
        self._ctl.close()

    def evaluate(
        self,
        *,
        command: Any,
        working_state: Any,
        session_context: dict[str, Any],
    ) -> Any:
        """Compatibility surface for `modules.brain.interfaces.PolicyAPI`."""
        from openminion.modules.brain.schemas import PolicyDecision

        if getattr(command, "kind", None) != "tool":
            return PolicyDecision(
                outcome="ALLOW",
                explanation=f"Policy bypassed for non-tool command kind='{getattr(command, 'kind', 'unknown')}'.",
            )

        invocation, ctx = self._build_invocation_and_context(
            command=command,
            working_state=working_state,
            session_context=session_context,
        )
        if invocation is None:
            return self._missing_tool_name_decision()

        config_overrides = self._effective_policy_config(working_state=working_state)
        if self._is_policy_disabled(config_overrides=config_overrides):
            self._log_policy_bypass(command=command, working_state=working_state)
            return PolicyDecision(
                outcome="ALLOW",
                explanation="Policy bypassed by resolved action policy mode.",
            )

        try:
            decision = self._check_policy_decision(
                invocation=invocation,
                ctx=ctx,
                command=command,
                config_overrides=config_overrides,
            )
            return self._policy_decision_from_raw(decision)
        except Exception as exc:
            return PolicyDecision(
                outcome="DENY",
                explanation=f"Policy evaluation failed: {exc}",
            )

    @staticmethod
    def _missing_tool_name_decision() -> Any:
        from openminion.modules.brain.schemas import PolicyDecision

        return PolicyDecision(
            outcome="REQUIRE_CLARIFICATION",
            explanation="Tool command is missing tool_name.",
            require_clarification=True,
            clarification_question="Which tool should I run?",
        )

    @staticmethod
    def _risk_override_for_command(command: Any) -> RiskSpec | None:
        risk_level = getattr(command, "risk_level", None)
        if not risk_level:
            return None
        risk_map = {
            "low": POLICY_RISK_READ,
            "medium": POLICY_RISK_WRITE,
            "high": POLICY_RISK_DESTRUCTIVE,
            "critical": POLICY_RISK_SECURITY,
        }
        risk_class = risk_map.get(str(risk_level).lower())
        if not risk_class:
            return None
        return RiskSpec(
            risk_class=risk_class,  # type: ignore[arg-type]
            side_effects=POLICY_SIDE_EFFECT_NONE,
            reversibility=POLICY_REVERSIBILITY_UNKNOWN,
        )

    @staticmethod
    def _is_policy_disabled(*, config_overrides: PolicyConfig | None) -> bool:
        return (
            config_overrides is not None
            and config_overrides.mode == POLICY_MODE_DISABLED
        )

    def _log_policy_bypass(self, *, command: Any, working_state: Any) -> None:
        _LOGGER.info(
            "policy.adapter.bypass session_id=%s agent_id=%s tool=%s mode=%s",
            str(getattr(working_state, "session_id", "") or ""),
            str(getattr(working_state, "agent_id", "") or ""),
            str(getattr(command, "tool_name", "") or ""),
            str(getattr(working_state, ACTION_POLICY_SESSION_OVERRIDE_KEY, "") or "")
            or str(getattr(self._action_policy_config, "mode", "") or ""),
        )

    def _check_policy_decision(
        self,
        *,
        invocation: dict[str, Any],
        ctx: dict[str, Any],
        command: Any,
        config_overrides: PolicyConfig | None,
    ) -> Any:
        check_kwargs: dict[str, Any] = {
            "invocation": invocation,
            "ctx": ctx,
            "risk_override": self._risk_override_for_command(command),
        }
        if config_overrides is not None:
            check_kwargs["config_overrides"] = config_overrides
        return self._ctl.check(**check_kwargs)

    def _policy_decision_from_raw(self, decision: Any) -> Any:
        from openminion.modules.brain.schemas import PolicyDecision

        decision_name = str(getattr(decision, "decision", "")).strip().upper()
        reason_code = str(getattr(decision, "reason_code", "")).strip().upper()
        details = self._extract_details(getattr(decision, "details", None))
        require_clarification = self._requires_clarification(
            decision_name=decision_name, reason_code=reason_code, details=details
        )
        clarification_question = self._first_non_empty(
            details.get("clarification_question"),
            details.get("question"),
            getattr(decision, "clarification_question", None),
        )
        outcome = self._policy_outcome_for_decision(decision_name)
        if require_clarification:
            outcome = "REQUIRE_CLARIFICATION"
            clarification_question = (
                clarification_question
                or str(getattr(decision, "reason", "") or "").strip()
            )
        return PolicyDecision(
            outcome=outcome,  # type: ignore[arg-type]
            explanation=str(getattr(decision, "reason", "") or ""),
            require_clarification=require_clarification,
            clarification_question=clarification_question or None,
        )

    @staticmethod
    def _requires_clarification(
        *, decision_name: str, reason_code: str, details: dict[str, Any]
    ) -> bool:
        return (
            bool(details.get("require_clarification"))
            or decision_name in _CLARIFICATION_DECISIONS
            or reason_code
            in {
                "REQUIRE_CLARIFICATION",
                "MISSING_FIELD",
                "MISSING_REQUIRED_FIELD",
                "AMBIGUOUS_INPUT",
            }
        )

    @staticmethod
    def _policy_outcome_for_decision(decision_name: str) -> str:
        return {
            POLICY_DECISION_ALLOW: "ALLOW",
            POLICY_DECISION_DENY: "DENY",
            POLICY_DECISION_REQUIRE_CONFIRM: "REQUIRE_CONFIRMATION",
            "REQUIRE_CLARIFICATION": "REQUIRE_CLARIFICATION",
            "CLARIFY": "REQUIRE_CLARIFICATION",
            "ASK": "REQUIRE_CLARIFICATION",
        }.get(decision_name, "DENY")

    @staticmethod
    def _action_for_decision(decision_name: str) -> str:
        decision_name = str(decision_name).strip().upper()
        if decision_name in _ALLOW_DECISIONS:
            return POLICY_GRANT_EFFECT_ALLOW
        if decision_name == POLICY_DECISION_DENY:
            return POLICY_GRANT_EFFECT_DENY
        return decision_name.lower()

    def grant_once_from_confirmation(
        self,
        *,
        command: Any,
        working_state: Any,
        session_context: dict[str, Any],
    ) -> str:
        """Create a one-time allow grant for a confirmed pending command."""
        invocation, ctx = self._build_invocation_and_context(
            command=command,
            working_state=working_state,
            session_context=session_context,
        )
        if invocation is None:
            raise ValueError("Tool command is missing tool_name.")
        return self._ctl.create_grant_from_confirmation(
            invocation=invocation,
            ctx=ctx,
            action="allow_once",
            max_uses=1,
        )

    def parse_confirmation_response(self, text: str) -> str:
        action_policy_config = self._action_policy_config
        if action_policy_config is not None:
            from ..runtime.service import parse_confirmation_response

            return parse_confirmation_response(
                text,
                affirmative_tokens=action_policy_config.affirmative_tokens,
                negative_tokens=action_policy_config.negative_tokens,
            )
        return self._ctl.parse_confirmation_response(text)

    def check_command(
        self,
        command: dict[str, Any],
        ctx: dict[str, Any],
        *,
        risk_override: RiskSpec | dict[str, Any] | None = None,
    ) -> PolicyCheckResult:
        """Check a brain command dict against the active policy."""
        invocation = self._normalise_command(command)
        norm_ctx = self._normalise_ctx(ctx)

        raw = self._ctl.check(
            invocation,
            norm_ctx,
            risk_override=risk_override
            if isinstance(risk_override, RiskSpec)
            else (
                RiskSpec(**risk_override) if isinstance(risk_override, dict) else None
            ),
        )

        return PolicyCheckResult(
            action=self._action_for_decision(raw.decision),
            code=raw.reason_code,
            reason=raw.reason or "",
            confirm_request=raw.confirm_request,
            modifications=getattr(raw, "modifications", None),
        )

    @staticmethod
    def _normalise_command(command: dict[str, Any]) -> dict[str, Any]:
        """Translate a brain command dict to a PolicyCtl invocation dict."""
        kind = str(command.get("kind", "tool")).lower()
        if kind == "a2a":
            tool = str(command.get("provider", command.get("tool", "a2a")))
            method = str(command.get("action", command.get("method", "invoke")))
        else:
            tool = str(command.get("tool", command.get("kind", "unknown")))
            method = str(command.get("method", command.get("action", "run")))

        raw_args = command.get("args") or command.get("arguments") or {}
        args = dict(raw_args) if isinstance(raw_args, dict) else {}
        args.setdefault("_command_id", command.get("command_id"))
        args.setdefault("_risk_level", command.get("risk_level", "low"))
        args.setdefault("_title", command.get("title", ""))

        return {"tool": tool, "method": method, "args": args}

    @staticmethod
    def _normalise_ctx(ctx: dict[str, Any]) -> dict[str, Any]:
        return {
            "session_id": ctx.get("session_id", ""),
            "agent_id": ctx.get("agent_id", ctx.get("subject_id", "")),
            "trace_id": ctx.get("trace_id", ""),
            "mode_name": ctx.get("mode_name", ""),
        }

    @staticmethod
    def _build_context(
        working_state: Any, session_context: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "session_id": getattr(working_state, "session_id", ""),
            "agent_id": getattr(working_state, "agent_id", ""),
            "trace_id": getattr(working_state, "trace_id", ""),
            "subject_id": session_context.get("subject_id", POLICY_SUBJECT_ID_LOCAL),
            "mode_name": session_context.get("mode_name", ""),
        }

    @staticmethod
    def _extract_details(raw: Any) -> dict[str, Any]:
        if isinstance(raw, dict):
            return dict(raw)
        if isinstance(raw, str):
            text = raw.strip()
            if not text:
                return {}
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                return {}
            if isinstance(parsed, dict):
                return dict(parsed)
        return {}

    @staticmethod
    def _first_non_empty(*values: Any) -> str:
        for value in values:
            text = str(value or "").strip()
            if text:
                return text
        return ""

    def _effective_policy_config(self, *, working_state: Any) -> PolicyConfig | None:
        session_mode_override = normalize_action_policy_mode_override(
            getattr(working_state, ACTION_POLICY_SESSION_OVERRIDE_KEY, None)
        )
        action_policy_config = self._action_policy_config
        if action_policy_config is not None:
            effective = overlay_action_policy_mode(
                action_policy_config,
                session_mode_override,
            )
            return policy_config_from_action_policy(effective)
        if session_mode_override is None:
            return None
        base_config = getattr(self._ctl, "_config", None)
        if not isinstance(base_config, PolicyConfig):
            return PolicyConfig(mode=map_action_policy_mode(session_mode_override))
        return PolicyConfig(
            mode=map_action_policy_mode(session_mode_override),  # type: ignore[arg-type]
            default_action=base_config.default_action,
            default_duration=base_config.default_duration,
            sandbox_path_prefixes=list(base_config.sandbox_path_prefixes),
            allow_read_only_without_prompt=base_config.allow_read_only_without_prompt,
            affirmative_tokens=list(base_config.affirmative_tokens),
            negative_tokens=list(base_config.negative_tokens),
            subject_id_default=base_config.subject_id_default,
            decision_log_enabled=base_config.decision_log_enabled,
        )

    @staticmethod
    def _build_invocation_and_context(
        *,
        command: Any,
        working_state: Any,
        session_context: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        ctx = PolicyCtlBrainAdapter._build_context(working_state, session_context)
        tool_name = str(getattr(command, "tool_name", "") or "").strip()
        if not tool_name:
            return None, ctx

        tool, method = (
            tool_name.rsplit(".", 1) if "." in tool_name else (tool_name, "default")
        )

        invocation = {
            "tool": tool,
            "method": method,
            "args": getattr(command, "args", {}),
            "invocation_id": getattr(command, "idempotency_key", ""),
        }
        return invocation, ctx
