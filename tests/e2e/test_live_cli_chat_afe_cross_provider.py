from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.helpers.live_cli_chat_alibaba import (
    artifact_dir,
    framework_root,
    require_live_flag,
    run_cli_session,
)
from tests.helpers.live_e2e_profiles import resolve_live_config_path

pytestmark = pytest.mark.e2e


_PROBE_MESSAGE = (
    "My name is Jay, I prefer TypeScript, please deploy the auth service by Friday."
)

_CATEGORY_NAME = "name"
_CATEGORY_PREFERENCE = "preference"
_CATEGORY_TASK = "task"

_CATEGORY_SIGNATURES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    # category -> (allowed record_types, content keyword substrings)
    _CATEGORY_NAME: (("fact",), ("jay",)),
    _CATEGORY_PREFERENCE: (("user_preference", "fact"), ("typescript",)),
    _CATEGORY_TASK: (("task", "fact"), ("auth", "deploy")),
}


@dataclass(frozen=True)
class _ProviderProbe:
    label: str
    config_basename: str
    agent_id: str


_PROVIDERS: tuple[_ProviderProbe, ...] = (
    _ProviderProbe(
        label="minimax-m2-7",
        config_basename="per-agent-minimax-official.json",
        agent_id="minimax-m2-7",
    ),
    _ProviderProbe(
        label="claude-haiku-4-5",
        config_basename="per-agent-openrouter-claude-haiku-4-5.json",
        agent_id="hello-agent",
    ),
    _ProviderProbe(
        label="gpt-5-4",
        config_basename="per-agent-openrouter-gpt-5-4.json",
        agent_id="hello-agent",
    ),
)


def _coerce_content_text(raw_content: str) -> str:
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


def _auto_extracted_candidates(
    memory_db: Path, *, cli_session_id: str
) -> list[dict[str, object]]:
    if not memory_db.exists():
        return []
    with sqlite3.connect(str(memory_db)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT candidate_id, session_id, proposed_scope, type, key,
                   title, content_json, tags_json, meta_json, status,
                   created_at
            FROM memory_candidates
            WHERE meta_json LIKE ?
              AND meta_json LIKE ?
            ORDER BY created_at ASC
            """,
            (
                '%"source": "auto_extracted"%',
                f'%"source_session_id": "{cli_session_id}%',
            ),
        ).fetchall()
    return [dict(row) for row in rows]


def _categorize(candidate: dict[str, object]) -> set[str]:
    record_type = str(candidate.get("type") or "").strip().lower()
    title = str(candidate.get("title") or "").lower()
    content_text = _coerce_content_text(
        str(candidate.get("content_json") or "")
    ).lower()
    haystack = f"{title}\n{content_text}"
    covered: set[str] = set()
    for category, (allowed_types, keywords) in _CATEGORY_SIGNATURES.items():
        if record_type not in allowed_types:
            continue
        if any(keyword in haystack for keyword in keywords):
            covered.add(category)
    return covered


@pytest.mark.e2e
@pytest.mark.parametrize(
    "probe",
    _PROVIDERS,
    ids=[probe.label for probe in _PROVIDERS],
)
def test_live_afe_cross_provider_extraction(probe: _ProviderProbe) -> None:
    require_live_flag()
    config_path = resolve_live_config_path(probe.config_basename, framework_root())
    if not config_path.exists():
        pytest.skip(f"missing config file: {config_path}")

    run_id = f"afe-cross-{probe.label}-{int(time.time())}"
    data_root = artifact_dir() / "data-roots" / run_id

    result = run_cli_session(
        session_id_prefix=run_id,
        user_input=f"{_PROBE_MESSAGE}\n/exit\n",
        agent_id=probe.agent_id,
        config_path=config_path,
        data_root_override=data_root,
    )

    memory_db = data_root / "memory" / "memory.db"
    deadline = time.time() + 5.0
    candidates: list[dict[str, object]] = []
    while time.time() < deadline:
        candidates = _auto_extracted_candidates(
            memory_db, cli_session_id=result.session_id
        )
        if candidates:
            break
        time.sleep(0.25)

    assert candidates, (
        f"provider={probe.label} staged no auto_extracted candidates\n"
        f"transcript={result.transcript_path}\n"
        f"memory_db={memory_db}"
    )

    covered: set[str] = set()
    for candidate in candidates:
        covered |= _categorize(candidate)

    assert len(covered) >= 2, (
        f"provider={probe.label} covered fewer than 2 expected categories\n"
        f"covered={sorted(covered)}\n"
        f"expected_any_of={sorted(_CATEGORY_SIGNATURES)}\n"
        f"candidate_count={len(candidates)}\n"
        f"candidates="
        + json.dumps(
            [
                {
                    "type": c.get("type"),
                    "title": c.get("title"),
                    "key": c.get("key"),
                    "content": _coerce_content_text(str(c.get("content_json") or "")),
                }
                for c in candidates
            ],
            indent=2,
        )
        + "\n"
        f"transcript={result.transcript_path}\n"
        f"memory_db={memory_db}"
    )
