from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Literal, Optional
from urllib.parse import urlparse

from ..models import (
    ContextSummary,
    InvocationSummary,
    PolicyConfig,
    PolicyDecision,
    PolicyGrant,
    PolicyGrantInput,
    RiskClass,
    RiskSpec,
    sanitize_args,
    stable_invocation_hash,
    utc_now_iso,
)
from ..interfaces import POLICY_INTERFACE_VERSION
from ..constants import (
    POLICY_CONFIRM_RESPONSE_AFFIRM,
    POLICY_CONFIRM_RESPONSE_DENY,
    POLICY_CONFIRM_RESPONSE_UNCLEAR,
    POLICY_DECISION_ALLOW,
    POLICY_DECISION_DENY,
    POLICY_DECISION_REQUIRE_CONFIRM,
    POLICY_DURATION_FOREVER,
    POLICY_DURATION_ONCE,
    POLICY_DURATION_SESSION,
    POLICY_DURATION_UNTIL,
    POLICY_HIGH_CONFIRM_RISKS,
    POLICY_GRANT_EFFECT_ALLOW,
    POLICY_GRANT_EFFECT_DENY,
    POLICY_GRANT_EFFECTS,
    POLICY_MODE_DISABLED,
    POLICY_MODE_ENFORCE,
    POLICY_MODE_ENFORCE_SAFE,
    POLICY_MODE_LOG_ONLY,
    POLICY_MODES,
    POLICY_REVERSIBILITY_IRREVERSIBLE,
    POLICY_REVERSIBILITY_PARTIALLY_REVERSIBLE,
    POLICY_REVERSIBILITY_REVERSIBLE,
    POLICY_REVERSIBILITY_UNKNOWN,
    POLICY_RISK_DESTRUCTIVE,
    POLICY_RISK_EXEC,
    POLICY_RISK_FINANCIAL,
    POLICY_RISK_READ,
    POLICY_RISK_SECURITY,
    POLICY_SIDE_EFFECT_CONFIRM_RISKS,
    POLICY_SIDE_EFFECT_EXTERNAL_ACCOUNT,
    POLICY_SIDE_EFFECT_LOCAL,
    POLICY_SIDE_EFFECT_REMOTE,
    POLICY_RISK_STATE_CHANGE,
    POLICY_RISK_WRITE,
    POLICY_SIDE_EFFECT_NONE,
)
from ..storage import PolicyStore
from ..storage.store import SQLitePolicyStore


_RISK_ORDER: Dict[RiskClass, int] = {
    POLICY_RISK_READ: 0,
    POLICY_RISK_WRITE: 1,
    POLICY_RISK_STATE_CHANGE: 2,
    POLICY_RISK_EXEC: 3,
    POLICY_RISK_SECURITY: 4,
    POLICY_RISK_FINANCIAL: 5,
    POLICY_RISK_DESTRUCTIVE: 6,
}


def _arg_path(args: Dict[str, Any]) -> Optional[str]:
    for key in ("path", "root"):
        value = args.get(key)
        if value is not None:
            text = str(value)
            if text:
                return text
    return None


def _arg_command(args: Dict[str, Any]) -> Optional[str]:
    argv = args.get("argv")
    if isinstance(argv, list):
        command = " ".join(str(item) for item in argv).strip()
        return command or None
    command = args.get("command")
    if command is None:
        return None
    text = str(command).strip()
    return text or None


def _arg_domain(args: Dict[str, Any]) -> Optional[str]:
    domain = _opt_str(args.get("domain"))
    if domain:
        return domain
    url = args.get("url")
    if isinstance(url, str):
        return _opt_str(urlparse(url).hostname)
    return None


def _normalize_confirmation_token(value: str) -> str:
    token = str(value or "").strip().lower().rstrip(".,!?")
    return " ".join(part for part in token.split() if part)


def parse_confirmation_response(
    text: str,
    *,
    affirmative_tokens: Iterable[str] | None = None,
    negative_tokens: Iterable[str] | None = None,
) -> Literal["affirm", "deny", "unclear"]:
    normalized = _normalize_confirmation_token(text)
    if not normalized:
        return POLICY_CONFIRM_RESPONSE_UNCLEAR

    affirmative = {
        _normalize_confirmation_token(token)
        for token in (
            affirmative_tokens
            if affirmative_tokens is not None
            else PolicyConfig().affirmative_tokens
        )
        if _normalize_confirmation_token(token)
    }
    negative = {
        _normalize_confirmation_token(token)
        for token in (
            negative_tokens
            if negative_tokens is not None
            else PolicyConfig().negative_tokens
        )
        if _normalize_confirmation_token(token)
    }

    # Confirmation is a safety boundary. Accept only exact configured choices;
    # mixed, conditional, or explanatory prose must stay unclear.
    if normalized in affirmative and normalized not in negative:
        return POLICY_CONFIRM_RESPONSE_AFFIRM
    if normalized in negative and normalized not in affirmative:
        return POLICY_CONFIRM_RESPONSE_DENY

    return POLICY_CONFIRM_RESPONSE_UNCLEAR


@dataclass(frozen=True)
class _GrantMatch:
    grant: PolicyGrant
    score: int


class PolicyCtl:
    contract_version = POLICY_INTERFACE_VERSION

    def __init__(
        self,
        *,
        store: PolicyStore,
        config: Optional[PolicyConfig] = None,
        risk_registry: Optional[Dict[str, RiskSpec | Dict[str, Any]]] = None,
    ) -> None:
        self._store = store
        self._config = config or PolicyConfig()
        self._risk_registry: Dict[str, RiskSpec] = {}
        for key, value in (risk_registry or {}).items():
            if isinstance(value, RiskSpec):
                self._risk_registry[key] = value
            elif isinstance(value, dict):
                self._risk_registry[key] = RiskSpec.from_dict(value)

    @staticmethod
    def with_sqlite(
        database_path: str | Path,
        *,
        config: Optional[PolicyConfig] = None,
        risk_registry: Optional[Dict[str, RiskSpec | Dict[str, Any]]] = None,
    ) -> "PolicyCtl":
        return PolicyCtl(
            store=SQLitePolicyStore(database_path),
            config=config,
            risk_registry=risk_registry,
        )

    def close(self) -> None:
        self._store.close()

    def mode(self) -> str:
        persisted = self._store.get_setting("mode")
        if persisted:
            return persisted
        return self._config.mode

    def set_mode(self, mode: str) -> None:
        normalized = str(mode or "").strip().lower()
        if normalized not in POLICY_MODES:
            raise ValueError(f"Invalid mode: {mode}")
        self._store.set_setting("mode", normalized)

    def register_risk(self, key: str, spec: RiskSpec | Dict[str, Any]) -> None:
        if isinstance(spec, RiskSpec):
            self._risk_registry[key] = spec
            return
        self._risk_registry[key] = RiskSpec.from_dict(spec)

    def check(
        self,
        invocation: Any,
        ctx: Any,
        *,
        risk_override: Optional[RiskSpec] = None,
        config_overrides: Optional[PolicyConfig] = None,
    ) -> PolicyDecision:
        inv = self._normalize_invocation(invocation)
        csum = self._normalize_context(ctx)
        risk = risk_override or self._resolve_risk(inv)
        effective_config = config_overrides or self._config
        mode = effective_config.mode if config_overrides is not None else self.mode()

        if mode == POLICY_MODE_DISABLED:
            return PolicyDecision(
                decision=POLICY_DECISION_ALLOW,
                reason_code="POLICY_DISABLED",
                reason="Policy mode is disabled",
                risk=risk,
            )

        consume_grants = mode in {POLICY_MODE_ENFORCE, POLICY_MODE_ENFORCE_SAFE}
        enforced = self._evaluate_enforced(
            inv,
            csum,
            risk,
            consume_grants=consume_grants,
            mode=mode,
            effective_config=effective_config,
        )
        self._log_decision(inv=inv, ctx=csum, decision=enforced)

        if mode == POLICY_MODE_LOG_ONLY:
            details = dict(enforced.details)
            details["would_decision"] = enforced.decision
            details["would_reason_code"] = enforced.reason_code
            return PolicyDecision(
                decision=POLICY_DECISION_ALLOW,
                reason_code="LOG_ONLY_ALLOW",
                reason="Policy log_only mode does not block actions",
                risk=risk,
                matched_grant_id=enforced.matched_grant_id,
                confirm_request=enforced.confirm_request,
                details=details,
            )
        return enforced

    def create_grant(self, grant: PolicyGrantInput) -> str:
        if grant.effect not in POLICY_GRANT_EFFECTS:
            raise ValueError("grant.effect must be allow|deny")
        if grant.duration_type == POLICY_DURATION_ONCE and not grant.invocation_hash:
            raise ValueError("once grants require invocation_hash")
        if grant.duration_type == POLICY_DURATION_UNTIL and not grant.expires_at:
            raise ValueError("until grants require expires_at")
        return self._store.create_grant(grant)

    def create_grant_from_confirmation(
        self,
        *,
        invocation: Any,
        ctx: Any,
        action: str,
        until_seconds: Optional[int] = None,
        scope_overrides: Optional[Dict[str, Any]] = None,
        max_uses: Optional[int] = None,
    ) -> str:
        inv = self._normalize_invocation(invocation)
        csum = self._normalize_context(ctx)
        target = self._default_target_scope(inv)
        if scope_overrides:
            target.update(scope_overrides)

        duration = POLICY_DURATION_FOREVER
        expires_at: Optional[str] = None
        session_id: Optional[str] = None
        invocation_hash: Optional[str] = None

        if action == "allow_once":
            duration = POLICY_DURATION_ONCE
            invocation_hash = inv.invocation_hash
        elif action == "allow_until":
            duration = POLICY_DURATION_UNTIL
            seconds = max(1, int(until_seconds or 600))
            expires_at = (
                datetime.now(timezone.utc) + timedelta(seconds=seconds)
            ).isoformat()
        elif action == "allow_session":
            duration = POLICY_DURATION_SESSION
            session_id = csum.session_id
        elif action == "allow_forever":
            duration = POLICY_DURATION_FOREVER
        else:
            raise ValueError(f"Unsupported confirmation action: {action}")

        return self.create_grant(
            PolicyGrantInput(
                effect=POLICY_GRANT_EFFECT_ALLOW,
                subject_id=csum.subject_id or self._config.subject_id_default,
                tool=inv.tool,
                method=inv.method,
                target_json=target,
                duration_type=duration,  # type: ignore[arg-type]
                expires_at=expires_at,
                session_id=session_id,
                invocation_hash=invocation_hash,
                max_uses=max_uses,
                created_trace_id=csum.trace_id,
                reason=f"created_from_confirmation:{action}",
            )
        )

    def revoke_grant(self, grant_id: str) -> bool:
        return self._store.revoke_grant(grant_id)

    def list_grants(
        self,
        *,
        subject_id: Optional[str] = None,
        effect: Optional[str] = None,
        tool: Optional[str] = None,
        method: Optional[str] = None,
        active_only: bool = False,
    ) -> list[PolicyGrant]:
        return self._store.list_grants(
            subject_id=subject_id,
            effect=effect,
            tool=tool,
            method=method,
            active_only=active_only,
        )

    def cleanup_expired(self) -> int:
        return self._store.cleanup_expired()

    def list_decisions(self, *, limit: int = 100) -> list[Dict[str, Any]]:
        return self._store.list_decisions(limit=limit)

    def parse_confirmation_response(
        self, text: str
    ) -> Literal["affirm", "deny", "unclear"]:
        return parse_confirmation_response(
            text,
            affirmative_tokens=self._config.affirmative_tokens,
            negative_tokens=self._config.negative_tokens,
        )

    def _evaluate_enforced(
        self,
        inv: InvocationSummary,
        csum: ContextSummary,
        risk: RiskSpec,
        *,
        consume_grants: bool,
        mode: str,
        effective_config: PolicyConfig,
    ) -> PolicyDecision:
        self._store.cleanup_expired()
        subject_id = csum.subject_id or effective_config.subject_id_default
        candidates = self._store.list_grants(subject_id=subject_id, active_only=True)
        matches = self._find_matching_grants(candidates, inv=inv, csum=csum, risk=risk)
        selected = self._select_match(matches)

        if selected is not None:
            grant = selected.grant
            if grant.effect == POLICY_GRANT_EFFECT_DENY:
                return PolicyDecision(
                    decision=POLICY_DECISION_DENY,
                    reason_code="EXPLICIT_DENY",
                    reason="Denied by explicit grant rule",
                    risk=risk,
                    matched_grant_id=grant.grant_id,
                    details={"grant_id": grant.grant_id},
                )

            if consume_grants:
                self._store.consume_grant_use(grant.grant_id)

            return PolicyDecision(
                decision=POLICY_DECISION_ALLOW,
                reason_code="EXPLICIT_ALLOW",
                reason="Allowed by explicit grant",
                risk=risk,
                matched_grant_id=grant.grant_id,
                details={"grant_id": grant.grant_id},
            )

        is_sensitive_target = self._matches_sensitive_target(inv, risk)
        if (
            risk.risk_class == POLICY_RISK_READ
            and effective_config.allow_read_only_without_prompt
            and not is_sensitive_target
        ):
            return PolicyDecision(
                decision=POLICY_DECISION_ALLOW,
                reason_code="READ_ONLY_ALLOW",
                reason="Read-only operation allowed by default",
                risk=risk,
            )

        confirm_reason = self._confirm_reason(
            csum=csum,
            risk=risk,
            mode=mode,
            effective_config=effective_config,
            is_sensitive_target=is_sensitive_target,
        )
        if confirm_reason is not None:
            reason_code, reason = confirm_reason
            return self._confirm_decision(
                inv=inv,
                csum=csum,
                risk=risk,
                reason_code=reason_code,
                reason=reason,
            )

        if risk.risk_class == POLICY_RISK_WRITE and self._is_write_under_sandbox(inv):
            return PolicyDecision(
                decision=POLICY_DECISION_ALLOW,
                reason_code="SANDBOX_WRITE_ALLOW",
                reason="Write scoped to sandbox path is allowed",
                risk=risk,
            )

        return PolicyDecision(
            decision=POLICY_DECISION_ALLOW,
            reason_code="NO_MATCHING_GRANT",
            reason="No matching grant; default allow",
            risk=risk,
        )

    def _confirm_reason(
        self,
        *,
        csum: ContextSummary,
        risk: RiskSpec,
        mode: str,
        effective_config: PolicyConfig,
        is_sensitive_target: bool,
    ) -> Optional[tuple[str, str]]:
        if is_sensitive_target:
            return ("SENSITIVE_TARGET", "Sensitive target requires confirmation")
        if str(csum.mode_name or "").strip().lower() == "plan" and risk.risk_class in {
            POLICY_RISK_WRITE,
            POLICY_RISK_STATE_CHANGE,
            POLICY_RISK_EXEC,
            POLICY_RISK_SECURITY,
            POLICY_RISK_FINANCIAL,
            POLICY_RISK_DESTRUCTIVE,
        }:
            return (
                "PLAN_MODE_CONFIRM",
                "Plan mode requires confirmation for non-read side effects",
            )
        if risk.risk_class in POLICY_HIGH_CONFIRM_RISKS:
            return ("HIGH_RISK", "High-risk action requires confirmation")
        if (
            risk.risk_class in POLICY_SIDE_EFFECT_CONFIRM_RISKS
            and risk.side_effects != POLICY_SIDE_EFFECT_NONE
        ):
            return (
                "SIDE_EFFECTS_CONFIRM",
                "Action with side effects requires confirmation",
            )
        if (
            risk.reversibility == POLICY_REVERSIBILITY_UNKNOWN
            and risk.risk_class != POLICY_RISK_READ
        ):
            return (
                "UNKNOWN_REVERSIBILITY",
                "Unknown reversibility requires confirmation",
            )
        if mode == POLICY_MODE_ENFORCE_SAFE and risk.risk_class != POLICY_RISK_READ:
            return (
                "ENFORCE_SAFE_CONFIRM",
                "enforce_safe mode requires confirmation for non-read actions",
            )
        if (
            effective_config.default_action == POLICY_DECISION_REQUIRE_CONFIRM.lower()
            and (
                risk.risk_class != POLICY_RISK_READ
                or not effective_config.allow_read_only_without_prompt
            )
        ):
            return (
                "DEFAULT_CONFIRM",
                "Default policy requires confirmation for non-read actions",
            )
        return None

    def _confirm_decision(
        self,
        *,
        inv: InvocationSummary,
        csum: ContextSummary,
        risk: RiskSpec,
        reason_code: str,
        reason: str,
    ) -> PolicyDecision:
        return PolicyDecision(
            decision=POLICY_DECISION_REQUIRE_CONFIRM,
            reason_code=reason_code,
            reason=reason,
            risk=risk,
            confirm_request=self._build_confirm_request(inv=inv, csum=csum, risk=risk),
        )

    def _build_confirm_request(
        self,
        *,
        inv: InvocationSummary,
        csum: ContextSummary,
        risk: RiskSpec,
    ) -> Dict[str, Any]:
        target_scope = self._default_target_scope(inv)
        scope_preview = {
            "allow_once": {
                "tool": inv.tool,
                "method": inv.method,
                "invocation_hash": inv.invocation_hash,
            },
            "allow_until": {
                "tool": inv.tool,
                "method": inv.method,
                "target": dict(target_scope),
            },
            "allow_session": {
                "tool": inv.tool,
                "method": inv.method,
                "target": dict(target_scope),
                "session_id": csum.session_id,
            },
            "allow_forever": {
                "tool": inv.tool,
                "method": inv.method,
                "target": dict(target_scope),
            },
        }
        return {
            "trace_id": csum.trace_id,
            "invocation_id": inv.invocation_id,
            "summary": {
                "tool": inv.tool,
                "method": inv.method,
                "args": sanitize_args(inv.args),
            },
            "risk": {
                "risk_class": risk.risk_class,
                "side_effects": risk.side_effects,
                "reversibility": risk.reversibility,
            },
            "suggested_choices": [
                {"action": "allow_once", "label": "Allow once"},
                {
                    "action": "allow_until",
                    "label": "Allow for 10 minutes",
                    "until_seconds": 600,
                },
                {"action": "allow_session", "label": "Allow for this session"},
                {"action": "allow_forever", "label": "Allow forever (scoped)"},
                {"action": "deny", "label": "Deny"},
            ],
            "scope_preview": scope_preview,
            "deny_option": {"action": "deny"},
        }

    def _default_target_scope(self, inv: InvocationSummary) -> Dict[str, Any]:
        args = inv.args
        target: Dict[str, Any] = {}
        path = _arg_path(args)
        if path:
            target["path_prefix"] = path
        if isinstance(args.get("argv"), list) and args["argv"]:
            first = str(args["argv"][0]).strip()
            if first:
                target["cmd_prefix"] = first
        if "host" in args:
            target["host"] = str(args["host"])
        domain = _arg_domain(args)
        if domain:
            target["domain"] = domain
        return target

    def _find_matching_grants(
        self,
        grants: Iterable[PolicyGrant],
        *,
        inv: InvocationSummary,
        csum: ContextSummary,
        risk: RiskSpec,
    ) -> list[_GrantMatch]:
        matched: list[_GrantMatch] = []
        facts = self._target_facts(inv)
        for grant in grants:
            if not self._grant_matches(
                grant, inv=inv, csum=csum, risk=risk, facts=facts
            ):
                continue
            matched.append(
                _GrantMatch(grant=grant, score=self._specificity_score(grant))
            )
        return matched

    def _grant_matches(
        self,
        grant: PolicyGrant,
        *,
        inv: InvocationSummary,
        csum: ContextSummary,
        risk: RiskSpec,
        facts: Dict[str, Any],
    ) -> bool:
        if grant.revoked_at is not None:
            return False
        if grant.expires_at is not None and grant.expires_at <= utc_now_iso():
            return False
        if grant.max_uses is not None and grant.uses_count >= grant.max_uses:
            return False
        if (
            grant.duration_type == POLICY_DURATION_SESSION
            and grant.session_id
            and grant.session_id != csum.session_id
        ):
            return False
        if (
            grant.duration_type == POLICY_DURATION_ONCE
            and grant.invocation_hash != inv.invocation_hash
        ):
            return False
        if grant.tool != "*" and grant.tool != inv.tool:
            return False
        if grant.method != "*" and grant.method != inv.method:
            return False
        if (
            grant.risk_floor
            and _RISK_ORDER[risk.risk_class] < _RISK_ORDER[grant.risk_floor]
        ):
            return False
        if not self._target_matches(grant.target_json, facts=facts, args=inv.args):
            return False
        return True

    def _target_matches(
        self, target: Dict[str, Any], *, facts: Dict[str, Any], args: Dict[str, Any]
    ) -> bool:
        if not target:
            return True

        def _values(key: str) -> list[str]:
            value = target.get(key)
            if value is None:
                return []
            if isinstance(value, list):
                return [str(item) for item in value if str(item).strip()]
            return [str(value)]

        for path_prefix in _values("path_prefix"):
            candidate = str(facts.get("path", ""))
            if not candidate.startswith(path_prefix):
                return False

        for cmd_prefix in _values("cmd_prefix"):
            cmd = str(facts.get("command", ""))
            if not cmd.startswith(cmd_prefix):
                return False

        for expr in _values("cmd_regex"):
            cmd = str(facts.get("command", ""))
            if not re.search(expr, cmd):
                return False

        for host in _values("host"):
            candidate = str(facts.get("host", ""))
            if not fnmatch.fnmatch(candidate, host):
                return False

        for domain in _values("domain"):
            candidate = str(facts.get("domain", ""))
            if not fnmatch.fnmatch(candidate, domain):
                return False

        for namespace in _values("namespace"):
            if str(facts.get("namespace", "")) != namespace:
                return False

        for cluster in _values("cluster"):
            if str(facts.get("cluster", "")) != cluster:
                return False

        for resource in _values("resource"):
            if str(facts.get("resource", "")) != resource:
                return False

        arg_equals = target.get("arg_equals")
        if isinstance(arg_equals, dict):
            for key, expected in arg_equals.items():
                if args.get(key) != expected:
                    return False

        arg_contains = target.get("arg_contains")
        if isinstance(arg_contains, dict):
            for key, expected in arg_contains.items():
                value = args.get(key)
                if isinstance(value, list):
                    text = " ".join(str(item) for item in value)
                else:
                    text = str(value)
                if str(expected) not in text:
                    return False

        return True

    def _specificity_score(self, grant: PolicyGrant) -> int:
        score = 0
        if grant.tool != "*":
            score += 8
        if grant.method != "*":
            score += 6
        if grant.target_json:
            score += 4 + len(grant.target_json.keys())
        if grant.risk_floor:
            score += 1
        return score

    def _select_match(self, matches: list[_GrantMatch]) -> Optional[_GrantMatch]:
        if not matches:
            return None
        matches.sort(key=lambda item: item.score, reverse=True)
        best_score = matches[0].score
        same = [item for item in matches if item.score == best_score]
        deny = [item for item in same if item.grant.effect == POLICY_GRANT_EFFECT_DENY]
        if deny:
            deny.sort(key=lambda item: item.grant.created_at, reverse=True)
            return deny[0]
        same.sort(key=lambda item: item.grant.created_at, reverse=True)
        return same[0]

    def _matches_sensitive_target(self, inv: InvocationSummary, risk: RiskSpec) -> bool:
        if not risk.sensitive_targets:
            return False
        facts = self._target_facts(inv)
        target_blob = " ".join(
            [
                str(facts.get("path", "")),
                str(facts.get("command", "")),
                str(facts.get("host", "")),
                str(facts.get("domain", "")),
            ]
        )
        for matcher in risk.sensitive_targets:
            if isinstance(matcher, str):
                if matcher and matcher in target_blob:
                    return True
                continue
            if not isinstance(matcher, dict):
                continue
            if self._target_matches(matcher, facts=facts, args=inv.args):
                return True
        return False

    def _is_write_under_sandbox(self, inv: InvocationSummary) -> bool:
        raw = _arg_path(inv.args)
        if not raw:
            return False
        raw_path = Path(raw).expanduser()
        for prefix in self._config.sandbox_path_prefixes:
            pref = Path(prefix).expanduser()
            if str(raw_path).startswith(str(pref)):
                return True
        return False

    def _target_facts(self, inv: InvocationSummary) -> Dict[str, Any]:
        args = inv.args
        facts: Dict[str, Any] = {}
        path = _arg_path(args)
        if path:
            facts["path"] = path
        command = _arg_command(args)
        if command:
            facts["command"] = command
        if "host" in args:
            facts["host"] = str(args["host"])
        domain = _arg_domain(args)
        if domain:
            facts["domain"] = domain
        if "namespace" in args:
            facts["namespace"] = str(args["namespace"])
        if "cluster" in args:
            facts["cluster"] = str(args["cluster"])
        if "resource" in args:
            facts["resource"] = str(args["resource"])
        return facts

    def _resolve_risk(self, inv: InvocationSummary) -> RiskSpec:
        keys = [
            f"{inv.tool}.{inv.method}",
            f"{inv.tool}.*",
            f"*.{inv.method}",
            "*.*",
        ]
        for key in keys:
            if key in self._risk_registry:
                return self._risk_registry[key]
        return self._infer_risk(inv)

    def _infer_risk(self, inv: InvocationSummary) -> RiskSpec:
        method = inv.method.lower()
        tool = inv.tool.lower()
        text = f"{tool}.{method}"
        args = inv.args

        if any(
            token in method
            for token in (
                POLICY_RISK_READ,
                "list",
                "show",
                "get",
                "status",
                "which",
                "snapshot",
                "tail",
            )
        ):
            risk_class: RiskClass = POLICY_RISK_READ
            return RiskSpec(
                risk_class=risk_class,
                side_effects=POLICY_SIDE_EFFECT_NONE,
                reversibility=POLICY_REVERSIBILITY_REVERSIBLE,
                default_confirm=False,
            )

        if any(
            token in text
            for token in ("delete", "remove", "rm", "drop", "destroy", "wipe", "kill")
        ):
            return RiskSpec(
                risk_class=POLICY_RISK_DESTRUCTIVE,
                side_effects=POLICY_SIDE_EFFECT_LOCAL,
                reversibility=POLICY_REVERSIBILITY_IRREVERSIBLE,
                default_confirm=True,
            )

        if any(token in text for token in ("pay", "buy", "transfer", "charge")):
            return RiskSpec(
                risk_class=POLICY_RISK_FINANCIAL,
                side_effects=POLICY_SIDE_EFFECT_EXTERNAL_ACCOUNT,
                reversibility=POLICY_REVERSIBILITY_UNKNOWN,
                default_confirm=True,
            )

        if any(token in text for token in ("exec", "run", "ssh", "shell")):
            side = (
                POLICY_SIDE_EFFECT_REMOTE if "ssh" in text else POLICY_SIDE_EFFECT_LOCAL
            )
            return RiskSpec(
                risk_class=POLICY_RISK_EXEC,
                side_effects=side,  # type: ignore[arg-type]
                reversibility=POLICY_REVERSIBILITY_UNKNOWN,
                default_confirm=True,
            )

        if any(
            token in text
            for token in (
                "click",
                "submit",
                "apply",
                "start",
                "stop",
                "restart",
                "write",
                "update",
            )
        ):
            side = (
                POLICY_SIDE_EFFECT_EXTERNAL_ACCOUNT
                if "browser" in tool
                else POLICY_SIDE_EFFECT_LOCAL
            )
            return RiskSpec(
                risk_class=POLICY_RISK_STATE_CHANGE,
                side_effects=side,  # type: ignore[arg-type]
                reversibility=POLICY_REVERSIBILITY_PARTIALLY_REVERSIBLE,
                default_confirm=True,
            )

        if "path" in args or "content" in args:
            return RiskSpec(
                risk_class=POLICY_RISK_WRITE,
                side_effects=POLICY_SIDE_EFFECT_LOCAL,
                reversibility=POLICY_REVERSIBILITY_PARTIALLY_REVERSIBLE,
                default_confirm=True,
            )

        return RiskSpec(
            risk_class=POLICY_RISK_READ,
            side_effects=POLICY_SIDE_EFFECT_NONE,
            reversibility=POLICY_REVERSIBILITY_UNKNOWN,
            default_confirm=False,
        )

    def _normalize_invocation(self, invocation: Any) -> InvocationSummary:
        if isinstance(invocation, dict):
            payload = dict(invocation)
            tool = str(payload.get("tool", "")).strip()
            method = str(payload.get("method", "")).strip()
            args = payload.get("args", {})
            invocation_id = (
                str(payload.get("invocation_id", "")).strip() or "invocation-local"
            )
        else:
            tool = str(getattr(invocation, "tool", "")).strip()
            method = str(getattr(invocation, "method", "")).strip()
            args = getattr(invocation, "args", {}) or {}
            invocation_id = (
                str(getattr(invocation, "invocation_id", "")).strip()
                or "invocation-local"
            )

        if not method and tool and "." in tool:
            left, right = tool.rsplit(".", 1)
            tool, method = left, right
        if not tool:
            raise ValueError("invocation.tool is required")
        if not method:
            raise ValueError("invocation.method is required")
        if not isinstance(args, dict):
            raise ValueError("invocation.args must be a dict")
        return InvocationSummary(
            invocation_id=invocation_id,
            tool=tool,
            method=method,
            args=dict(args),
            invocation_hash=stable_invocation_hash(
                tool=tool, method=method, args=dict(args)
            ),
        )

    def _normalize_context(self, ctx: Any) -> ContextSummary:
        if isinstance(ctx, dict):
            payload = dict(ctx)
            return ContextSummary(
                trace_id=_opt_str(payload.get("trace_id")),
                session_id=_opt_str(payload.get("session_id")),
                agent_id=_opt_str(payload.get("agent_id")),
                subject_id=_opt_str(payload.get("subject_id")),
                mode_name=_opt_str(payload.get("mode_name")),
            )
        return ContextSummary(
            trace_id=_opt_str(getattr(ctx, "trace_id", None)),
            session_id=_opt_str(getattr(ctx, "session_id", None)),
            agent_id=_opt_str(getattr(ctx, "agent_id", None)),
            subject_id=_opt_str(getattr(ctx, "subject_id", None)),
            mode_name=_opt_str(getattr(ctx, "mode_name", None)),
        )

    def _log_decision(
        self, *, inv: InvocationSummary, ctx: ContextSummary, decision: PolicyDecision
    ) -> None:
        if not self._config.decision_log_enabled:
            return
        self._store.log_decision(
            trace_id=ctx.trace_id,
            session_id=ctx.session_id,
            agent_id=ctx.agent_id,
            invocation_id=inv.invocation_id,
            tool=inv.tool,
            method=inv.method,
            decision=decision.decision.lower(),
            matched_grant_id=decision.matched_grant_id,
            reason_code=decision.reason_code,
            risk_spec=decision.risk.to_dict(),
        )


def _opt_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
