"""Best-effort chat phase timing emission for runtime ingress."""

import asyncio
import inspect
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openminion.services.runtime.interfaces import RuntimeFacade


def _emit_chat_phase_timing(
    *,
    runtime: "RuntimeFacade",
    timer: "object",
    request: "object",
) -> None:
    try:
        from openminion.modules.telemetry.events.catalog import CHAT_PHASE_TIMING
        from openminion.modules.telemetry.schemas import TelemetryEvent

        telemetry_service = getattr(runtime, "telemetry_service", None)
        if telemetry_service is None:
            return
        session_id_str = str(getattr(request, "session_id", "") or "")
        turn_id_str = str(getattr(request, "request_id", "") or "")
        runtime_config = getattr(runtime, "config", None)
        runtime_settings = getattr(runtime_config, "runtime", None)
        process_mode = getattr(runtime_settings, "process_mode", "")
        payload = timer.build_payload(  # type: ignore[attr-defined]
            turn_id=turn_id_str,
            session_id=session_id_str,
            agent_id=str(getattr(request, "agent_id", "") or ""),
            process_mode=str(process_mode or ""),
        )
        record_sync = getattr(telemetry_service, "record_event_sync", None)
        if record_sync is not None:
            record_sync(
                TelemetryEvent(
                    session_id=session_id_str,
                    turn_id=turn_id_str,
                    event_type=CHAT_PHASE_TIMING,
                    data=payload.as_dict(),
                )
            )
            return
        emit = getattr(telemetry_service, "emit_canonical_event", None)
        if emit is None:
            return
        result = emit(
            session_id_str,
            turn_id_str,
            CHAT_PHASE_TIMING,
            payload.as_dict(),
        )
        if inspect.iscoroutine(result):
            try:
                asyncio.get_running_loop()
                asyncio.ensure_future(result)
            except RuntimeError:
                try:
                    asyncio.run(result)
                except Exception:
                    try:
                        result.close()
                    except Exception:
                        pass
    except Exception:
        pass
