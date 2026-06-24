from typing import Any

from openminion.modules.brain.adapters.factory import create_rlm_adapter


def _is_rlm_enabled(config: Any) -> bool:
    enabled = False
    try:
        rlm_cfg = getattr(config, "rlm", None)
        if rlm_cfg is not None:
            enabled = getattr(rlm_cfg, "enabled", False)
        elif hasattr(config, "extra") and isinstance(config.extra, dict):
            enabled = config.extra.get("rlm", {}).get("enabled", False)
    except Exception:
        enabled = False
    return bool(enabled)


def init_rlm_adapter(
    *,
    mode: str,
    config: Any,
    session_api: Any,
    context_api: Any,
    llm_api: Any,
    memory_api: Any | None,
    skill_api: Any | None,
    retrieve_api: Any | None,
    logger: Any,
) -> Any:
    rlm_enabled = _is_rlm_enabled(config)

    if rlm_enabled:
        try:
            # wire through the canonical recursive-family owner
            from openminion.modules.brain.loop.recursive.service import RLMService
            from openminion.modules.brain.adapters.factory import (
                RLMBridgeSessionClient,
                RLMBridgeContextClient,
                RLMBridgeLLMClient,
                RLMBridgeMemoryClient,
            )

            rlm_service = RLMService(
                sessctl=RLMBridgeSessionClient(session_api),
                contextctl=RLMBridgeContextClient(context_api),
                llmctl=RLMBridgeLLMClient(llm_api),
                memctl=RLMBridgeMemoryClient(memory_api)
                if memory_api is not None
                else None,
                skillctl=skill_api,
                retrievectl=retrieve_api,
            )

            rlm_api = create_rlm_adapter(mode=mode, service=rlm_service)
            logger.info("RLMService wired with real bridge clients")
            return rlm_api
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "RLMService unavailable; falling back to local adapter: %s",
                exc,
            )
            return create_rlm_adapter(mode=mode, service=None)

    logger.debug("RLM disabled by config — using LocalRLMAdapter")
    return create_rlm_adapter(mode=mode, service=None)
