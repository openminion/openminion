"""One guarded file replacement after a contradictory readback."""

from __future__ import annotations

import hashlib
from typing import Any

from .contracts import canonical_tool_call_signature, semantic_batch_signature


def record_replacement_evidence(
    loop_state: Any, results: list[tuple[Any, Any]]
) -> None:
    if len(results) != 1:
        return
    history = loop_state.scratchpad.setdefault("file_replacement_recovery", {})
    for call, outcome in results:
        if (
            not isinstance(call.arguments.get("path"), str)
            or not call.arguments["path"]
        ):
            continue
        if call.name == "file.write" and (
            call.arguments.get("expected_content_sha256") is not None
            or call.arguments.get("expected_version") is not None
        ):
            actual_signature = semantic_batch_signature([call])
            if actual_signature not in loop_state.seen_signatures:
                loop_state.seen_signatures.append(actual_signature)
            continue
        result = outcome.action_result
        if result is None or result.status != "success" or outcome.job is not None:
            continue
        args = call.arguments
        if call.name == "file.write" and not args.get("append", False):
            if not isinstance(args.get("content"), str):
                continue
            history.setdefault(
                canonical_tool_call_signature(call),
                {
                    "path": args.get("path"),
                    "content": args["content"],
                    "result_path": result.outputs.get("path"),
                    "used": False,
                },
            )
        elif call.name == "file.read" and not args.get("offset", 0):
            content = result.outputs.get("content")
            if (
                not isinstance(content, str)
                or result.outputs.get("truncated", False)
                or result.outputs.get("returned_length", len(content)) != len(content)
                or result.outputs.get("total_length", len(content)) != len(content)
            ):
                continue
            for entry in history.values():
                if entry["path"] != args.get("path") or entry["used"]:
                    continue
                if entry["result_path"] and result.outputs.get("path") not in {
                    None,
                    entry["result_path"],
                }:
                    continue
                entry.pop("expected_content_sha256", None)
                entry.pop("expected_version", None)
                if content != entry["content"]:
                    entry["expected_content_sha256"] = hashlib.sha256(
                        content.encode("utf-8")
                    ).hexdigest()
                    if isinstance(result.outputs.get("version"), str):
                        entry["expected_version"] = result.outputs["version"]


def consume_replacement_recovery(loop_state: Any, tool_calls: list[Any]) -> bool:
    if len(tool_calls) != 1 or tool_calls[0].name != "file.write":
        return False
    call = tool_calls[0]
    history = loop_state.scratchpad.get("file_replacement_recovery", {})
    entry = history.get(canonical_tool_call_signature(call))
    if not entry or entry["used"] or not entry.get("expected_content_sha256"):
        return False
    entry["used"] = True
    call.arguments = {
        **call.arguments,
        "expected_content_sha256": entry["expected_content_sha256"],
    }
    if entry.get("expected_version"):
        call.arguments["expected_version"] = entry["expected_version"]
    return True
