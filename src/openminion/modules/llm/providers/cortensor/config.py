from collections.abc import Mapping
from dataclasses import replace

from openminion.base.config import OpenMinionConfig
from openminion.base.config.env import resolve_environment_config


def resolve_cortensor_runtime_config(
    config: OpenMinionConfig,
    *,
    env: Mapping[str, str] | None = None,
):
    provider_config = config.providers.cortensor
    resolved_env = _resolve_env(env)
    base_url_override = str(resolved_env.get("CORTENSOR_API_URL", "")).strip()
    mode_override = str(resolved_env.get("CORTENSOR_API_MODE", "")).strip()
    session_id_override_raw = str(resolved_env.get("CORTENSOR_SESSION_ID", "")).strip()
    session_ids_override_raw = str(
        resolved_env.get("CORTENSOR_SESSION_IDS", "")
    ).strip()
    session_pool_override_raw = str(
        resolved_env.get("CORTENSOR_SESSION_POOL", "")
    ).strip()
    dedicated_session_ids_override_raw = str(
        resolved_env.get("CORTENSOR_DEDICATED_SESSION_IDS", "")
    ).strip()
    ephemeral_session_ids_override_raw = str(
        resolved_env.get("CORTENSOR_EPHEMERAL_SESSION_IDS", "")
    ).strip()
    session_parallel_requests_override_raw = str(
        resolved_env.get("CORTENSOR_SESSION_PARALLEL_REQUESTS", "")
    ).strip()
    session_retry_rounds_override_raw = str(
        resolved_env.get("CORTENSOR_SESSION_RETRY_ROUNDS", "")
    ).strip()
    max_tokens_override_raw = str(resolved_env.get("CORTENSOR_MAX_TOKENS", "")).strip()

    session_id_override = provider_config.session_id
    if session_id_override_raw:
        try:
            parsed = int(session_id_override_raw)
            if parsed > 0:
                session_id_override = parsed
        except ValueError:
            pass

    session_ids_override = list(provider_config.session_ids)
    if session_ids_override_raw:
        parsed_ids = _parse_positive_session_ids_csv(session_ids_override_raw)
        if parsed_ids:
            session_ids_override = parsed_ids

    session_pool_override = provider_config.session_pool
    if session_pool_override_raw:
        session_pool_override = session_pool_override_raw

    dedicated_session_ids_override = list(provider_config.dedicated_session_ids)
    if dedicated_session_ids_override_raw:
        parsed_dedicated_ids = _parse_positive_session_ids_csv(
            dedicated_session_ids_override_raw
        )
        if parsed_dedicated_ids:
            dedicated_session_ids_override = parsed_dedicated_ids

    ephemeral_session_ids_override = list(provider_config.ephemeral_session_ids)
    if ephemeral_session_ids_override_raw:
        parsed_ephemeral_ids = _parse_positive_session_ids_csv(
            ephemeral_session_ids_override_raw
        )
        if parsed_ephemeral_ids:
            ephemeral_session_ids_override = parsed_ephemeral_ids

    session_parallel_requests_override = provider_config.session_parallel_requests
    if session_parallel_requests_override_raw:
        try:
            parsed_parallel = int(session_parallel_requests_override_raw)
            if parsed_parallel > 0:
                session_parallel_requests_override = parsed_parallel
        except ValueError:
            pass

    session_retry_rounds_override = provider_config.session_retry_rounds
    if session_retry_rounds_override_raw:
        try:
            parsed_retry_rounds = int(session_retry_rounds_override_raw)
            if parsed_retry_rounds > 0:
                session_retry_rounds_override = parsed_retry_rounds
        except ValueError:
            pass

    max_tokens_override = provider_config.max_tokens
    if max_tokens_override_raw:
        try:
            parsed_max_tokens = int(max_tokens_override_raw)
            if parsed_max_tokens > 0:
                max_tokens_override = parsed_max_tokens
        except ValueError:
            pass

    return replace(
        provider_config,
        base_url=base_url_override or provider_config.base_url,
        api_mode=mode_override or provider_config.api_mode,
        session_id=session_id_override,
        session_ids=session_ids_override,
        session_pool=session_pool_override,
        dedicated_session_ids=dedicated_session_ids_override,
        ephemeral_session_ids=ephemeral_session_ids_override,
        session_parallel_requests=session_parallel_requests_override,
        session_retry_rounds=session_retry_rounds_override,
        max_tokens=max_tokens_override,
    )


def _parse_positive_session_ids_csv(raw_value: str) -> list[int]:
    parsed_ids: list[int] = []
    for part in [item.strip() for item in str(raw_value).split(",")]:
        if not part:
            continue
        try:
            parsed = int(part)
        except ValueError:
            continue
        if parsed > 0:
            parsed_ids.append(parsed)
    return parsed_ids


def _resolve_env(env: Mapping[str, str] | None) -> Mapping[str, str]:
    if env is not None:
        return env
    return resolve_environment_config()
