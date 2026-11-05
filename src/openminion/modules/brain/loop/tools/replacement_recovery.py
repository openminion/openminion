"""One guarded file replacement after a contradictory readback."""

from __future__ import annotations

import hashlib
from typing import Any

from .contracts import canonical_tool_call_signature, semantic_batch_signature


def _replacement_key(call: Any) -> str:
    return hashlib.sha256(
        canonical_tool_call_signature(call).encode("utf-8")
    ).hexdigest()


def record_replacement_evidence(
    loop_state: Any, results: list[tuple[Any, Any]]
) -> None:
    if len(results) != 1:
        return
    call, outcome = results[0]
    args = call.arguments
    if (
        call.name not in {"file.write", "file.read"}
        or not isinstance(args.get("path"), str)
        or not args["path"]
    ):
        return
    if call.name == "file.write" and (
        args.get("expected_content_sha256") is not None
        or args.get("expected_version") is not None
    ):
        actual_signature = semantic_batch_signature([call])
        if actual_signature not in loop_state.seen_signatures:
            loop_state.seen_signatures.append(actual_signature)
        return
    result = outcome.action_result
    if result is None or result.status != "success" or outcome.job is not None:
        return
    if call.name == "file.write" and not args.get("append", False):
        if not isinstance(args.get("content"), str):
            return
        history = loop_state.scratchpad.setdefault("file_replacement_recovery", {})
        history.setdefault(
            _replacement_key(call),
            {
                "path": args["path"],
                "content_sha256": hashlib.sha256(
                    args["content"].encode("utf-8")
                ).hexdigest(),
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
            return
        observed_digest = (
            result.outputs.get("content_sha256")
            or hashlib.sha256(content.encode("utf-8")).hexdigest()
        )
        for entry in loop_state.scratchpad.get(
            "file_replacement_recovery", {}
        ).values():
            if entry["path"] != args["path"] or entry["used"]:
                continue
            if entry["result_path"] and result.outputs.get("path") not in {
                None,
                entry["result_path"],
            }:
                continue
            entry.pop("expected_content_sha256", None)
            entry.pop("expected_version", None)
            if observed_digest != entry["content_sha256"]:
                entry["expected_content_sha256"] = observed_digest
                if isinstance(result.outputs.get("version"), str):
                    entry["expected_version"] = result.outputs["version"]


def consume_replacement_recovery(loop_state: Any, tool_calls: list[Any]) -> bool:
    if len(tool_calls) != 1 or tool_calls[0].name != "file.write":
        return False
    call = tool_calls[0]
    history = loop_state.scratchpad.get("file_replacement_recovery", {})
    entry = history.get(_replacement_key(call))
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
