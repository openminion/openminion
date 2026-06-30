"""Capture cold, warm, and TTFT chat phase timing baselines."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

from openminion.api.runtime import APIRuntime
from openminion.base.generated_paths import resolve_generated_root
from openminion.modules.telemetry.events.catalog import CHAT_PHASE_TIMING
from openminion.modules.telemetry.trace.phase_timing import CHAT_PHASES


class _BaselineSink:
    """Capture `chat.phase_timing` events from sync or async telemetry paths."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def capture_sync(self, event: object) -> None:
        event_type = getattr(event, "event_type", "")
        if event_type != CHAT_PHASE_TIMING:
            return
        self.events.append(
            {
                "session_id": getattr(event, "session_id", ""),
                "turn_id": getattr(event, "turn_id", ""),
                **dict(getattr(event, "data", {}) or {}),
            }
        )

    async def emit_canonical_event(
        self, session_id, turn_id, event_type, payload, **kwargs
    ):
        if event_type == CHAT_PHASE_TIMING:
            self.events.append(
                {
                    "session_id": session_id,
                    "turn_id": turn_id,
                    **dict(payload or {}),
                }
            )


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    try:
        return statistics.quantiles(values, n=100)[int(pct) - 1]
    except statistics.StatisticsError:
        return values[-1]


def _summarize(events: list[dict[str, Any]]) -> dict[str, Any]:
    if not events:
        return {"runs": 0, "note": "no events captured"}
    totals = [int(e.get("total_turn_ms", 0)) for e in events]
    ttfts = [
        int(e["time_to_first_text_ms"])
        for e in events
        if e.get("time_to_first_text_ms") is not None
    ]
    summary: dict[str, Any] = {
        "runs": len(events),
        "total_turn_ms_p50": _percentile(sorted(totals), 50),
        "total_turn_ms_p95": _percentile(sorted(totals), 95),
        "total_turn_ms_max": max(totals),
        "ttft_ms_p50": _percentile(sorted(ttfts), 50) if ttfts else None,
        "ttft_ms_p95": _percentile(sorted(ttfts), 95) if ttfts else None,
        "ttft_observations": len(ttfts),
        "cold_start_observed": any(bool(e.get("cold_start")) for e in events),
    }
    per_phase: dict[str, dict[str, float | None]] = {}
    for phase in CHAT_PHASES:
        ms_values = sorted(int(e.get(f"{phase}_ms", 0)) for e in events)
        per_phase[phase] = {
            "p50": _percentile(ms_values, 50),
            "p95": _percentile(ms_values, 95),
            "max": ms_values[-1] if ms_values else None,
        }
    summary["per_phase_ms"] = per_phase
    return summary


def _run_one_turn(
    *,
    runtime: APIRuntime,
    agent_id: str,
    session_id: str,
    message: str,
    cold_start: bool,
) -> dict[str, Any]:
    started = time.perf_counter()
    payload = {
        "agent_id": agent_id,
        "session_id": session_id,
        "message": message,
        "__crtl_cold_start__": cold_start,
    }
    try:
        result = runtime.run_turn(payload=payload)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return {
            "ok": True,
            "wall_clock_ms": elapsed_ms,
            "result_keys": sorted(result.keys()) if isinstance(result, dict) else [],
        }
    except Exception as exc:  # noqa: BLE001 - baseline must capture failures
        return {
            "ok": False,
            "wall_clock_ms": int((time.perf_counter() - started) * 1000),
            "error": f"{type(exc).__name__}: {exc}",
        }


def _measure_path(
    *,
    path_id: str,
    config_path: str,
    agent_id: str,
    message: str,
    runs: int,
) -> dict[str, Any]:
    """Measure one path × N runs.  Returns the per-path summary block."""

    sink = _BaselineSink()
    runtime: APIRuntime | None = None
    raw_runs: list[dict[str, Any]] = []
    try:
        runtime = APIRuntime.from_config_path(config_path)
        # Tap `TelemetryService.record_event_sync` — that's the sync seam
        # the chat ingress hits.  Async `emit_canonical_event` is tapped
        # only when present (test stubs).
        if runtime.telemetry_service is not None:
            real_sync = getattr(runtime.telemetry_service, "record_event_sync", None)
            if real_sync is not None:

                def _tap_sync(event):
                    sink.capture_sync(event)
                    return real_sync(event)

                runtime.telemetry_service.record_event_sync = _tap_sync  # type: ignore[assignment]

        for i in range(int(runs)):
            cold = path_id != "warm_daemon" or i == 0
            session_id = f"crtl-baseline-{path_id}-{i}"
            run_result = _run_one_turn(
                runtime=runtime,
                agent_id=agent_id,
                session_id=session_id,
                message=message,
                cold_start=cold,
            )
            raw_runs.append({"run_index": i, "cold_start": cold, **run_result})
    except Exception as exc:  # noqa: BLE001
        return {
            "path_id": path_id,
            "error": f"{type(exc).__name__}: {exc}",
            "events": [],
            "summary": {"runs": 0, "note": "path setup failed"},
        }
    finally:
        if runtime is not None:
            try:
                runtime.close()
            except Exception:
                pass

    return {
        "path_id": path_id,
        "events": list(sink.events),
        "runs_raw": raw_runs,
        "summary": _summarize(list(sink.events)),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="OpenMinion config path")
    parser.add_argument("--agent", required=True, help="Agent id to invoke")
    parser.add_argument("--message", default="hey", help="Turn message text")
    parser.add_argument("--runs", type=int, default=3, help="Runs per path")
    parser.add_argument(
        "--paths",
        default="cold_single_process",
        help="Comma-separated path ids",
    )
    args = parser.parse_args(argv)

    paths = [p.strip() for p in args.paths.split(",") if p.strip()]
    artifact: dict[str, Any] = {
        "command": " ".join(sys.argv),
        "config": args.config,
        "agent": args.agent,
        "message": args.message,
        "runs_per_path": args.runs,
        "notes": [
            "Some per-phase timing fields remain 0 until those phases expose direct instrumentation.",
            "`time_to_first_text_ms` stays None until the runtime emits first-text timing.",
            "The `warm_daemon` path currently exercises the same in-process "
            "runtime as `cold_single_process` (one APIRuntime reused across "
            "runs); a separate daemon path can add a stronger comparison later.",
            "Three load-bearing wall-clock metrics: cold full-turn, warm "
            "full-turn, and TTFT are tracked separately in the output artifact.",
        ],
        "paths": {},
    }
    for path_id in paths:
        print(f"[crtl-baseline] measuring path={path_id} runs={args.runs}")
        artifact["paths"][path_id] = _measure_path(
            path_id=path_id,
            config_path=args.config,
            agent_id=args.agent,
            message=args.message,
            runs=args.runs,
        )
        summary = artifact["paths"][path_id]["summary"]
        if summary.get("runs", 0) > 0:
            print(
                f"  runs={summary['runs']} "
                f"total_p50={summary['total_turn_ms_p50']}ms "
                f"total_p95={summary['total_turn_ms_p95']}ms "
                f"ttft_obs={summary['ttft_observations']}"
            )
        elif "error" in artifact["paths"][path_id]:
            print(f"  error: {artifact['paths'][path_id]['error']}")

    out_dir = Path(resolve_generated_root()) / "crtl-baseline"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "crtl-baseline.json"
    out_path.write_text(json.dumps(artifact, indent=2, default=str))
    print(f"[crtl-baseline] wrote {out_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover - manual entrypoint
    raise SystemExit(main())
