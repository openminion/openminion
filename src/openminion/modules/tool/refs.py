from typing import Any


def tool_result_artifact_refs(
    *,
    session_id: str,
    result: Any,
    tool_name: str | None = None,
    trace_id: str | None = None,
    call_id: str | None = None,
) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    if result is None:
        return refs
    resolved_tool = str(tool_name or getattr(result, "tool_name", "")).strip() or "tool"
    resolved_call = str(call_id or getattr(result, "call_id", "")).strip() or "call"
    data = getattr(result, "data", {}) or {}
    if isinstance(data, dict):
        candidates = data.get("artifact_refs")
        if isinstance(candidates, list):
            seen: set[str] = set()
            for item in candidates:
                if isinstance(item, dict):
                    ref = str(item.get("ref", "") or "").strip()
                else:
                    ref = str(item or "").strip()
                if not ref or ref in seen:
                    continue
                seen.add(ref)
                refs.append(
                    {
                        "ref": ref,
                        "type": "tool_result",
                        "tool": resolved_tool,
                    }
                )
            if refs:
                return refs
        artifacts = data.get("artifacts")
        if isinstance(artifacts, dict):
            seen = set()
            for value in artifacts.values():
                ref = str(value or "").strip()
                if not ref or ref in seen:
                    continue
                seen.add(ref)
                refs.append(
                    {
                        "ref": ref,
                        "type": "tool_result",
                        "tool": resolved_tool,
                    }
                )
            if refs:
                return refs
    if trace_id:
        ref = f"artifact://tool/{session_id}/{trace_id}/{resolved_tool}/{resolved_call}"
    else:
        ref = f"artifact://tool/{session_id}/{resolved_tool}"
    return [{"ref": ref, "type": "tool_result", "tool": resolved_tool}]
