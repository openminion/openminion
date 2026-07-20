#!/usr/bin/env python3.11
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

OPENMINION_ROOT = Path(__file__).resolve().parents[3]
OPENMINION_SRC = OPENMINION_ROOT / "src"
if str(OPENMINION_ROOT) not in sys.path:
    sys.path.insert(0, str(OPENMINION_ROOT))
if str(OPENMINION_SRC) not in sys.path:
    sys.path.insert(0, str(OPENMINION_SRC))

from openminion.base.generated_paths import resolve_generated_root  # noqa: E402
from openminion.modules.session.storage.sqlite_store import (  # noqa: E402
    SQLiteSessionStore,
)
from openminion.modules.brain.paths import resolve_brain_sessions_db_path  # noqa: E402
from openminion_eval.skills import (  # noqa: E402
    build_nl_named_skill_target_report,
    load_nl_named_skill_manifest,
    load_nl_named_skill_prompt_variants,
    load_nl_named_skill_rubric,
    representative_nl_named_skill_target_ids,
    render_nl_named_skill_prompt,
    write_nl_named_skill_report,
)
from tests.helpers.live_cli_chat_alibaba import (  # noqa: E402
    extract_assistant_messages,
    extract_last_debug_payload,
    framework_root,
    openminion_root,
    python_bin,
    timeout_seconds,
)
from tests.helpers.live_skill_targets import (  # noqa: E402
    SkillLiveTarget,
    official_skill_dense_targets,
    representative_skill_dense_targets,
    validate_skill_live_target,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate NL named-skill quality baseline reports."
    )
    parser.add_argument(
        "--target-set",
        choices=("official", "representative"),
        default="representative",
        help="Which named-skill target inventory to run.",
    )
    parser.add_argument(
        "--output-root",
        help="Optional output directory. Defaults under .openminion/runtime/.",
    )
    parser.add_argument(
        "--target-id",
        action="append",
        help="Optional target id filter. Repeatable.",
    )
    parser.add_argument(
        "--rerun-existing",
        action="store_true",
        help="Recompute reports even when the target report JSON already exists.",
    )
    return parser.parse_args()


def _selected_targets(target_set: str) -> tuple[SkillLiveTarget, ...]:
    if target_set == "official":
        return official_skill_dense_targets()
    if target_set == "representative":
        return representative_skill_dense_targets()
    raise ValueError(f"unsupported target set: {target_set!r}")


def _default_output_root(target_set: str) -> Path:
    return resolve_generated_root(home_root=OPENMINION_ROOT.parent) / (
        f"nl-named-skill-{target_set}-baseline"
    )


def _runner_env(*, data_root: Path, trace_root: Path | None = None) -> dict[str, str]:
    env = dict(os.environ)
    for key in (
        "OPENMINION_CONFIG",
        "OPENMINION_DATA_ROOT",
        "OPENMINION_IDENTITY_DB",
        "OPENMINION_IDENTITY_ROOT",
        "OPENMINION_TRACE_REQUESTS_DIR",
    ):
        env.pop(key, None)

    env["OPENMINION_HOME"] = str(framework_root())
    env["OPENMINION_DATA_ROOT"] = str(data_root)
    if trace_root is not None:
        env["OPENMINION_TRACE_REQUESTS"] = "1"
        env["OPENMINION_TRACE_REQUESTS_DIR"] = str(trace_root)
    current_pythonpath = str(env.get("PYTHONPATH", "")).strip()
    src_root = str(openminion_root() / "src")
    env["PYTHONPATH"] = (
        src_root
        if not current_pythonpath
        else f"{src_root}{os.pathsep}{current_pythonpath}"
    )
    return env


def _ingest_named_skill_fixture(
    *,
    target: SkillLiveTarget,
    data_root: Path,
    fixture_path: Path,
    transcript_dir: Path,
) -> str:
    transcript_path = transcript_dir / f"ingest-{fixture_path.parent.name}.json"
    completed = subprocess.run(
        [
            str(python_bin()),
            "-m",
            "openminion",
            "skill",
            "ingest",
            "--file",
            str(fixture_path),
            "--scope",
            "agent",
            "--agent-id",
            target.agent_id,
            "--config",
            str(target.config_path),
        ],
        cwd=str(openminion_root()),
        env=_runner_env(data_root=data_root),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout_seconds("skill_dense"),
        check=False,
    )
    transcript = completed.stdout or ""
    transcript_path.write_text(transcript, encoding="utf-8")
    if completed.returncode != 0:
        raise RuntimeError(
            "skill ingest failed for "
            f"agent={target.agent_id} fixture={fixture_path}\n"
            f"transcript={transcript_path}\n{transcript}"
        )
    payload = json.loads(transcript)
    if payload.get("ok") is not True:
        raise RuntimeError(
            f"ingest payload was not ok for agent={target.agent_id}\n"
            f"transcript={transcript_path}\n{transcript}"
        )
    return str(payload.get("skill_id", "") or "").strip()


def _run_named_skill_chat(
    *,
    target: SkillLiveTarget,
    data_root: Path,
    trace_root: Path,
    transcript_dir: Path,
    prompt: str,
    slug: str,
) -> tuple[str, Path, str]:
    session_id = f"nl-named-skill-{target.agent_id}-{slug}-{uuid.uuid4().hex[:8]}"
    transcript_path = transcript_dir / f"{session_id}.txt"
    completed = subprocess.run(
        [
            str(python_bin()),
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
        cwd=str(openminion_root()),
        env=_runner_env(data_root=data_root, trace_root=trace_root / session_id),
        input=f"{prompt}\n/debug\n/exit\n",
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout_seconds("skill_dense"),
        check=False,
    )
    transcript = completed.stdout or ""
    transcript_path.write_text(transcript, encoding="utf-8")
    if completed.returncode != 0:
        raise RuntimeError(
            f"cli chat failed for session={session_id} exit={completed.returncode}\n"
            f"transcript={transcript_path}\n{transcript}"
        )
    return session_id, transcript_path, transcript


def _event_session_id(*, session_id: str, transcript: str) -> str:
    debug_payload = extract_last_debug_payload(transcript)
    last_turn = debug_payload.get("last_turn")
    if not isinstance(last_turn, dict):
        raise ValueError("missing /debug last_turn payload")
    metadata = (
        dict(last_turn.get("metadata", {}))
        if isinstance(last_turn.get("metadata"), dict)
        else {}
    )
    conversation_id = str(metadata.get("conversation_id", "")).strip()
    return f"{session_id}::conv:{conversation_id}" if conversation_id else session_id


def _load_session_events(*, data_root: Path, event_session_id: str) -> list[dict]:
    brain_store_path = resolve_brain_sessions_db_path(
        storage_path=data_root / "state" / "openminion.db"
    )
    store = SQLiteSessionStore(brain_store_path)
    try:
        return store.list_events(event_session_id, limit=400)
    finally:
        store.close()


def main() -> int:
    args = parse_args()
    manifest_version, scenarios = load_nl_named_skill_manifest()
    prompt_variant_version, prompt_variants = load_nl_named_skill_prompt_variants()
    rubric_version, rubric_dimensions = load_nl_named_skill_rubric()
    targets = _selected_targets(args.target_set)
    if args.target_set == "representative":
        expected = set(representative_nl_named_skill_target_ids())
        actual = {target.target_id for target in targets}
        if actual != expected:
            raise ValueError(
                f"representative target mismatch: expected {sorted(expected)} got {sorted(actual)}"
            )

    output_root = (
        Path(args.output_root).expanduser().resolve()
        if args.output_root
        else _default_output_root(args.target_set)
    )
    output_root.mkdir(parents=True, exist_ok=True)

    requested_target_ids = {
        str(target_id).strip()
        for target_id in (args.target_id or [])
        if str(target_id).strip()
    }
    if requested_target_ids:
        targets = tuple(
            target for target in targets if target.target_id in requested_target_ids
        )
        if not targets:
            raise ValueError(
                f"no NNSE targets matched requested target ids: {sorted(requested_target_ids)}"
            )

    summary_targets: list[dict[str, object]] = []
    for target in targets:
        validate_skill_live_target(target)
        report_path = output_root / f"{target.target_id}.json"
        if report_path.exists() and not args.rerun_existing:
            existing = json.loads(report_path.read_text(encoding="utf-8"))
            summary_targets.append(
                {
                    "target_id": str(existing.get("target_id", "") or target.target_id),
                    "agent_id": str(existing.get("agent_id", "") or target.agent_id),
                    "report_path": str(report_path),
                    "attempt_count": int(
                        existing.get("summary", {}).get("attempt_count", 0)
                    ),
                    "selection_accuracy_count": int(
                        existing.get("summary", {}).get("selection_accuracy_count", 0)
                    ),
                    "selection_confidence_count": int(
                        existing.get("summary", {}).get("selection_confidence_count", 0)
                    ),
                    "empty_fallback_count": int(
                        existing.get("summary", {}).get("empty_fallback_count", 0)
                    ),
                    "wrong_skill_count": int(
                        existing.get("summary", {}).get("wrong_skill_count", 0)
                    ),
                }
            )
            continue

        run_id = f"{target.target_id}-{uuid.uuid4().hex[:6]}"
        data_root = output_root / "data-roots" / run_id
        trace_root = output_root / "traces"
        transcript_dir = output_root / "transcripts"
        data_root.mkdir(parents=True, exist_ok=True)
        trace_root.mkdir(parents=True, exist_ok=True)
        transcript_dir.mkdir(parents=True, exist_ok=True)

        for scenario in scenarios:
            _ingest_named_skill_fixture(
                target=target,
                data_root=data_root,
                fixture_path=scenario.fixture_path,
                transcript_dir=transcript_dir,
            )

        attempts: list[dict[str, object]] = []
        for scenario in scenarios:
            for variant in prompt_variants:
                prompt = render_nl_named_skill_prompt(
                    scenario=scenario,
                    variant=variant,
                )
                session_id, transcript_path, transcript = _run_named_skill_chat(
                    target=target,
                    data_root=data_root,
                    trace_root=trace_root,
                    transcript_dir=transcript_dir,
                    prompt=prompt,
                    slug=f"{scenario.skill_id}-{variant.variant_id}",
                )
                event_session_id = _event_session_id(
                    session_id=session_id,
                    transcript=transcript,
                )
                events = _load_session_events(
                    data_root=data_root,
                    event_session_id=event_session_id,
                )
                events_path = transcript_dir / f"{session_id}-events.json"
                events_path.write_text(
                    json.dumps(events, indent=2, sort_keys=True),
                    encoding="utf-8",
                )

                selected_events = [
                    event
                    for event in events
                    if str(event.get("type", "")) == "skill.selected"
                ]
                payload = (
                    dict(selected_events[-1].get("payload", {}))
                    if selected_events
                    else {}
                )
                skill_ref = (
                    dict(payload.get("skill_ref", {}))
                    if isinstance(payload.get("skill_ref"), dict)
                    else {}
                )
                selected_skill_id = str(
                    skill_ref.get("id", "") or payload.get("id", "")
                ).strip()
                selected_skill_ids = [
                    str(item).strip()
                    for item in payload.get("selected_skill_ids", [])
                    if str(item).strip()
                ]
                assistant_output = "\n\n".join(
                    extract_assistant_messages(
                        transcript=transcript,
                        session_id=session_id,
                        agent_id=target.agent_id,
                    )
                )
                attempts.append(
                    {
                        "scenario_id": scenario.scenario_id,
                        "prompt_variant_id": variant.variant_id,
                        "prompt": prompt,
                        "session_id": session_id,
                        "transcript": str(transcript_path),
                        "events": str(events_path),
                        "assistant_preview": assistant_output,
                        "skill_selected_event": bool(selected_events),
                        "selected_skill_id": selected_skill_id,
                        "selected_skill_ids": selected_skill_ids,
                    }
                )

        report = build_nl_named_skill_target_report(
            {
                "target_id": target.target_id,
                "agent_id": target.agent_id,
                "config_path": str(target.config_path),
                "attempts": attempts,
            },
            manifest_version=manifest_version,
            scenarios=scenarios,
            prompt_variant_version=prompt_variant_version,
            prompt_variants=prompt_variants,
            rubric_version=rubric_version,
            rubric_dimensions=rubric_dimensions,
        )
        report_path = write_nl_named_skill_report(report_path, report)
        summary_targets.append(
            {
                "target_id": report.target_id,
                "agent_id": report.agent_id,
                "report_path": str(report_path),
                "attempt_count": report.summary["attempt_count"],
                "selection_accuracy_count": report.summary["selection_accuracy_count"],
                "selection_confidence_count": report.summary[
                    "selection_confidence_count"
                ],
                "empty_fallback_count": report.summary["empty_fallback_count"],
                "wrong_skill_count": report.summary["wrong_skill_count"],
            }
        )

    summary_path = output_root / "summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "manifest_version": manifest_version,
                "rubric_version": rubric_version,
                "prompt_variant_version": prompt_variant_version,
                "target_set": args.target_set,
                "target_count": len(summary_targets),
                "targets": summary_targets,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"[ok] wrote {summary_path}")
    return 0
