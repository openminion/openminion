#!/usr/bin/env python3.11
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any


DEFAULT_PROMPTS: list[dict[str, str | None]] = [
    {
        "prompt": "create a github issue for the login bug",
        "expected_skill": "github_issue_helper",
    },
    {
        "prompt": "open a bug on github for the checkout failure",
        "expected_skill": "github_issue_helper",
    },
    {
        "prompt": "deploy to staging using our runbook",
        "expected_skill": "deploy_runbook",
    },
    {
        "prompt": "run the staging deployment procedure safely",
        "expected_skill": "deploy_runbook",
    },
    {"prompt": "what time is it?", "expected_skill": None},
    {"prompt": "hello, how are you?", "expected_skill": None},
    {"prompt": "search for latest ai news then summarize it", "expected_skill": None},
    {"prompt": "list the files in the current folder", "expected_skill": None},
    {
        "prompt": "please file an issue on github for the login regression",
        "expected_skill": "github_issue_helper",
    },
    {
        "prompt": "deploy the service to staging and verify the rollout",
        "expected_skill": "deploy_runbook",
    },
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Probe LLM-driven skill selection against a legacy rules baseline."
    )
    parser.add_argument(
        "--root",
        required=True,
        help="Artifact root for generated home/data/log files.",
    )
    parser.add_argument(
        "--config",
        default="../test-configs/per-agent-alibaba-minimax.json",
        help="OpenMinion config path relative to openminion/ workdir.",
    )
    parser.add_argument("--agent", default="alibaba-minimax")
    parser.add_argument("--python", default=".venv/bin/python3.11")
    parser.add_argument("--timeout", type=float, default=240.0)
    parser.add_argument(
        "--session-prefix",
        default="skill-ab",
        help="Prefix used for generated chat sessions.",
    )
    parser.add_argument(
        "--prompt",
        action="append",
        help="Optional prompt override. Repeatable. Uses default prompt set when omitted.",
    )
    parser.add_argument(
        "--output",
        default="summary.json",
        help="Summary file name under the artifact root.",
    )
    parser.add_argument(
        "--skip-ingest",
        action="store_true",
        help="Skip sample skill ingestion to validate no-skill runtime behavior.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[4]
    openminion_root = repo_root / "openminion"
    artifact_root = Path(args.root).resolve()
    home_root = artifact_root / "home"
    data_root = artifact_root / "data"
    skills_root = artifact_root / "skills"

    if artifact_root.exists():
        shutil.rmtree(artifact_root)
    skills_root.mkdir(parents=True, exist_ok=True)
    home_root.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)

    _write_brain_config(home_root / "brain.yaml")
    skill_config = _skill_config_payload(data_root)
    _write_jsonless_yaml(home_root / "skill.yaml", skill_config)
    skill_markdowns = _write_skills(skills_root)

    env = os.environ.copy()
    env["OPENMINION_HOME"] = str(home_root)
    env["OPENMINION_DATA_ROOT"] = str(data_root)
    env["PYTHONPATH"] = "src"

    if not args.skip_ingest:
        for markdown_path in skill_markdowns:
            _run(
                [
                    args.python,
                    "-m",
                    "openminion",
                    "--config",
                    args.config,
                    "skill",
                    "ingest",
                    "--file",
                    str(markdown_path),
                    "--config",
                    str(home_root / "skill.yaml"),
                ],
                cwd=openminion_root,
                env=env,
            )

    prompts = _prompt_set(args.prompt)
    summary_rows: list[dict[str, Any]] = []
    for index, prompt_spec in enumerate(prompts, start=1):
        prompt = str(prompt_spec["prompt"])
        expected_skill = prompt_spec["expected_skill"]
        rules_baseline = _legacy_rules_baseline(
            python_bin=args.python,
            openminion_root=openminion_root,
            home_root=home_root,
            data_root=data_root,
            skill_config_path=home_root / "skill.yaml",
            prompt=prompt,
        )
        session_name = f"{args.session_prefix}-{index:02d}"
        transcript_path = artifact_root / f"{session_name}.txt"
        started = time.perf_counter()
        _run(
            [
                args.python,
                "tests/e2e/runners/run_cli_chat_probe.py",
                "--config",
                args.config,
                "--agent",
                args.agent,
                "--session",
                session_name,
                "--timeout",
                str(args.timeout),
                "--message",
                prompt,
            ],
            cwd=openminion_root,
            env=env,
            stdout_path=transcript_path,
        )
        chat_elapsed_ms = int(round((time.perf_counter() - started) * 1000.0))
        llm_events = _fetch_skill_events(data_root=data_root, session_name=session_name)
        llm_summary = _summarize_llm_events(llm_events)
        summary_rows.append(
            {
                "prompt": prompt,
                "expected_skill": expected_skill,
                "rules_baseline": rules_baseline,
                "llm_runtime": {
                    **llm_summary,
                    "chat_elapsed_ms": chat_elapsed_ms,
                    "transcript_path": str(transcript_path),
                },
            }
        )

    output_path = artifact_root / args.output
    output_path.write_text(
        json.dumps(
            {
                "config": args.config,
                "agent": args.agent,
                "artifact_root": str(artifact_root),
                "prompt_count": len(summary_rows),
                "rows": summary_rows,
            },
            indent=2,
            ensure_ascii=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(output_path)
    return 0


def _prompt_set(raw_prompts: list[str] | None) -> list[dict[str, str | None]]:
    if not raw_prompts:
        return list(DEFAULT_PROMPTS)
    return [{"prompt": prompt, "expected_skill": None} for prompt in raw_prompts]


def _run(
    cmd: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    stdout_path: Path | None = None,
) -> None:
    stdout_handle = None
    try:
        if stdout_path is not None:
            stdout_path.parent.mkdir(parents=True, exist_ok=True)
            stdout_handle = stdout_path.open("w", encoding="utf-8")
        completed = subprocess.run(
            cmd,
            cwd=str(cwd),
            env=env,
            check=False,
            stdout=stdout_handle or subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    finally:
        if stdout_handle is not None:
            stdout_handle.close()
    if completed.returncode != 0:
        detail = ""
        if isinstance(completed.stdout, str) and completed.stdout.strip():
            detail = f"\n{completed.stdout.strip()}"
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(cmd)}{detail}"
        )


def _write_brain_config(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "brain:",
                "  budgets:",
                "    max_ticks_per_user_turn: 8",
                "    max_tool_calls: 8",
                "    max_a2a_calls: 0",
                "    max_total_llm_tokens: 100000",
                "    max_elapsed_ms: 120000",
                "  skill_selection_strategy: llm",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _skill_config_payload(data_root: Path) -> dict[str, Any]:
    skill_root = data_root / "skill"
    return {
        "skill": {
            "sqlite_path": str(skill_root / "skill.db"),
            "blob_root": str(skill_root / "blobs"),
            "fallback_root": str(skill_root / "fallback"),
            "default_status_filter": ["draft", "verified", "blessed"],
            "high_risk_status_filter": ["verified", "blessed"],
            "known_tools": ["tool.http", "tool.exec.run", "tool.file.write"],
        }
    }


def _write_jsonless_yaml(path: Path, payload: dict[str, Any]) -> None:
    skill = payload["skill"]
    path.write_text(
        "\n".join(
            [
                "skill:",
                f"  sqlite_path: {skill['sqlite_path']}",
                f"  blob_root: {skill['blob_root']}",
                f"  fallback_root: {skill['fallback_root']}",
                "  default_status_filter: [draft, verified, blessed]",
                "  high_risk_status_filter: [verified, blessed]",
                "  known_tools: [tool.http, tool.exec.run, tool.file.write]",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _write_skills(skills_root: Path) -> list[Path]:
    github = skills_root / "github-skill.md"
    github.write_text(
        "\n".join(
            [
                "---",
                "name: GitHub Issue Helper",
                "id: github_issue_helper",
                "status: verified",
                "tags: [github, issues]",
                "tools: [tool.http]",
                "risk: low",
                "applies_to:",
                "  intents: [create github issue, open bug]",
                "---",
                "",
                "## Summary",
                "Open and update GitHub issues with the correct fields.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    deploy = skills_root / "deploy-skill.md"
    deploy.write_text(
        "\n".join(
            [
                "---",
                "name: Deploy Runbook",
                "id: deploy_runbook",
                "status: verified",
                "tags: [deploy, staging]",
                "tools: [tool.exec.run]",
                "risk: medium",
                "applies_to:",
                "  intents: [deploy to staging, run deployment]",
                "---",
                "",
                "## Summary",
                "Follow the validated staging deployment runbook safely.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return [github, deploy]


def _legacy_rules_baseline(
    *,
    python_bin: str,
    openminion_root: Path,
    home_root: Path,
    data_root: Path,
    skill_config_path: Path,
    prompt: str,
) -> dict[str, Any]:
    payload = _run_json(
        [
            python_bin,
            "-m",
            "openminion.modules.skill.cli",
            "--config",
            str(skill_config_path),
            "--home-root",
            str(home_root),
            "--data-root",
            str(data_root),
            "match",
            "--intent",
            prompt,
            "--agent-id",
            "probe-agent",
            "--k",
            "3",
        ],
        cwd=openminion_root,
        env={
            **os.environ,
            "PYTHONPATH": "src",
        },
    )
    matches = payload.get("matches", []) if isinstance(payload, dict) else []
    shortlisted = [
        str(match.get("skill_id", "") or "").strip()
        for match in matches
        if isinstance(match, dict) and str(match.get("skill_id", "") or "").strip()
    ]
    selected = matches[0] if matches else None
    return {
        "selected_skill_id": selected["skill_id"] if selected is not None else None,
        "selected_score": selected["score"] if selected is not None else None,
        "selected_reasons": selected["reasons"] if selected is not None else [],
        "shortlisted": shortlisted,
    }


def _run_json(cmd: list[str], *, cwd: Path, env: dict[str, str]) -> dict[str, Any]:
    completed = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(cmd)}\n{completed.stdout.strip()}"
        )
    return json.loads(completed.stdout)


def _fetch_skill_events(*, data_root: Path, session_name: str) -> list[dict[str, Any]]:
    db_path = data_root / "state" / "brain" / "sessions.db"
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        row = cur.execute(
            "select session_id from sessions where session_id like ? order by updated_at desc limit 1",
            (f"{session_name}::conv:%",),
        ).fetchone()
        if row is None:
            return []
        session_id = row[0]
        events = cur.execute(
            "select seq, event_type, payload_json from session_events "
            "where session_id=? and event_type like 'skill.%' order by seq",
            (session_id,),
        ).fetchall()
        return [
            {
                "seq": seq,
                "event_type": event_type,
                "payload": json.loads(payload_json),
                "session_id": session_id,
            }
            for seq, event_type, payload_json in events
        ]
    finally:
        conn.close()


def _summarize_llm_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    prerouting = next(
        (
            event["payload"]
            for event in events
            if event["event_type"] == "skill.prerouting"
        ),
        {},
    )
    selected = next(
        (
            event["payload"]
            for event in events
            if event["event_type"] == "skill.selected"
        ),
        {},
    )
    shortlisted = next(
        (
            event["payload"]
            for event in events
            if event["event_type"] == "skill.shortlisted"
        ),
        {},
    )
    skill_ref = selected.get("skill_ref") if isinstance(selected, dict) else {}
    if not isinstance(skill_ref, dict):
        skill_ref = {}
    return {
        "selected_skill_id": skill_ref.get("id"),
        "needed": prerouting.get("needed"),
        "intent": prerouting.get("intent"),
        "latency_ms": prerouting.get("latency_ms"),
        "token_count": prerouting.get("token_count"),
        "fail_closed_reason": prerouting.get("fail_closed_reason"),
        "shortlisted": shortlisted.get("skill_ids", []),
        "events": events,
    }
