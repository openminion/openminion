"""Timeout and profile override helpers for runtime ingress."""

from typing import Any, Mapping, Optional

from openminion.base.config import (
    ConfigError,
    OpenMinionConfig,
    RunProfileOverrides,
    resolve_runtime_profile,
    run_profile_overrides_from_mapping,
)

from .types import TurnRequestError


def resolve_timeout_seconds(
    *,
    payload: dict[str, Any],
    default_seconds: int,
    config: Optional[OpenMinionConfig] = None,
    agent_id: Optional[str] = None,
    run_profile_overrides: RunProfileOverrides | None = None,
) -> float:
    override = payload.get("timeout_seconds")
    effective_default = float(default_seconds)
    if override is None and config is not None:
        effective_default = max(
            effective_default,
            _minimum_api_turn_timeout_for_provider(
                config,
                agent_id=agent_id,
                run_profile_overrides=run_profile_overrides,
            ),
        )
    raw_value = override if override is not None else effective_default
    try:
        timeout = float(raw_value)
    except (TypeError, ValueError) as exc:
        raise TurnRequestError(
            "`timeout_seconds` must be a number greater than zero."
        ) from exc
    if timeout <= 0:
        raise TurnRequestError("`timeout_seconds` must be greater than zero.")
    return timeout


def _minimum_api_turn_timeout_for_provider(
    config: OpenMinionConfig,
    *,
    agent_id: Optional[str] = None,
    run_profile_overrides: RunProfileOverrides | None = None,
) -> float:
    provider_name = (
        (
            resolve_runtime_profile(
                config,
                agent_id=agent_id,
                overrides=run_profile_overrides,
            ).provider
            or ""
        )
        .strip()
        .lower()
    )
    if provider_name != "cortensor":
        return 0.0

    cortensor = config.providers.cortensor
    completion_timeout = max(
        int(cortensor.timeout_seconds),
        int(cortensor.precommit_timeout_seconds),
    )
    buffer_seconds = max(0, int(cortensor.transport_timeout_buffer_seconds))
    return float(completion_timeout + buffer_seconds + 5)


def _parse_run_profile_overrides(payload: Mapping[str, Any]) -> RunProfileOverrides:
    try:
        return run_profile_overrides_from_mapping(payload)
    except ConfigError as exc:
        raise TurnRequestError(str(exc)) from exc
