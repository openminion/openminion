from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..runner import BrainRunner


def track_call_started(
    runner: "BrainRunner", llm_call_id: str, purpose: str, model: str
) -> None:
    from time import time

    runner._call_order_tracker[llm_call_id] = {
        "started_at": time(),
        "purpose": purpose,
        "model": model,
        "manifest_emitted": False,
        "completed_at": None,
    }


def track_manifest_emitted(runner: "BrainRunner", llm_call_id: str) -> None:
    if llm_call_id in runner._call_order_tracker:
        runner._call_order_tracker[llm_call_id]["manifest_emitted"] = True


def track_call_completed(runner: "BrainRunner", llm_call_id: str) -> None:
    from time import time

    if llm_call_id in runner._call_order_tracker:
        runner._call_order_tracker[llm_call_id]["completed_at"] = time()


def validate_call_order(
    runner: "BrainRunner", llm_call_id: str, expected_stage: str
) -> dict[str, Any]:
    if llm_call_id not in runner._call_order_tracker:
        return {
            "valid": False,
            "reason": f"llm.call.started not found for {llm_call_id}",
        }

    call_state = runner._call_order_tracker[llm_call_id]

    if expected_stage == "context.manifest.created":
        if call_state["manifest_emitted"]:
            return {
                "valid": False,
                "reason": "context.manifest already emitted for this call",
            }
        if call_state["completed_at"] is not None:
            return {
                "valid": False,
                "reason": "llm.call.completed already emitted before manifest",
            }
        track_manifest_emitted(runner, llm_call_id)
        return {"valid": True, "reason": None}

    if expected_stage == "llm.call.completed":
        if not call_state["manifest_emitted"]:
            return {
                "valid": False,
                "reason": "context.manifest not emitted before llm.call.completed",
            }
        return {"valid": True, "reason": None}

    return {"valid": True, "reason": None}
