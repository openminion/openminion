import json
import logging

from openminion.base.redaction import redact_mapping
from openminion.modules.controlplane.channels.authenticity import (
    ChannelAuthenticityDecision,
    ChannelAuthenticityEvidence,
    ChannelAuthenticityPolicy,
    evaluate_inbound_authenticity,
)
from openminion.modules.policy import (
    DECISION_ALLOW,
    DECISION_REQUIRE_APPROVAL,
    SecurityPolicyAction,
    SecurityPolicyCheck,
    SecurityPolicyContext,
    SecurityPolicyDecision,
    SecurityPolicyEngine,
    default_internal_actor,
)
from openminion.modules.storage.runtime.session_store import SessionStore


class GatewaySecurity:
    def __init__(
        self,
        *,
        sessions: SessionStore,
        logger: logging.Logger,
        agent_id: str,
        security_policy: SecurityPolicyEngine | None,
        channel_authenticity_policy: ChannelAuthenticityPolicy | None,
    ) -> None:
        self._sessions = sessions
        self._logger = logger
        self._agent_id = agent_id
        self._security_policy = security_policy
        self._channel_authenticity_policy = channel_authenticity_policy

    def evaluate_policy(
        self,
        *,
        resource: str,
        verb: str,
        risk: str,
        channel: str,
        target: str,
        session_id: str,
        run_id: str,
        tool_name: str = "",
    ) -> SecurityPolicyDecision:
        if self._security_policy is None:
            return SecurityPolicyDecision(
                decision=DECISION_ALLOW,
                reason_code="policy_disabled",
                policy_version="disabled",
            )
        check = SecurityPolicyCheck(
            actor=default_internal_actor(self._agent_id),
            action=SecurityPolicyAction(
                resource=resource,
                verb=verb,
                risk=risk,
                tool_name=tool_name,
            ),
            context=SecurityPolicyContext(
                channel=channel,
                target=target,
                session_id=session_id,
                run_id=run_id,
            ),
        )
        return self._security_policy.evaluate(check)

    def evaluate_inbound_authenticity(
        self,
        *,
        channel: str,
        target: str,
        body: str,
        inbound_metadata: dict[str, str],
    ) -> ChannelAuthenticityDecision:
        policy = self._channel_authenticity_policy
        if policy is None:
            return ChannelAuthenticityDecision(
                allowed=True,
                verified=channel.strip().lower() == "console",
                reason_code="authenticity_policy_disabled",
                mode="off",
                details={"channel": channel},
            )
        return evaluate_inbound_authenticity(
            policy=policy,
            evidence=ChannelAuthenticityEvidence(
                channel=channel,
                target=target,
                body=body,
                metadata=inbound_metadata,
            ),
        )

    def enforce_inbound_authenticity(
        self,
        *,
        session_id: str,
        run_id: str,
        decision: ChannelAuthenticityDecision,
    ) -> None:
        if decision.allowed and not decision.warning:
            return
        payload: dict[str, object] = {
            "run_id": run_id,
            "decision": "allow" if decision.allowed else "deny",
            "reason_code": decision.reason_code,
            "authenticity_verified": str(decision.verified).lower(),
            "authenticity_mode": decision.mode,
            "details": decision.details,
        }
        event_type = "security_warning" if decision.allowed else "auth_denied"
        self._append_security_event(
            session_id=session_id,
            event_type=event_type,
            payload=payload,
        )
        if decision.allowed:
            return
        raise RuntimeError(
            "inbound authenticity denied "
            f"(reason={decision.reason_code}, mode={decision.mode})"
        )

    def enforce_policy(
        self,
        *,
        session_id: str,
        run_id: str,
        decision: SecurityPolicyDecision,
    ) -> None:
        if decision.decision == DECISION_ALLOW:
            return

        event_type = (
            "approval_required"
            if decision.decision == DECISION_REQUIRE_APPROVAL
            else "policy_denied"
        )
        try:
            self._append_security_event(
                session_id=session_id,
                event_type=event_type,
                payload={
                    "run_id": run_id,
                    "decision": decision.decision,
                    "reason_code": decision.reason_code,
                    "policy_version": decision.policy_version,
                    "required_approval_level": decision.required_approval_level,
                    "details": decision.details,
                },
            )
        except Exception:
            pass

        if decision.decision == DECISION_REQUIRE_APPROVAL:
            raise RuntimeError(
                "security policy requires approval "
                f"(reason={decision.reason_code}, policy_version={decision.policy_version})"
            )
        raise RuntimeError(
            "security policy denied action "
            f"(reason={decision.reason_code}, policy_version={decision.policy_version})"
        )

    def emit_agent_security_events(
        self,
        *,
        session_id: str,
        run_id: str,
        metadata: dict[str, str],
    ) -> None:
        raw_events = str(metadata.get("security_events", "")).strip()
        if not raw_events:
            return

        try:
            parsed = json.loads(raw_events)
        except json.JSONDecodeError:
            self._append_security_event(
                session_id=session_id,
                event_type="security_warning",
                payload={
                    "run_id": run_id,
                    "reason_code": "invalid_security_events_payload",
                },
            )
            return

        if not isinstance(parsed, list):
            self._append_security_event(
                session_id=session_id,
                event_type="security_warning",
                payload={
                    "run_id": run_id,
                    "reason_code": "invalid_security_events_type",
                },
            )
            return

        valid_event_types = {
            "policy_denied",
            "approval_required",
            "security_warning",
            "auth_denied",
        }
        for event in parsed:
            if not isinstance(event, dict):
                continue
            raw_event_kind = str(event.get("event_kind", "")).strip()
            event_kind = (
                raw_event_kind
                if raw_event_kind in valid_event_types
                else "security_warning"
            )
            payload: dict[str, object] = {
                "run_id": run_id,
                "reason_code": str(event.get("reason_code", "")).strip(),
                "policy_version": str(event.get("policy_version", "")).strip(),
                "decision": str(event.get("decision", "")).strip(),
                "tool_name": str(event.get("tool_name", "")).strip(),
                "call_id": str(event.get("call_id", "")).strip(),
                "source": str(event.get("source", "")).strip(),
                "signals": str(event.get("signals", "")).strip(),
            }
            self._append_security_event(
                session_id=session_id,
                event_type=event_kind,
                payload=payload,
            )

    def _append_security_event(
        self,
        *,
        session_id: str,
        event_type: str,
        payload: dict[str, object],
    ) -> None:
        safe_payload, redaction_count = redact_mapping(payload)
        self._sessions.append_event(
            session_id=session_id,
            event_type=event_type,
            payload=safe_payload,
        )
        if redaction_count <= 0:
            return
        self._sessions.append_event(
            session_id=session_id,
            event_type="secret_redacted",
            payload={
                "run_id": str(payload.get("run_id", "")).strip(),
                "reason_code": "redaction_applied",
                "source_event_type": event_type,
                "redacted_fields_count": str(redaction_count),
            },
        )
