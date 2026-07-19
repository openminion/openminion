from __future__ import annotations

import re

from openminion.services.agent.memory import MEMORY_POLICY_SNAPSHOT_VERSION


def _extract_memory_policy_metadata(*, response_text: str) -> dict[str, str] | None:
    text = str(response_text or "").strip()
    if not text:
        return None

    if text.lower().startswith("memory policy snapshot:"):
        source = "runtime.config"
        version = MEMORY_POLICY_SNAPSHOT_VERSION
        for raw_line in text.splitlines():
            line = str(raw_line or "").strip()
            if line.startswith("-"):
                line = line[1:].strip()
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            normalized = key.strip().lower().replace(" ", "_")
            parsed_value = value.strip()
            if normalized == "source" and parsed_value:
                source = parsed_value
            elif normalized == "version" and parsed_value:
                version = parsed_value
        return {
            "memory_policy_route": "runtime_policy_snapshot",
            "memory_policy_source": source,
            "memory_policy_version": version,
            "reason_code": "memory_policy_snapshot",
            "response_posture": "deterministic",
        }

    if text.startswith("MEMORY_POLICY: policy_unavailable"):
        source_match = re.search(r"source=([^\s)]+)", text)
        version_match = re.search(r"version=([^\s)]+)", text)
        reason_match = re.search(r"reason=([^\s)]+)", text)
        metadata = {
            "memory_policy_route": "runtime_policy_snapshot",
            "memory_policy_source": source_match.group(1)
            if source_match
            else "runtime.config",
            "memory_policy_version": version_match.group(1)
            if version_match
            else MEMORY_POLICY_SNAPSHOT_VERSION,
            "reason_code": "policy_unavailable",
            "response_posture": "degraded",
        }
        if reason_match:
            metadata["memory_policy_error"] = reason_match.group(1)
        return metadata

    return None
