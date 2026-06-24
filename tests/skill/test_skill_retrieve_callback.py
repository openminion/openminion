from __future__ import annotations

import io
import json
import logging
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from openminion.cli.commands import skill as skill_cli
from openminion.modules.retrieve.runtime.retrieve import RetrieveCtl
from openminion.modules.skill.runtime.skill import Skill


class _FakeSkill:
    def __init__(self, config: str, *, event_callback=None) -> None:
        self._event_callback = event_callback

    def ingest_file(
        self,
        *,
        path: str,
        name: str | None,
        scope: str,
        agent_id: str | None,
        trust: str | None = None,
        promotion_path: str = "operator",
    ) -> tuple[str, str, list[str]]:
        del path, name, agent_id, trust, promotion_path
        if callable(self._event_callback):
            self._event_callback(
                "skill.ingested",
                {
                    "skill_id": "skill-demo",
                    "version_hash": "v123",
                    "source_ref": "blob://skill-demo",
                    "scope": scope,
                },
            )
        return ("skill-demo", "v123", [])

    def get_skill(self, skill_id: str, version: str | None):
        return SimpleNamespace(
            source_artifact_ref="blob://skill-demo",
            name="skill-demo",
            scope="global",
            agent_id=None,
        )

    def ingest_artifact(
        self,
        *,
        source_artifact_ref: str,
        name: str,
        scope: str,
        agent_id: str | None,
        trust: str | None = None,
        promotion_path: str = "operator",
    ) -> tuple[str, str, list[str]]:
        del name, agent_id, trust, promotion_path
        if callable(self._event_callback):
            self._event_callback(
                "skill.ingested",
                {
                    "skill_id": "skill-demo",
                    "version_hash": "v123-refresh",
                    "source_ref": source_artifact_ref,
                    "scope": scope,
                },
            )
        return ("skill-demo", "v123-refresh", [])

    def close(self) -> None:
        return None


def _run_ingest(args: Namespace) -> tuple[int, dict]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = skill_cli._run_skill_ingest(args)
    text = buf.getvalue().strip()
    return code, json.loads(text) if text else {}


def _run_refresh(args: Namespace) -> tuple[int, dict]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = skill_cli._run_skill_refresh(args)
    text = buf.getvalue().strip()
    return code, json.loads(text) if text else {}


def _run_reingest_all(args: Namespace) -> tuple[int, dict]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = skill_cli._run_skill_reingest_all(args)
    text = buf.getvalue().strip()
    return code, json.loads(text) if text else {}


def test_skill_ingest_fires_retrieve_event(monkeypatch) -> None:
    retrieve_ctl = Mock()
    monkeypatch.setattr(skill_cli, "_check_skill_available", lambda: True)
    monkeypatch.setattr(skill_cli, "Skill", _FakeSkill)
    args = Namespace(
        file="tests/fixtures/skill.md",
        name=None,
        scope="global",
        agent_id=None,
        config="skill.json",
        retrieve_ctl=retrieve_ctl,
    )

    code, payload = _run_ingest(args)
    assert code == 0
    assert payload["ok"] is True
    retrieve_ctl.ingest_event.assert_called_once()
    event_type, event_payload = retrieve_ctl.ingest_event.call_args.args
    assert event_type == "skill.ingested"
    for key in ("skill_id", "version_hash", "source_ref", "scope"):
        assert key in event_payload


def test_skill_ingest_no_ctl_is_noop(monkeypatch) -> None:
    monkeypatch.setattr(skill_cli, "_check_skill_available", lambda: True)
    monkeypatch.setattr(skill_cli, "Skill", _FakeSkill)
    args = Namespace(
        file="tests/fixtures/skill.md",
        name=None,
        scope="global",
        agent_id=None,
        config="skill.json",
        retrieve_ctl=None,
    )
    code, payload = _run_ingest(args)
    assert code == 0
    assert payload["ok"] is True


def test_skill_ingest_error_does_not_propagate(monkeypatch) -> None:
    retrieve_ctl = Mock()
    retrieve_ctl.ingest_event.side_effect = RuntimeError("retrieve ingest failed")
    monkeypatch.setattr(skill_cli, "_check_skill_available", lambda: True)
    monkeypatch.setattr(skill_cli, "Skill", _FakeSkill)
    args = Namespace(
        file="tests/fixtures/skill.md",
        name=None,
        scope="global",
        agent_id=None,
        config="skill.json",
        retrieve_ctl=retrieve_ctl,
    )

    code, payload = _run_ingest(args)
    assert code == 0
    assert payload["ok"] is True
    retrieve_ctl.ingest_event.assert_called_once()


def test_skill_refresh_fires_retrieve_event(monkeypatch) -> None:
    retrieve_ctl = Mock()
    monkeypatch.setattr(skill_cli, "_check_skill_available", lambda: True)
    monkeypatch.setattr(skill_cli, "Skill", _FakeSkill)
    args = Namespace(
        skill_id="skill-demo",
        version=None,
        config="skill.json",
        retrieve_ctl=retrieve_ctl,
    )

    code, payload = _run_refresh(args)
    assert code == 0
    assert payload["ok"] is True
    retrieve_ctl.ingest_event.assert_called_once()


def test_skill_ingest_and_retrieve(tmp_path: Path) -> None:
    skill_path = tmp_path / "web-search-skill.md"
    skill_path.write_text(
        (
            "---\n"
            "name: Web Search Skill\n"
            "id: web_search_skill\n"
            "status: verified\n"
            "tools: [web.search]\n"
            "risk: low\n"
            "---\n\n"
            "## Summary\n"
            "Use web search to find recent sources and summarize results.\n\n"
            "## Procedure\n"
            '- web.search query "latest updates"\n'
        ),
        encoding="utf-8",
    )

    retrieve_ctl = RetrieveCtl(
        {
            "version": 1,
            "retrievectl": {
                "storage": {
                    "sqlite_path": str(tmp_path / "retrievectl.db"),
                    "blob_root": str(tmp_path / "retrievectl_blob"),
                    "wal_mode": False,
                },
                "defaults": {
                    "strategy": "contextual",
                    "contextual_enabled": True,
                    "embeddings_enabled": False,
                    "lexical_candidate_count": 25,
                    "snippet_tokens": 120,
                    "chunk_target_tokens": 40,
                    "chunk_min_tokens": 20,
                    "chunk_max_tokens": 60,
                    "doc_group_target_tokens": 80,
                    "doc_group_min_tokens": 40,
                    "doc_group_max_tokens": 120,
                    "raptor_internal_k": 2,
                    "raptor_leaf_k": 4,
                },
            },
        }
    )

    skill_ctl = Skill(
        {
            "skill": {
                "sqlite_path": str(tmp_path / "skill.db"),
                "blob_root": str(tmp_path / "skill_blob"),
                "fallback_root": str(tmp_path / "skill_fallback"),
                "wal": False,
                "known_tools": ["web.search", "web.fetch"],
            }
        },
        event_callback=skill_cli._make_skill_event_callback(
            retrieve_ctl, logging.getLogger("test.skill.retrieve")
        ),
    )

    try:
        skill_id, version_hash, _warnings = skill_ctl.ingest_file(
            path=skill_path, scope="agent", agent_id="agent.demo"
        )
        assert skill_id == "web_search_skill"
        assert version_hash

        rows = retrieve_ctl.retrieve(
            query="web search tool",
            purpose="act",
            scope={"agent": True},
            k=3,
            strategy="contextual",
        )
        assert rows
        assert any(str(item.get("ref_type", "")) == "skill" for item in rows)

        row = retrieve_ctl.store.execute(
            "SELECT COUNT(*) AS count FROM retrievectl_docs WHERE source_type = 'skill'"
        ).fetchone()
        assert row is not None
        assert int(row["count"]) >= 1
        stored = retrieve_ctl.store.execute(
            """
            SELECT d.title AS title, u.fts_text AS fts_text
            FROM retrievectl_docs d
            JOIN retrievectl_units u ON u.doc_id = d.doc_id
            WHERE d.source_type = 'skill'
            ORDER BY d.created_at DESC
            LIMIT 1
            """
        ).fetchone()
        assert stored is not None
        assert stored["title"] == "Web Search Skill"
        assert "title=Web Search Skill" in str(stored["fts_text"])
        assert "web_search_skill" in str(stored["fts_text"])
    finally:
        skill_ctl.close()
        retrieve_ctl.close()


def test_skill_retrieve_matches_hyphenated_skill_ids(tmp_path: Path) -> None:
    skill_path = tmp_path / "claude-api-skill.md"
    skill_path.write_text(
        (
            "---\n"
            "name: Claude API\n"
            "id: claude-api\n"
            "status: verified\n"
            "tools: [web.fetch]\n"
            "risk: low\n"
            "---\n\n"
            "## Summary\n"
            "Use the Claude API skill to plan a small API integration.\n\n"
            "## Procedure\n"
            '- web.fetch url "https://example.com"\n'
        ),
        encoding="utf-8",
    )
    distractor_path = tmp_path / "generic-api-skill.md"
    distractor_path.write_text(
        (
            "---\n"
            "name: API Helper\n"
            "id: api-helper\n"
            "status: verified\n"
            "tools: [web.fetch]\n"
            "risk: low\n"
            "---\n\n"
            "## Summary\n"
            "Use this helper for broad API investigation and general setup.\n\n"
            "## Procedure\n"
            '- web.fetch url "https://example.org"\n'
        ),
        encoding="utf-8",
    )

    retrieve_ctl = RetrieveCtl(
        {
            "version": 1,
            "retrievectl": {
                "storage": {
                    "sqlite_path": str(tmp_path / "retrievectl.db"),
                    "blob_root": str(tmp_path / "retrievectl_blob"),
                    "wal_mode": False,
                },
                "defaults": {
                    "strategy": "contextual",
                    "contextual_enabled": True,
                    "embeddings_enabled": False,
                    "lexical_candidate_count": 25,
                    "snippet_tokens": 120,
                    "chunk_target_tokens": 40,
                    "chunk_min_tokens": 20,
                    "chunk_max_tokens": 60,
                    "doc_group_target_tokens": 80,
                    "doc_group_min_tokens": 40,
                    "doc_group_max_tokens": 120,
                    "raptor_internal_k": 2,
                    "raptor_leaf_k": 4,
                },
            },
        }
    )

    skill_ctl = Skill(
        {
            "skill": {
                "sqlite_path": str(tmp_path / "skill.db"),
                "blob_root": str(tmp_path / "skill_blob"),
                "fallback_root": str(tmp_path / "skill_fallback"),
                "wal": False,
                "known_tools": ["web.fetch"],
            }
        },
        event_callback=skill_cli._make_skill_event_callback(
            retrieve_ctl, logging.getLogger("test.skill.retrieve")
        ),
    )

    try:
        skill_id, _version_hash, _warnings = skill_ctl.ingest_file(
            path=skill_path, scope="agent", agent_id="agent.demo"
        )
        assert skill_id == "claude-api"
        other_skill_id, _other_version_hash, _other_warnings = skill_ctl.ingest_file(
            path=distractor_path, scope="agent", agent_id="agent.demo"
        )
        assert other_skill_id == "api-helper"

        rows = retrieve_ctl.retrieve(
            query="claude-api",
            purpose="plan",
            scope={"agent": True},
            k=5,
            strategy="contextual",
        )

        assert rows
        assert any(str(item.get("ref_type", "")) == "skill" for item in rows)
        assert "Claude API" in str(rows[0].get("text_snippet", ""))
        fts_row = retrieve_ctl.store.execute(
            """
            SELECT title, fts_text
            FROM retrievectl_units_fts
            ORDER BY unit_id ASC
            LIMIT 1
            """
        ).fetchone()
        assert fts_row is not None
        assert str(fts_row["title"]) == "Claude API"
        assert "claude-api" in str(fts_row["fts_text"])
    finally:
        skill_ctl.close()
        retrieve_ctl.close()


def test_skill_reingest_all_backfills_retrieve(tmp_path: Path) -> None:
    skill_cfg = {
        "skill": {
            "sqlite_path": str(tmp_path / "skill.db"),
            "blob_root": str(tmp_path / "skill_blob"),
            "fallback_root": str(tmp_path / "skill_fallback"),
            "wal": False,
            "known_tools": ["web.search", "web.fetch"],
        }
    }
    retrieve_ctl = RetrieveCtl(
        {
            "version": 1,
            "retrievectl": {
                "storage": {
                    "sqlite_path": str(tmp_path / "retrievectl.db"),
                    "blob_root": str(tmp_path / "retrievectl_blob"),
                    "wal_mode": False,
                },
                "defaults": {
                    "strategy": "contextual",
                    "contextual_enabled": True,
                    "embeddings_enabled": False,
                    "lexical_candidate_count": 25,
                    "snippet_tokens": 120,
                    "chunk_target_tokens": 40,
                    "chunk_min_tokens": 20,
                    "chunk_max_tokens": 60,
                    "doc_group_target_tokens": 80,
                    "doc_group_min_tokens": 40,
                    "doc_group_max_tokens": 120,
                    "raptor_internal_k": 2,
                    "raptor_leaf_k": 4,
                },
            },
        }
    )

    try:
        skill_ctl = Skill(skill_cfg)
        try:
            skill_ctl.ingest_text(
                name="Web Search One",
                markdown=(
                    "---\nname: Web Search One\nid: web_search_one\nstatus: verified\n"
                    "tools: [web.search]\nrisk: low\n---\n\n## Summary\nSearch for recent updates.\n"
                ),
                scope="global",
                agent_id=None,
            )
            skill_ctl.ingest_text(
                name="Web Fetch Two",
                markdown=(
                    "---\nname: Web Fetch Two\nid: web_fetch_two\nstatus: verified\n"
                    "tools: [web.fetch]\nrisk: low\n---\n\n## Summary\nFetch and parse web pages.\n"
                ),
                scope="global",
                agent_id=None,
            )
        finally:
            skill_ctl.close()

        before_row = retrieve_ctl.store.execute(
            "SELECT COUNT(*) AS count FROM retrievectl_docs WHERE source_type = 'skill'"
        ).fetchone()
        assert before_row is not None
        assert int(before_row["count"]) == 0

        args = Namespace(
            config=skill_cfg,
            retrieve_ctl=retrieve_ctl,
            app=None,
        )
        code, payload = _run_reingest_all(args)
        assert code == 0
        assert payload.get("ok") is True
        assert int(payload.get("reingested", 0)) == 2

        after_row = retrieve_ctl.store.execute(
            "SELECT COUNT(*) AS count FROM retrievectl_docs WHERE source_type = 'skill'"
        ).fetchone()
        assert after_row is not None
        assert int(after_row["count"]) == 2

        rows = retrieve_ctl.retrieve(
            query="parse web pages",
            purpose="act",
            scope={"global": True},
            k=3,
            strategy="contextual",
        )
        assert rows
        assert any(str(item.get("ref_type", "")) == "skill" for item in rows)
    finally:
        retrieve_ctl.close()
