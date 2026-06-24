from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from tests.helpers.live_cli_chat_alibaba import (
    artifact_dir,
    extract_assistant_messages,
    framework_root,
    require_live_flag,
    run_cli_session,
)
from tests.helpers.live_e2e_profiles import resolve_live_config_path

pytestmark = pytest.mark.e2e


_OFFICIAL_CONFIG = resolve_live_config_path(
    "per-agent-minimax-official.json",
    framework_root(),
)


def _record_content_text(raw_content: str) -> str:
    try:
        parsed = json.loads(raw_content)
    except json.JSONDecodeError:
        return raw_content
    if isinstance(parsed, str):
        return parsed
    if isinstance(parsed, dict):
        for key in ("text", "value", "summary", "content"):
            value = parsed.get(key)
            if value:
                return str(value)
    return str(parsed)


def _email_rows(memory_db: Path, *, agent_id: str) -> list[dict[str, object]]:
    with sqlite3.connect(str(memory_db)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, scope, type, key, title, content_json, is_deleted,
                   superseded_by_id
            FROM memory_records
            WHERE scope = ?
              AND type = 'fact'
              AND key = 'fact:user_email'
            ORDER BY updated_at ASC
            """,
            (f"agent:{agent_id}",),
        ).fetchall()
    return [dict(row) for row in rows]


@pytest.mark.e2e
@pytest.mark.timeout(180)
def test_live_minimax_official_conversational_fact_supersession() -> None:
    require_live_flag()
    if not _OFFICIAL_CONFIG.exists():
        pytest.skip(f"missing config file: {_OFFICIAL_CONFIG}")

    agent_id = "minimax-m2-7"
    run_id = f"omcfs-email-{int(time.time())}"
    data_root = artifact_dir() / "data-roots" / run_id
    old_email = f"old-{run_id}@example.com"
    new_email = f"new-{run_id}@example.com"

    run_cli_session(
        session_id_prefix=f"{run_id}-s1",
        user_input=(f"remember: my work email is {old_email}\n/exit\n"),
        agent_id=agent_id,
        config_path=_OFFICIAL_CONFIG,
        data_root_override=data_root,
    )

    run_cli_session(
        session_id_prefix=f"{run_id}-s2",
        user_input=(
            f"Correction: my work email is {new_email}. Remember this instead.\n/exit\n"
        ),
        agent_id=agent_id,
        config_path=_OFFICIAL_CONFIG,
        data_root_override=data_root,
    )

    recall = run_cli_session(
        session_id_prefix=f"{run_id}-s3",
        user_input="What is my work email? Answer with only the email.\n/exit\n",
        agent_id=agent_id,
        config_path=_OFFICIAL_CONFIG,
        data_root_override=data_root,
    )

    assistant_messages = extract_assistant_messages(
        transcript=recall.transcript,
        session_id=recall.session_id,
        agent_id=agent_id,
    )
    assistant_text = "\n".join(assistant_messages).strip().lower()
    assert new_email.lower() in assistant_text, (
        "live MiniMax recall did not include the superseding email\n"
        f"transcript={recall.transcript_path}\n"
        f"assistant_text={assistant_text}"
    )
    assert old_email.lower() not in assistant_text, (
        "live MiniMax recall leaked the stale email\n"
        f"transcript={recall.transcript_path}\n"
        f"assistant_text={assistant_text}"
    )

    rows = _email_rows(data_root / "memory" / "memory.db", agent_id=agent_id)
    live_rows = [row for row in rows if int(row["is_deleted"] or 0) == 0]
    superseded_rows = [
        row
        for row in rows
        if int(row["is_deleted"] or 0) == 1
        and str(row.get("superseded_by_id") or "").strip()
    ]

    assert len(live_rows) == 1, (
        "expected exactly one live normalized-key email record\n"
        f"data_root={data_root}\n"
        f"rows={rows!r}"
    )
    live_content = _record_content_text(str(live_rows[0].get("content_json") or ""))
    assert new_email.lower() in live_content.lower()
    assert old_email.lower() not in live_content.lower()

    superseded_content = "\n".join(
        _record_content_text(str(row.get("content_json") or ""))
        for row in superseded_rows
    ).lower()
    assert old_email.lower() in superseded_content, (
        "expected superseded history to preserve the old email\n"
        f"data_root={data_root}\n"
        f"rows={rows!r}"
    )
