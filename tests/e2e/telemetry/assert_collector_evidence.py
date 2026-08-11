from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator


def assert_collector_evidence(
    artifact_root: Path,
    *,
    invocation_ids: tuple[str, ...] = (),
    require_probe: bool = False,
) -> None:
    traces = _documents(artifact_root / "traces.json") if invocation_ids else []
    logs = _documents(artifact_root / "logs.json")
    trace_text = json.dumps(traces, sort_keys=True)
    for invocation_id in invocation_ids:
        if invocation_id not in trace_text:
            raise AssertionError(f"missing lifecycle correlation: {invocation_id}")
        correlated = [
            item
            for item in _walk_dicts(traces)
            if invocation_id in json.dumps(item, sort_keys=True)
            and str(item.get("name") or "").startswith("invoke_agent")
        ]
        if not correlated:
            raise AssertionError(f"synthetic-only lifecycle evidence: {invocation_id}")
    if require_probe:
        probe_records = [
            item
            for item in _walk_dicts(logs)
            if _log_body(item) == "telemetry.export.probe"
        ]
        if len(probe_records) != 1:
            raise AssertionError(
                f"expected exactly one telemetry.export.probe LogRecord, got {len(probe_records)}"
            )
        record = probe_records[0]
        attributes = _attributes(record)
        expected = {
            "openminion.event_type": "telemetry.export.probe",
            "openminion.telemetry.probe": True,
            "openminion.payload.criticality": "diagnostic",
        }
        for key, value in expected.items():
            if attributes.get(key) != value:
                raise AssertionError(f"malformed probe attribute: {key}")
        if attributes.get("openminion.payload.protocol") not in {
            "grpc",
            "http/protobuf",
        }:
            raise AssertionError("malformed probe protocol")


def _documents(path: Path) -> list[Any]:
    if not path.is_file():
        raise AssertionError(f"missing Collector artifact: {path.name}")
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise AssertionError(f"empty Collector artifact: {path.name}")
    try:
        documents = [json.loads(line) for line in text.splitlines() if line.strip()]
    except json.JSONDecodeError as exc:
        raise AssertionError(f"invalid Collector artifact: {path.name}") from exc
    if not documents:
        raise AssertionError(f"empty Collector artifact: {path.name}")
    return documents


def _walk_dicts(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _log_body(record: dict[str, Any]) -> str:
    body = record.get("body")
    if isinstance(body, dict):
        return str(body.get("stringValue") or body.get("string_value") or "")
    return str(body or "")


def _attributes(record: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for item in record.get("attributes", []):
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "")
        value = item.get("value")
        if isinstance(value, dict) and value:
            value = next(iter(value.values()))
        result[key] = value
    return result


__all__ = ["assert_collector_evidence"]
