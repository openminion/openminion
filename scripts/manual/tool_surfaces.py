#!/usr/bin/env python3
"""Validate that canonical tool-facing registry and parser surfaces stay aligned."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from common.terminal_output import emit_json_report  # noqa: E402

from openminion.modules.llm.providers.tool_calling import (  # noqa: E402
    extract_fallback_tool_calls_from_text,
)
from openminion.modules.tool import build_default_tool_registry  # noqa: E402


LEGACY_MODEL_NAMES = {
    "list_files",
    "read_file",
    "write_file",
    "find_files",
    "run_command",
    "web_search",
    "web_fetch",
    "lookup_weather",
    "start_process",
    "stop_process",
    "process_status",
    "process_output",
}


def _fail(message: str) -> tuple[bool, str]:
    return False, message


def _check_model_facing_registry() -> tuple[bool, str]:
    registry = build_default_tool_registry()
    names = {spec.name for spec in registry.model_provider_specs()}
    leaked = sorted(names & LEGACY_MODEL_NAMES)
    if leaked:
        return _fail(
            "Legacy names leaked to model-facing registry: " + ", ".join(leaked)
        )
    return True, "model_provider_specs() is canonical-only"


def _check_parser_normalization() -> tuple[bool, str]:
    checks = [
        (
            '{"tool_calls":[{"name":"run_command","arguments":{"command":"pwd"}}]}',
            {"exec.run"},
            "exec.run",
        ),
        (
            '{"tool_calls":[{"name":"lookup_weather","arguments":{"location":"San Francisco"}}]}',
            {"weather"},
            "weather",
        ),
    ]
    for payload, allowed, expected in checks:
        calls = extract_fallback_tool_calls_from_text(
            payload,
            provider_name="openrouter",
            model_name="canonical-surface-guard",
            allowed_tool_names=allowed,
        )
        if not calls:
            return _fail(f"Parser did not recover call for payload={payload}")
        actual = str(calls[0].name or "")
        if actual != expected:
            return _fail(
                f"Parser normalization mismatch: expected={expected} actual={actual}"
            )
    return True, "parser fallback normalization maps legacy names to canonical IDs"


def main() -> int:
    checks = [
        _check_model_facing_registry(),
        _check_parser_normalization(),
    ]
    findings = [message for ok, message in checks if not ok]
    payload = {
        "ok": not findings,
        "checks": [
            "model_provider_specs_canonical_only",
            "parser_normalization_canonical",
        ],
    }
    emit_json_report(
        "tool_surfaces",
        payload,
        summary=(("checks", len(checks)),),
        findings=findings,
        ok_message="canonical tool-facing registry and parser surfaces are aligned.",
        report_stream=sys.stderr,
        json_stream=sys.stdout,
    )
    return 0 if not findings else 1


if __name__ == "__main__":
    raise SystemExit(main())
