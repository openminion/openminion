import hmac
from dataclasses import dataclass, field
from hashlib import sha256
from time import time
from typing import Mapping

from openminion.base.config import ChannelAuthenticityConfig
from openminion.base.config.env import resolve_environment_config

MODE_OFF = "off"
MODE_WARN = "warn"
MODE_REQUIRE = "require"


@dataclass(frozen=True)
class ChannelAuthenticityPolicy:
    mode: str = MODE_WARN
    trusted_channels: tuple[str, ...] = ("console",)
    required_channels: tuple[str, ...] = ()
    secret_env_by_channel: dict[str, str] = field(default_factory=dict)
    max_age_seconds: int = 300
    allowed_algorithms: tuple[str, ...] = ("hmac-sha256",)


@dataclass(frozen=True)
class ChannelAuthenticityEvidence:
    channel: str
    target: str
    body: str
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ChannelAuthenticityDecision:
    allowed: bool
    verified: bool
    reason_code: str
    mode: str
    details: dict[str, str] = field(default_factory=dict)

    @property
    def warning(self) -> bool:
        return (
            self.allowed and not self.verified and self.reason_code.startswith("warn_")
        )


def build_channel_authenticity_policy(
    config: ChannelAuthenticityConfig,
) -> ChannelAuthenticityPolicy:
    trusted_channels = _normalize_channel_tuple(config.trusted_channels)
    required_channels = _normalize_channel_tuple(config.required_channels)
    secret_env_by_channel = {
        key: value
        for key, value in sorted(
            (
                (str(key or "").strip().lower(), str(value or "").strip())
                for key, value in config.secret_env_by_channel.items()
            ),
            key=lambda item: item[0],
        )
        if key and value
    }
    allowed_algorithms = _normalize_algorithm_tuple(config.allowed_algorithms)
    mode = _normalize_mode(config.mode)
    return ChannelAuthenticityPolicy(
        mode=mode,
        trusted_channels=trusted_channels or ("console",),
        required_channels=required_channels,
        secret_env_by_channel=secret_env_by_channel,
        max_age_seconds=max(0, int(config.max_age_seconds)),
        allowed_algorithms=allowed_algorithms or ("hmac-sha256",),
    )


def evaluate_inbound_authenticity(
    *,
    policy: ChannelAuthenticityPolicy,
    evidence: ChannelAuthenticityEvidence,
    now_epoch_seconds: int | None = None,
) -> ChannelAuthenticityDecision:
    channel = _normalize_channel(evidence.channel)
    if not channel:
        return ChannelAuthenticityDecision(
            allowed=False,
            verified=False,
            reason_code="invalid_channel",
            mode=policy.mode,
        )

    if channel in policy.trusted_channels:
        return ChannelAuthenticityDecision(
            allowed=True,
            verified=True,
            reason_code="trusted_channel",
            mode=policy.mode,
            details={"channel": channel},
        )

    requires_signature = (
        channel in policy.required_channels or policy.mode == MODE_REQUIRE
    )
    signature = _extract_signature(evidence.metadata)
    if not signature:
        return _unsigned_signature_decision(
            policy=policy,
            channel=channel,
            requires_signature=requires_signature,
        )

    algorithm = _extract_algorithm(evidence.metadata)
    if algorithm not in policy.allowed_algorithms:
        return _unsigned_signature_decision(
            policy=policy,
            channel=channel,
            requires_signature=requires_signature,
            required_reason="unsupported_algorithm",
            warn_reason="warn_unsupported_algorithm",
            allow_reason="signature_ignored",
            extra_details={"algorithm": algorithm},
        )

    timestamp_raw = _extract_timestamp(evidence.metadata)
    if not timestamp_raw:
        timestamp_decision = _unsigned_signature_decision(
            policy=policy,
            channel=channel,
            requires_signature=requires_signature,
            required_reason="missing_signature_timestamp",
            warn_reason="warn_missing_signature_timestamp",
        )
        if timestamp_decision is not None:
            return timestamp_decision
    else:
        try:
            signature_epoch = int(timestamp_raw)
        except ValueError:
            timestamp_decision = _unsigned_signature_decision(
                policy=policy,
                channel=channel,
                requires_signature=requires_signature,
                required_reason="invalid_signature_timestamp",
                warn_reason="warn_invalid_signature_timestamp",
            )
            if timestamp_decision is not None:
                return timestamp_decision
        else:
            now_ts = int(now_epoch_seconds if now_epoch_seconds is not None else time())
            if (
                policy.max_age_seconds > 0
                and abs(now_ts - signature_epoch) > policy.max_age_seconds
            ):
                timestamp_decision = _unsigned_signature_decision(
                    policy=policy,
                    channel=channel,
                    requires_signature=requires_signature,
                    required_reason="stale_signature_timestamp",
                    warn_reason="warn_stale_signature_timestamp",
                )
                if timestamp_decision is not None:
                    return timestamp_decision

    secret_env = policy.secret_env_by_channel.get(channel, "")
    if not secret_env:
        return _unsigned_signature_decision(
            policy=policy,
            channel=channel,
            requires_signature=requires_signature,
            required_reason="missing_channel_secret_env",
            warn_reason="warn_signature_unverifiable",
            allow_reason="signature_unverifiable",
        )

    secret_value = resolve_environment_config().get(secret_env, "").strip()
    if not secret_value:
        return _unsigned_signature_decision(
            policy=policy,
            channel=channel,
            requires_signature=requires_signature,
            required_reason="missing_channel_secret_value",
            warn_reason="warn_signature_unverifiable",
            allow_reason="signature_unverifiable",
            extra_details={"secret_env": secret_env},
        )

    expected = _sign_payload(
        secret=secret_value, payload=evidence.body, algorithm=algorithm
    )
    if not hmac.compare_digest(expected, signature):
        return _unsigned_signature_decision(
            policy=policy,
            channel=channel,
            requires_signature=requires_signature,
            required_reason="invalid_signature",
            warn_reason="warn_invalid_signature",
            allow_reason="signature_invalid_ignored",
            extra_details={"algorithm": algorithm},
        )

    return ChannelAuthenticityDecision(
        allowed=True,
        verified=True,
        reason_code="verified_signature",
        mode=policy.mode,
        details={"channel": channel, "algorithm": algorithm},
    )


def _unsigned_signature_decision(
    *,
    policy: ChannelAuthenticityPolicy,
    channel: str,
    requires_signature: bool,
    required_reason: str = "missing_signature",
    warn_reason: str = "warn_missing_signature",
    allow_reason: str = "unsigned_allowed",
    extra_details: Mapping[str, str] | None = None,
) -> ChannelAuthenticityDecision | None:
    details = {"channel": channel}
    if extra_details:
        details.update(
            {
                str(key): str(value)
                for key, value in extra_details.items()
                if str(key).strip() and str(value).strip()
            }
        )
    if requires_signature:
        return ChannelAuthenticityDecision(
            allowed=False,
            verified=False,
            reason_code=required_reason,
            mode=policy.mode,
            details=details,
        )
    if policy.mode == MODE_WARN:
        return ChannelAuthenticityDecision(
            allowed=True,
            verified=False,
            reason_code=warn_reason,
            mode=policy.mode,
            details=details,
        )
    if allow_reason:
        return ChannelAuthenticityDecision(
            allowed=True,
            verified=False,
            reason_code=allow_reason,
            mode=policy.mode,
            details=details,
        )
    return None


def _normalize_mode(raw: str) -> str:
    value = str(raw or "").strip().lower()
    if value in {MODE_OFF, MODE_WARN, MODE_REQUIRE}:
        return value
    return MODE_WARN


def _normalize_channel(value: str) -> str:
    return str(value or "").strip().lower()


def _normalize_channel_tuple(values) -> tuple[str, ...]:
    normalized = {
        _normalize_channel(item) for item in values if _normalize_channel(item)
    }
    return tuple(sorted(normalized))


def _normalize_algorithm_tuple(values) -> tuple[str, ...]:
    normalized = {
        str(item or "").strip().lower() for item in values if str(item or "").strip()
    }
    return tuple(sorted(normalized))


def _extract_signature(metadata: Mapping[str, str]) -> str:
    raw = (
        str(metadata.get("auth_signature") or metadata.get("signature") or "")
        .strip()
        .lower()
    )
    if raw.startswith("sha256="):
        raw = raw.split("=", 1)[1]
    return raw


def _extract_algorithm(metadata: Mapping[str, str]) -> str:
    return (
        str(
            metadata.get("auth_signature_alg")
            or metadata.get("signature_alg")
            or "hmac-sha256"
        )
        .strip()
        .lower()
    )


def _extract_timestamp(metadata: Mapping[str, str]) -> str:
    return str(
        metadata.get("auth_signature_ts") or metadata.get("signature_ts") or ""
    ).strip()


def _sign_payload(*, secret: str, payload: str, algorithm: str) -> str:
    if algorithm != "hmac-sha256":
        return ""
    digest = hmac.new(
        key=secret.encode("utf-8"),
        msg=str(payload or "").encode("utf-8"),
        digestmod=sha256,
    ).hexdigest()
    return str(digest).strip().lower()
