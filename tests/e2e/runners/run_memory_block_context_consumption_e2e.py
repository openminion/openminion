"""Deterministic memory-block context consumption E2E probe."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from openminion.base.generated_paths import resolve_generated_root

from sophiagraph import SophiaGraphMemoryStore

from tests.context.test_memory_block_context_consumption import (
    _block,
    _request,
    _service,
)


def _default_output_path() -> Path:
    return (
        resolve_generated_root()
        / "session-context-reliability"
        / "memory-block-context-consumption-e2e.json"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prove a pinned Sophiagraph block enters OpenMinion context."
    )
    parser.add_argument("--out", type=Path, default=_default_output_path())
    args = parser.parse_args(argv)

    store = SophiaGraphMemoryStore()
    block = _block(
        "blk-e2e-pinned",
        class_name="active_mission",
        mode="pinned",
        content="Pinned E2E guidance: prefer the memory-block context proof.",
    )
    store.put_memory_block(block)

    pack = _service(store).build_pack(_request())
    segment = next(
        segment
        for segment in pack.segments
        if segment.id == "memory-block:blk-e2e-pinned"
    )
    trace = pack.context_manifest.decision_trace
    payload = {
        "scenario": "pinned_sophiagraph_block_without_retrieval_luck",
        "ok": True,
        "segment_id": segment.id,
        "bucket": segment.bucket,
        "content": segment.content,
        "retrieval_segment_count": len(
            [item for item in pack.segments if item.bucket == "retrieval"]
        ),
        "memory_selected_count": (
            pack.token_budget_report.buckets["memory"].selected_count
        ),
        "trace_memory_block_refs": list(trace.memory_block_refs if trace else []),
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
