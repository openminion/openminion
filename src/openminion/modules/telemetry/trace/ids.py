from __future__ import annotations

import hashlib


def build_execution_traceparent(invocation_id: str, execution_id: str) -> str:
    trace_id = hashlib.sha256(str(invocation_id).encode("utf-8")).hexdigest()[:32]
    span_id = hashlib.sha256(str(execution_id).encode("utf-8")).hexdigest()[:16]
    return f"00-{trace_id}-{span_id}-01"


__all__ = ["build_execution_traceparent"]
