from __future__ import annotations

import logging
import time
import uuid
from collections import deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from threading import RLock
from typing import Any

from openminion.modules.tool.contracts import ProviderToolSpec
from openminion.modules.tool.constants import TOOL_EXPOSURE_EVENT_HISTORY_LIMIT
from openminion.modules.tool.errors import ToolRuntimeError

from .contracts import (
    ExposureReason,
    ToolCatalogCard,
    ToolExposureDecision,
    ToolExposureProfile,
    ToolExposureSession,
)
from .defaults import requires_explicit_exposure_profile

_LOG = logging.getLogger(__name__)


def _tokens(values: Iterable[object] | object | None) -> frozenset[str]:
    if values is None:
        return frozenset()
    raw_values = (
        values if isinstance(values, (list, tuple, set, frozenset)) else [values]
    )
    return frozenset(
        str(value or "").strip() for value in raw_values if str(value or "").strip()
    )


class ToolExposureService:
    """Own explicit, scoped exposure decisions without interpreting user prose."""

    def __init__(self, profiles: Iterable[ToolExposureProfile] = ()) -> None:
        self._profiles: dict[str, ToolExposureProfile] = {}
        self._activations: dict[tuple[str, str, str, str], ToolExposureSession] = {}
        self._events: deque[dict[str, Any]] = deque(
            maxlen=TOOL_EXPOSURE_EVENT_HISTORY_LIMIT
        )
        self._event_sink: Callable[[dict[str, Any]], None] | None = None
        self._lock = RLock()
        self.register_profiles(profiles)

    def register_profiles(self, profiles: Iterable[ToolExposureProfile]) -> None:
        incoming: dict[str, ToolExposureProfile] = {}
        for profile in profiles:
            if profile.profile_id in incoming:
                raise ToolRuntimeError(
                    "INVALID_ARGUMENT", "profile_id values must be unique"
                )
            incoming[profile.profile_id] = profile
        with self._lock:
            conflicts = [
                profile_id
                for profile_id, profile in incoming.items()
                if profile_id in self._profiles
                and self._profiles[profile_id] != profile
            ]
            if conflicts:
                raise ToolRuntimeError(
                    "INVALID_ARGUMENT",
                    "profile_id values already own different exposure profiles",
                    {"profile_ids": sorted(conflicts)},
                )
            self._profiles.update(incoming)

    def bind_event_sink(
        self,
        sink: Callable[[dict[str, Any]], None] | None,
    ) -> None:
        with self._lock:
            self._event_sink = sink

    @property
    def profiles(self) -> tuple[ToolExposureProfile, ...]:
        with self._lock:
            return tuple(
                sorted(self._profiles.values(), key=lambda item: item.profile_id)
            )

    @property
    def events(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            return tuple(dict(event) for event in self._events)

    def profile(self, profile_id: str) -> ToolExposureProfile | None:
        with self._lock:
            return self._profiles.get(str(profile_id or "").strip())

    def activate(
        self,
        profile_id: str,
        *,
        session_id: str,
        task_id: str = "",
        target_id: str = "",
        target_kind: str = "",
        credential_scopes: Iterable[str] = (),
        dependencies: Iterable[str] = (),
        approved: bool = False,
        ttl_seconds: float | None = None,
        activation_reason: str = "",
        approved_by: str = "",
        policy_source: str = "",
    ) -> ToolExposureSession:
        profile = self.profile(profile_id)
        if profile is None:
            raise KeyError(profile_id)
        session_id = str(session_id or "").strip()
        if not session_id:
            raise ToolRuntimeError("INVALID_ARGUMENT", "session_id is required")
        if ttl_seconds is not None and float(ttl_seconds) <= 0:
            raise ToolRuntimeError(
                "INVALID_ARGUMENT", "ttl_seconds must be greater than zero"
            )
        audit_id = uuid.uuid4().hex
        try:
            self._validate_activation(
                profile,
                target_id=target_id,
                target_kind=target_kind,
                credential_scopes=_tokens(credential_scopes),
                dependencies=_tokens(dependencies),
                approved=approved,
            )
        except ToolRuntimeError as exc:
            self._record(
                "activation_denied",
                profile_id=profile.profile_id,
                session_id=session_id,
                task_id=task_id,
                target_id=target_id,
                audit_id=audit_id,
                reason_code=exc.message,
            )
            raise
        activation = ToolExposureSession(
            profile_id=profile.profile_id,
            session_id=session_id,
            task_id=str(task_id or "").strip(),
            target_id=str(target_id or "").strip(),
            audit_id=audit_id,
            expires_at=(time.time() + float(ttl_seconds)) if ttl_seconds else None,
            activation_reason=str(activation_reason or "").strip(),
            approved_by=str(approved_by or "").strip(),
            policy_source=str(policy_source or "").strip(),
        )
        key = self._activation_key(activation)
        with self._lock:
            self._activations[key] = activation
        self._record("activated", activation=activation)
        return activation

    def deactivate(
        self,
        profile_id: str,
        *,
        session_id: str,
        task_id: str = "",
        target_id: str = "",
    ) -> bool:
        key = (
            str(session_id or "").strip(),
            str(task_id or "").strip(),
            str(target_id or "").strip(),
            str(profile_id or "").strip(),
        )
        with self._lock:
            activation = self._activations.pop(key, None)
        if activation is not None:
            self._record("deactivated", activation=activation)
        return activation is not None

    def decide(
        self,
        tool_name: str,
        *,
        session_id: str = "",
        task_id: str = "",
        target_id: str = "",
    ) -> ToolExposureDecision:
        name = str(tool_name or "").strip()
        profiles = [profile for profile in self.profiles if name in profile.tool_names]
        if not profiles:
            if requires_explicit_exposure_profile(name):
                return ToolExposureDecision(
                    tool_name=name,
                    state="hidden",
                    reason_code="profile_inactive",
                    target_id=str(target_id or "").strip(),
                )
            return ToolExposureDecision(tool_name=name, state="visible")
        for profile in profiles:
            if profile.default_active:
                return ToolExposureDecision(
                    tool_name=name,
                    state="visible",
                    profile_id=profile.profile_id,
                    target_id=str(target_id or "").strip(),
                )
            activation = self._matching_activation(
                profile.profile_id,
                session_id=session_id,
                task_id=task_id,
                target_id=target_id,
            )
            if activation is not None:
                return ToolExposureDecision(
                    tool_name=name,
                    state="visible",
                    profile_id=profile.profile_id,
                    activation_id=activation.audit_id,
                    target_id=activation.target_id,
                )
        return ToolExposureDecision(
            tool_name=name,
            state="hidden",
            profile_id=profiles[0].profile_id,
            reason_code="profile_inactive",
            target_id=str(target_id or "").strip(),
        )

    def filter_specs(
        self,
        specs: Sequence[ProviderToolSpec],
        *,
        session_id: str = "",
        task_id: str = "",
        target_id: str = "",
    ) -> list[ProviderToolSpec]:
        return [
            spec
            for spec in specs
            if self.decide(
                str(spec.name or ""),
                session_id=session_id,
                task_id=task_id,
                target_id=target_id,
            ).state
            == "visible"
        ]

    def cards(
        self,
        *,
        session_id: str = "",
        task_id: str = "",
        target_id: str = "",
    ) -> tuple[ToolCatalogCard, ...]:
        cards: list[ToolCatalogCard] = []
        for profile in self.profiles:
            activation = self._matching_activation(
                profile.profile_id,
                session_id=session_id,
                task_id=task_id,
                target_id=target_id,
            )
            if profile.default_active or activation is not None:
                cards.append(profile.card(activation))
        return tuple(cards)

    def snapshot(
        self,
        *,
        session_id: str = "",
        task_id: str = "",
        target_id: str = "",
    ) -> dict[str, Any]:
        profiles = [
            {
                "profile_id": profile.profile_id,
                "title": profile.title,
                "tier": profile.risk.tier,
                "default_active": profile.default_active,
                "active": profile.default_active
                or self._matching_activation(
                    profile.profile_id,
                    session_id=session_id,
                    task_id=task_id,
                    target_id=target_id,
                )
                is not None,
                "tool_names": sorted(profile.tool_names),
            }
            for profile in self.profiles
        ]
        with self._lock:
            events = [
                dict(event)
                for event in self._events
                if self._event_matches_scope(
                    event,
                    session_id=session_id,
                    task_id=task_id,
                    target_id=target_id,
                )
            ]
        return {
            "profiles": profiles,
            "events": events,
        }

    def record_refusal(
        self,
        decision: ToolExposureDecision,
        *,
        session_id: str = "",
        task_id: str = "",
        target_id: str = "",
    ) -> None:
        self._record(
            "invocation_refused",
            profile_id=decision.profile_id,
            session_id=session_id,
            task_id=task_id,
            target_id=target_id or decision.target_id,
            audit_id=decision.activation_id,
            reason_code=decision.reason_code or "profile_inactive",
            tool_name=decision.tool_name,
        )

    def record_invocation(
        self,
        decision: ToolExposureDecision,
        *,
        session_id: str = "",
        task_id: str = "",
        target_id: str = "",
    ) -> None:
        if not decision.profile_id:
            return
        self._record(
            "invoked",
            profile_id=decision.profile_id,
            session_id=session_id,
            task_id=task_id,
            target_id=target_id or decision.target_id,
            audit_id=decision.activation_id,
            tool_name=decision.tool_name,
        )

    @staticmethod
    def _activation_key(activation: ToolExposureSession) -> tuple[str, str, str, str]:
        return (
            activation.session_id,
            activation.task_id,
            activation.target_id,
            activation.profile_id,
        )

    @staticmethod
    def _validate_activation(
        profile: ToolExposureProfile,
        *,
        target_id: str,
        target_kind: str,
        credential_scopes: frozenset[str],
        dependencies: frozenset[str],
        approved: bool,
    ) -> None:
        reason: ExposureReason | None = None
        if profile.target_kinds and not str(target_id or "").strip():
            reason = "target_missing"
        elif (
            profile.target_kinds
            and str(target_kind or "").strip() not in profile.target_kinds
        ):
            reason = "risk_denied"
        elif not profile.credential_scopes.issubset(credential_scopes):
            reason = "credential_missing"
        elif not profile.dependencies.issubset(dependencies):
            reason = "dependency_missing"
        elif profile.risk.requires_approval and not approved:
            reason = "approval_required"
        if reason:
            raise ToolRuntimeError(
                "INVALID_ARGUMENT",
                reason,
                {"reason_code": reason},
            )

    def _matching_activation(
        self,
        profile_id: str,
        *,
        session_id: str,
        task_id: str,
        target_id: str,
    ) -> ToolExposureSession | None:
        now = time.time()
        requested_session = str(session_id or "").strip()
        requested_task = str(task_id or "").strip()
        requested_target = str(target_id or "").strip()
        expired: list[ToolExposureSession] = []
        match: ToolExposureSession | None = None
        with self._lock:
            candidates = list(self._activations.items())
            for key, activation in candidates:
                if activation.expires_at is not None and activation.expires_at <= now:
                    self._activations.pop(key, None)
                    expired.append(activation)
                    continue
                if activation.profile_id != profile_id:
                    continue
                if activation.session_id != requested_session:
                    continue
                if activation.task_id and activation.task_id != requested_task:
                    continue
                if activation.target_id and activation.target_id != requested_target:
                    continue
                match = activation
                break
        for activation in expired:
            self._record("expired", activation=activation)
        return match

    @staticmethod
    def _event_matches_scope(
        event: Mapping[str, Any],
        *,
        session_id: str,
        task_id: str,
        target_id: str,
    ) -> bool:
        for key, value in (
            ("session_id", session_id),
            ("task_id", task_id),
            ("target_id", target_id),
        ):
            expected = str(value or "").strip()
            if expected and str(event.get(key, "") or "").strip() != expected:
                return False
        return True

    def _record(
        self,
        event: str,
        *,
        activation: ToolExposureSession | None = None,
        profile_id: str = "",
        session_id: str = "",
        task_id: str = "",
        target_id: str = "",
        audit_id: str = "",
        reason_code: str = "",
        tool_name: str = "",
    ) -> None:
        if activation is not None:
            profile_id = activation.profile_id
            session_id = activation.session_id
            task_id = activation.task_id
            target_id = activation.target_id
            audit_id = activation.audit_id
            activation_reason = activation.activation_reason
            approved_by = activation.approved_by
            policy_source = activation.policy_source
        else:
            activation_reason = ""
            approved_by = ""
            policy_source = ""
        record = {
            "event": event,
            "profile_id": str(profile_id or "").strip(),
            "session_id": str(session_id or "").strip(),
            "task_id": str(task_id or "").strip(),
            "target_id": str(target_id or "").strip(),
            "audit_id": str(audit_id or "").strip(),
            "reason_code": str(reason_code or "").strip(),
            "tool_name": str(tool_name or "").strip(),
            "activation_reason": activation_reason,
            "approved_by": approved_by,
            "policy_source": policy_source,
            "timestamp": time.time(),
        }
        with self._lock:
            self._events.append(record)
            sink = self._event_sink
        if sink is not None:
            try:
                sink(dict(record))
            except Exception:
                _LOG.warning("tool exposure event sink failed", exc_info=True)


def exposure_scope(metadata: Mapping[str, Any] | None) -> dict[str, str]:
    values = metadata if isinstance(metadata, Mapping) else {}
    return {
        "session_id": str(values.get("session_id", "") or "").strip(),
        "task_id": str(values.get("task_id", "") or "").strip(),
        "target_id": str(values.get("target_id", "") or "").strip(),
    }


__all__ = ["ToolExposureService", "exposure_scope"]
