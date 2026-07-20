#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[4]
OPENMINION_DIR = REPO_ROOT / "openminion"
OPENMINION_SRC = OPENMINION_DIR / "src"
if str(OPENMINION_DIR) not in sys.path:
    sys.path.insert(0, str(OPENMINION_DIR))
if str(OPENMINION_SRC) not in sys.path:
    sys.path.insert(0, str(OPENMINION_SRC))

from openminion.base.generated_paths import resolve_generated_root  # noqa: E402
from tests.e2e import test_live_skill_dense_catalog_matrix as matrix  # noqa: E402
from tests.helpers.live_skill_targets import (  # noqa: E402
    SkillLiveTarget,
    dense_skill_artifact_dirname,
    official_skill_dense_targets,
)


@dataclass(frozen=True)
class ProbeResult:
    scenario: str
    expected_skill_id: str | None
    selected_skill_id: str | None
    selected_skill_ids: list[str]
    result: str
    reason: str | None
    transcript: str
    events: str
    payload: dict[str, Any] | None = None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the official external-skill dense catalog against a checked-in "
            "live skill profile and record skill.selected saturation evidence."
        )
    )
    parser.add_argument(
        "--target",
        default="minimax-m2-7",
        help="Skill dense live target id (default: minimax-m2-7)",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=120,
        help="Per-scenario max polling time (default: 120)",
    )
    parser.add_argument(
        "--include-missing-negative",
        action="store_true",
        help="Also run the missing-skill negative prompt.",
    )
    parser.add_argument(
        "--scenario",
        action="append",
        help=(
            "Optional scenario skill id filter. Repeatable. When omitted, "
            "run the full official dense catalog."
        ),
    )
    parser.add_argument(
        "--no-magic-phrase",
        action="store_true",
        help=(
            "SSOC-05: use the no-magic-phrase prompt variant that does NOT "
            "match the direct-named fast-path regex. Forces the LLM-select "
            "path to do skill identification, restoring measurement of "
            "LLM-select quality on the same official-catalog skill set."
        ),
    )
    return parser.parse_args()


def _resolve_target(target_id: str) -> SkillLiveTarget:
    for target in official_skill_dense_targets():
        if target.target_id == target_id:
            return target
    valid = ", ".join(target.target_id for target in official_skill_dense_targets())
    raise SystemExit(f"unknown target {target_id!r}; expected one of: {valid}")


def _artifact_root() -> Path:
    root = resolve_generated_root(
        home_root=OPENMINION_DIR
    ) / dense_skill_artifact_dirname("official")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _brain_db_path(*, data_root: Path) -> Path:
    return matrix.resolve_brain_sessions_db_path(
        storage_path=data_root / "state" / "openminion.db"
    )


def _query_session_events(
    *, data_root: Path, conversation_session_id: str
) -> list[dict[str, Any]]:
    db_path = _brain_db_path(data_root=data_root)
    if not db_path.exists():
        return []
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            """
            select event_type, payload_json
            from session_events
            where session_id = ?
            order by seq
            """,
            (conversation_session_id,),
        ).fetchall()
    finally:
        conn.close()
    events: list[dict[str, Any]] = []
    for event_type, payload_json in rows:
        payload: dict[str, Any]
        try:
            raw = json.loads(payload_json or "{}")
        except json.JSONDecodeError:
            raw = {}
        payload = raw if isinstance(raw, dict) else {}
        events.append({"type": str(event_type), "payload": payload})
    return events


def _find_conversation_session_id(
    *, data_root: Path, base_session_id: str
) -> str | None:
    db_path = _brain_db_path(data_root=data_root)
    if not db_path.exists():
        return None
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            """
            select session_id
            from sessions
            where session_id like ?
            order by updated_at desc
            limit 1
            """,
            (f"{base_session_id}::conv:%",),
        ).fetchone()
    finally:
        conn.close()
    return str(row[0]) if row else None


def _terminate_chat(proc: subprocess.Popen[str]) -> str:
    try:
        proc.terminate()
    except ProcessLookupError:
        pass
    try:
        stdout, _ = proc.communicate(timeout=10)
        return stdout or ""
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, _ = proc.communicate()
        return stdout or ""


def _probe_positive_scenario(
    *,
    target: SkillLiveTarget,
    data_root: Path,
    trace_root: Path,
    transcript_dir: Path,
    scenario: matrix._DenseSkillScenario,
    expected_skill_id: str,
    timeout_seconds: int,
) -> ProbeResult:
    session_id = (
        f"live-skill-probe-{target.agent_id}-{scenario.skill_id}-{uuid.uuid4().hex[:8]}"
    )
    transcript_path = transcript_dir / f"{session_id}.txt"
    events_path = transcript_dir / f"{session_id}-events.json"
    proc = subprocess.Popen(
        [
            str(matrix.python_bin()),
            "-m",
            "openminion",
            "--config",
            str(target.config_path),
            "--agent",
            target.agent_id,
            "--session",
            session_id,
            "--verbosity",
            "quiet",
            "--progress",
            "off",
        ],
        cwd=str(matrix.openminion_root()),
        env=matrix._run_env(data_root=data_root, trace_root=trace_root / session_id),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert proc.stdin is not None
    proc.stdin.write(f"{scenario.prompt}\n")
    proc.stdin.flush()

    selected_payload: dict[str, Any] | None = None
    selected_skill_id: str | None = None
    selected_skill_ids: list[str] = []
    final_reason: str | None = None
    final_events: list[dict[str, Any]] = []
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        conv_session_id = _find_conversation_session_id(
            data_root=data_root,
            base_session_id=session_id,
        )
        if conv_session_id is None:
            time.sleep(1)
            continue
        final_events = _query_session_events(
            data_root=data_root,
            conversation_session_id=conv_session_id,
        )
        selected_events = [
            event
            for event in final_events
            if str(event.get("type", "")) == "skill.selected"
        ]
        if selected_events:
            payload = dict(selected_events[-1].get("payload", {}))
            skill_ref = (
                dict(payload.get("skill_ref", {}))
                if isinstance(payload.get("skill_ref"), dict)
                else {}
            )
            selected_payload = payload
            selected_skill_id = (
                str(skill_ref.get("id", "") or payload.get("id", "")).strip() or None
            )
            selected_skill_ids = [
                str(item).strip() for item in payload.get("selected_skill_ids", [])
            ]
            final_reason = (
                None
                if selected_skill_id == expected_skill_id
                and selected_skill_ids == [expected_skill_id]
                else "wrong_selected_skill"
            )
            break
        prerouting_events = [
            event
            for event in final_events
            if str(event.get("type", "")) == "skill.prerouting"
        ]
        if prerouting_events:
            prerouting_payload = dict(prerouting_events[-1].get("payload", {}))
            if prerouting_payload.get("selected_skill_count") == 0:
                final_reason = "missing_skill_selected"
                break
        if proc.poll() is not None:
            break
        time.sleep(1)

    transcript = _terminate_chat(proc)
    transcript_path.write_text(transcript, encoding="utf-8")
    events_path.write_text(
        json.dumps(final_events, indent=2, sort_keys=True), encoding="utf-8"
    )

    if selected_skill_id == expected_skill_id and selected_skill_ids == [
        expected_skill_id
    ]:
        return ProbeResult(
            scenario=scenario.skill_id,
            expected_skill_id=expected_skill_id,
            selected_skill_id=selected_skill_id,
            selected_skill_ids=selected_skill_ids,
            result="pass",
            reason=None,
            transcript=str(transcript_path),
            events=str(events_path),
            payload=selected_payload,
        )
    reason = final_reason or "missing_skill_selected"
    return ProbeResult(
        scenario=scenario.skill_id,
        expected_skill_id=expected_skill_id,
        selected_skill_id=selected_skill_id,
        selected_skill_ids=selected_skill_ids,
        result="fail",
        reason=reason,
        transcript=str(transcript_path),
        events=str(events_path),
        payload=selected_payload,
    )


def _probe_missing_negative(
    *,
    target: SkillLiveTarget,
    data_root: Path,
    trace_root: Path,
    transcript_dir: Path,
    timeout_seconds: int,
) -> ProbeResult:
    session_id = f"live-skill-probe-{target.agent_id}-missing-{uuid.uuid4().hex[:8]}"
    transcript_path = transcript_dir / f"{session_id}.txt"
    events_path = transcript_dir / f"{session_id}-events.json"
    proc = subprocess.Popen(
        [
            str(matrix.python_bin()),
            "-m",
            "openminion",
            "--config",
            str(target.config_path),
            "--agent",
            target.agent_id,
            "--session",
            session_id,
            "--verbosity",
            "quiet",
            "--progress",
            "off",
        ],
        cwd=str(matrix.openminion_root()),
        env=matrix._run_env(data_root=data_root, trace_root=trace_root / session_id),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert proc.stdin is not None
    proc.stdin.write(f"{matrix._MISSING_SKILL_PROMPT}\n")
    proc.stdin.flush()

    final_events: list[dict[str, Any]] = []
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        conv_session_id = _find_conversation_session_id(
            data_root=data_root, base_session_id=session_id
        )
        if conv_session_id is None:
            time.sleep(1)
            continue
        final_events = _query_session_events(
            data_root=data_root, conversation_session_id=conv_session_id
        )
        selected_events = [
            e for e in final_events if str(e.get("type", "")) == "skill.selected"
        ]
        prerouting_events = [
            e for e in final_events if str(e.get("type", "")) == "skill.prerouting"
        ]
        if selected_events:
            break
        if prerouting_events:
            payload = dict(prerouting_events[-1].get("payload", {}))
            if payload.get("selected_skill_count") == 0:
                break
        if proc.poll() is not None:
            break
        time.sleep(1)

    transcript = _terminate_chat(proc)
    transcript_path.write_text(transcript, encoding="utf-8")
    events_path.write_text(
        json.dumps(final_events, indent=2, sort_keys=True), encoding="utf-8"
    )
    selected_events = [
        e for e in final_events if str(e.get("type", "")) == "skill.selected"
    ]
    prerouting_events = [
        e for e in final_events if str(e.get("type", "")) == "skill.prerouting"
    ]
    if not selected_events and prerouting_events:
        payload = dict(prerouting_events[-1].get("payload", {}))
        if payload.get("selected_skill_count") == 0:
            return ProbeResult(
                scenario="missing_skill",
                expected_skill_id=None,
                selected_skill_id=None,
                selected_skill_ids=[],
                result="pass",
                reason=None,
                transcript=str(transcript_path),
                events=str(events_path),
                payload=payload,
            )
    return ProbeResult(
        scenario="missing_skill",
        expected_skill_id=None,
        selected_skill_id=None,
        selected_skill_ids=[],
        result="fail",
        reason="unexpected_missing_skill_behavior",
        transcript=str(transcript_path),
        events=str(events_path),
        payload=None,
    )


def main() -> int:
    args = _parse_args()
    target = _resolve_target(args.target)
    matrix.validate_skill_live_target(target)
    scenarios = (
        matrix._official_dense_scenarios_no_magic_phrase()
        if args.no_magic_phrase
        else matrix._official_dense_scenarios()
    )
    requested_scenarios = {
        str(skill_id).strip()
        for skill_id in (args.scenario or [])
        if str(skill_id).strip()
    }
    if requested_scenarios:
        scenarios = tuple(
            scenario
            for scenario in scenarios
            if scenario.skill_id in requested_scenarios
        )
        missing = sorted(
            requested_scenarios.difference(
                {scenario.skill_id for scenario in scenarios}
            )
        )
        if missing:
            raise SystemExit("unknown scenario skill ids: " + ", ".join(missing))

    artifact_root = _artifact_root()
    run_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    # encode the prompt mode in the run_id so artifacts from the
    # magic-phrase and no-magic-phrase variants are distinguishable.
    mode_marker = "no-magic-phrase" if args.no_magic_phrase else "magic-phrase"
    run_id = f"{target.target_id}-{mode_marker}-{run_stamp}-{uuid.uuid4().hex[:6]}"
    data_root = artifact_root / "data-roots" / run_id
    trace_root = artifact_root / "traces"
    transcript_dir = artifact_root / "transcripts"
    summary_path = artifact_root / f"official-skill-dense-probe-{run_id}.json"
    for path in (data_root, trace_root, transcript_dir):
        path.mkdir(parents=True, exist_ok=True)

    ingested_ids: dict[str, str] = {}
    results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    for scenario in scenarios:
        payload = matrix._run_skill_ingest(
            target=target,
            data_root=data_root,
            fixture_path=scenario.fixture_path,
            transcript_dir=transcript_dir,
        )
        warnings = [str(item).strip() for item in payload.get("warnings", [])]
        if any(item.startswith("lint.error:") for item in warnings):
            raise RuntimeError(
                f"lint.error during skill ingest for {scenario.skill_id}: {warnings}"
            )
        ingested_ids[scenario.skill_id] = str(payload.get("skill_id", "")).strip()

    for scenario in scenarios:
        result = _probe_positive_scenario(
            target=target,
            data_root=data_root,
            trace_root=trace_root,
            transcript_dir=transcript_dir,
            scenario=scenario,
            expected_skill_id=ingested_ids[scenario.skill_id],
            timeout_seconds=args.timeout_seconds,
        )
        row = {
            "scenario": result.scenario,
            "expected_skill_id": result.expected_skill_id,
            "selected_skill_id": result.selected_skill_id,
            "selected_skill_ids": result.selected_skill_ids,
            "result": result.result,
            "reason": result.reason,
            "transcript": result.transcript,
            "events": result.events,
        }
        if result.payload is not None:
            row["payload"] = result.payload
        results.append(row)
        if result.result != "pass":
            failures.append(row)
        print(
            f"{scenario.skill_id}: {result.result}{'' if result.reason is None else ' (' + result.reason + ')'}"
        )
        sys.stdout.flush()

    if args.include_missing_negative:
        negative = _probe_missing_negative(
            target=target,
            data_root=data_root,
            trace_root=trace_root,
            transcript_dir=transcript_dir,
            timeout_seconds=args.timeout_seconds,
        )
        row = {
            "scenario": negative.scenario,
            "expected_skill_id": negative.expected_skill_id,
            "selected_skill_id": negative.selected_skill_id,
            "selected_skill_ids": negative.selected_skill_ids,
            "result": negative.result,
            "reason": negative.reason,
            "transcript": negative.transcript,
            "events": negative.events,
        }
        if negative.payload is not None:
            row["payload"] = negative.payload
        results.append(row)
        if negative.result != "pass":
            failures.append(row)
        print(
            f"missing_skill: {negative.result}{'' if negative.reason is None else ' (' + negative.reason + ')'}"
        )
        sys.stdout.flush()

    summary = {
        "target": target.target_id,
        "config_path": str(target.config_path),
        "agent_id": target.agent_id,
        # prompt_mode distinguishes the magic-phrase baseline from
        # the no-magic-phrase variant so post-hoc analysis can compare both.
        "prompt_mode": "no-magic-phrase" if args.no_magic_phrase else "magic-phrase",
        "failure_count": len(failures),
        "pass_count": sum(
            1 for item in results if str(item.get("result", "")) == "pass"
        ),
        "results": results,
        "failures": failures,
    }
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(f"summary_path={summary_path}")
    print(
        f"pass_count={summary['pass_count']} failure_count={summary['failure_count']}"
    )
    return 0 if not failures else 1
