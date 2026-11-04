"""Retain external outcomes and canonical usage for the finite transfer experiment."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import subprocess
import time

from openminion.modules.skill.learning.measurement import MeasurementObservation
from tests.e2e.cli.focus.harness import FocusProbe, FocusScenario
from tests.e2e.cli.focus.harness.assertions import read_focus_evidence
from tests.e2e.cli.focus.test_live_skill_confirmed_save import _selected_skill_events


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def retain(ctl, value: object, *, evaluator: str) -> str:
    ref = ctl.ingest_bytes(
        json.dumps(value, sort_keys=True).encode(),
        mime="application/json",
        agent_id=evaluator,
    ).ref
    ctl.ref_add("session", "verified-learning-experiment", ref)
    return ref


def canonical_usage(events: list[dict], *, provider: str, model: str) -> int | None:
    phases = [
        event["data"] for event in events if event["event_type"] == "chat.phase_timing"
    ]
    if len(phases) != 1:
        return None
    phase = phases[0]
    attempts = phase.get("provider_attempts", [])
    counts = [phase.get("provider_input_tokens"), phase.get("provider_output_tokens")]
    completions = [
        event["data"] for event in events if event["event_type"] == "llm.call.completed"
    ]
    if (
        not attempts
        or not completions
        or any(
            not all(
                isinstance(call.get("usage", {}).get(key), int)
                for key in ("input_tokens", "output_tokens")
            )
            for call in completions
        )
        or not all(isinstance(value, int) and value >= 0 for value in counts)
        or len(attempts) != phase.get("provider_calls_total")
        or len(attempts) != len(completions)
        or any(
            attempt["outcome"] != "ok"
            or attempt["provider"] != provider
            or attempt["model"] != model
            for attempt in attempts
        )
        or any(event["event_type"] == "llm.call.failed" for event in events)
    ):
        return None
    if any(
        sum(call["usage"][key] for call in completions) != count
        for key, count in zip(("input_tokens", "output_tokens"), counts, strict=True)
    ):
        return None
    return sum(counts)


def oracle(
    evaluation: Path,
    python: Path,
    action: str,
    case: str,
    workspace: Path,
    output: Path,
) -> dict:
    subprocess.run(
        [
            str(python),
            "-I",
            str(evaluation / "sealed/oracle.py"),
            action,
            "--case",
            case,
            "--workspace",
            str(workspace),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    return json.loads(output.read_text())


def verify_freeze(evaluation: Path, protocol: dict) -> None:
    for filename, field in (
        ("oracle.py", "oracle_sha256"),
        ("corpus.json", "corpus_sha256"),
    ):
        assert (
            sha256((evaluation / "sealed" / filename).read_bytes()).hexdigest()
            == protocol[field]
        )


def focus_turn(
    probe: FocusProbe, prompt: str, output: Path
) -> tuple[list[dict], dict, float]:
    approvals: list[dict] = []
    result = {"completed": False, "approvals": approvals, "error": None}
    elapsed = 0.0
    with probe.session() as session:
        try:
            probe.wait_ready(session)
            started = time.monotonic()
            try:
                probe.run_turn(
                    session,
                    FocusScenario(
                        scenario_id=probe.session_id,
                        prompt=prompt,
                        timeout=300,
                        max_auto_approvals=0,
                        max_auto_continuations=0,
                    ),
                    approval_events=approvals,
                )
                result["completed"] = True
            finally:
                elapsed = time.monotonic() - started
        except (AssertionError, TimeoutError) as error:
            result["error"] = str(error)
        finally:
            output.write_text(session.visible_transcript, encoding="utf-8")
    try:
        events, _, _ = read_focus_evidence(probe.environment(), probe.session_id)
        unique = {event.event_id: event.to_dict() for event in events}
        return list(unique.values()), result, elapsed
    except (AssertionError, sqlite3.Error, OSError) as error:
        result["error"] = str(error)
        result["completed"] = False
        return [], result, elapsed


def observation(
    ctl,
    *,
    probe: FocusProbe,
    prompt: str,
    events: list[dict],
    result: dict,
    external: dict,
    elapsed: float,
    evaluator: str,
    provider: str,
    model: str,
    evaluation: Path,
) -> MeasurementObservation:
    calls = [event for event in events if event["event_type"] == "tool.call.requested"]
    trace_ids = {
        event["data"]["trace_id"]
        for event in events
        if event["event_type"] == "llm.call.completed" and event["data"].get("trace_id")
    }
    trace_id = next(iter(trace_ids)) if len(trace_ids) == 1 else None
    selected = {}
    if events:
        for event in _selected_skill_events(probe):
            ref = event["payload"]["skill_ref"]
            selected[ref["id"]] = ref["version"]
    commands = json.dumps(calls)
    contaminated = any(
        value in commands
        for value in (str(evaluation), "oracle.py", "protocol-freeze.json", "../")
    )
    return MeasurementObservation(
        session_id=probe.session_id,
        trace_id=trace_id,
        trace_ref=retain(
            ctl, {"session_id": probe.session_id, "events": events}, evaluator=evaluator
        )
        if events
        else None,
        command_ref=retain(
            ctl,
            {
                "session_id": probe.session_id,
                "argv": probe.command(),
                "prompt": prompt,
                "tool_requests": calls,
            },
            evaluator=evaluator,
        ),
        result_ref=retain(
            ctl,
            {"session_id": probe.session_id, "focus": result, "external": external},
            evaluator=evaluator,
        ),
        selected_skills=selected,
        outcome=external["external_outcome"] if result["completed"] else "unavailable",
        tokens=canonical_usage(events, provider=provider, model=model),
        tool_calls=len(calls),
        elapsed_seconds=elapsed,
        interventions=len(result["approvals"]),
        contaminated=contaminated,
    )
