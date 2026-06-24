from __future__ import annotations

from pathlib import Path
from typing import Any

from openminion.modules.skill.config import SkillConfig, load_config
from openminion.modules.skill.runtime.skill import Skill


_SKILL_TEMPLATE = """
---
name: {name}
id: {skill_id}
status: draft
tags: [{tag}]
tools: [tool.shell]
risk: low
applies_to:
  intents: [{intent_phrase}]
---

## Summary
{summary}

## When To Use
{when_to_use}

## Procedure
- tool.shell run "echo {skill_id}"
""".strip()


def _build_skill_markdown(
    *,
    skill_id: str,
    name: str,
    summary: str,
    when_to_use: str,
    tag: str = "generic",
    intent_phrase: str | None = None,
) -> str:
    return _SKILL_TEMPLATE.format(
        skill_id=skill_id,
        name=name,
        summary=summary,
        when_to_use=when_to_use,
        tag=tag,
        intent_phrase=intent_phrase or f"use {skill_id}",
    )


def _cfg(tmp_path: Path, **overrides: Any) -> SkillConfig:
    base = {
        "skill": {
            "sqlite_path": str(tmp_path / "skill.db"),
            "wal": False,
            "default_status_filter": ["draft", "verified", "blessed"],
            "high_risk_status_filter": ["blessed", "verified"],
            "known_tools": ["tool.shell"],
        }
    }
    cfg = load_config(base)
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


def _ingest_many(
    ctl: Skill,
    *,
    count: int,
    distinguished_id: str | None = None,
    distinguished_query_terms: str = "",
) -> None:
    for i in range(count):
        skill_id = f"generic_skill_{i:02d}"
        ctl.ingest_text(
            name=f"Generic Skill {i:02d}",
            markdown=_build_skill_markdown(
                skill_id=skill_id,
                name=f"Generic Skill {i:02d}",
                summary=f"Generic catalog placeholder number {i}",
                when_to_use=f"When you need generic placeholder behavior number {i}",
            ),
        )
    if distinguished_id is not None:
        ctl.ingest_text(
            name="Distinguished Linear Sync",
            markdown=_build_skill_markdown(
                skill_id=distinguished_id,
                name="Distinguished Linear Sync",
                summary=(
                    f"Synchronise {distinguished_query_terms} across projects "
                    "with deterministic ordering."
                ),
                when_to_use=(
                    f"When the user asks to sync {distinguished_query_terms}."
                ),
                tag="linear",
                intent_phrase=f"sync {distinguished_query_terms}",
            ),
        )


def test_skill_match_uses_narrower_when_catalog_exceeds_threshold(
    tmp_path: Path,
) -> None:
    cfg = _cfg(tmp_path, selection_rag_threshold=10, selection_rag_topk=5)
    ctl = Skill(cfg)
    try:
        _ingest_many(
            ctl,
            count=14,
            distinguished_id="linear_distinguished",
            distinguished_query_terms="linear issues",
        )

        matches = ctl.match(
            intent_text="sync linear issues across projects",
            step_hint={"risk": "low"},
            agent_id="agent.any",
            k=3,
        )

        assert matches, "Expected at least one match when narrowing fires"
        assert "linear_distinguished" in {m.skill_id for m in matches}
    finally:
        ctl.close()


def test_skill_match_skips_narrower_when_catalog_under_threshold(
    tmp_path: Path,
) -> None:
    captured: list[tuple[str, dict[str, Any]]] = []

    def _capture_emit(
        self: Skill,
        *,
        operation: str,
        status: str = "ok",
        extra: dict[str, Any] | None = None,
    ) -> None:  # noqa: ARG001
        captured.append((operation, dict(extra or {})))

    cfg = _cfg(tmp_path, selection_rag_threshold=10, selection_rag_topk=5)
    ctl = Skill(cfg)
    # Stamp a session/turn so the operation emitter doesn't short-circuit;
    # then swap the emitter for our capture.
    ctl.set_telemetry_context(session_id="s-test", turn_id="t-test")
    ctl._emit_skill_operation = _capture_emit.__get__(ctl, Skill)  # type: ignore[method-assign]
    try:
        _ingest_many(
            ctl,
            count=5,
            distinguished_id="linear_below_threshold",
            distinguished_query_terms="linear issues",
        )

        matches = ctl.match(
            intent_text="sync linear issues",
            step_hint={"risk": "low"},
            agent_id="agent.any",
            k=3,
        )

        # Behavior identical to pre-SLV2-01: matches return, and the shortlist
        # telemetry payload does NOT carry narrow_threshold / narrowed flags.
        assert matches is not None
        shortlist_events = [extra for op, extra in captured if op == "shortlist"]
        assert shortlist_events, "Expected shortlist telemetry to fire"
        for extra in shortlist_events:
            assert "narrowed" not in extra
            assert "narrow_threshold" not in extra
            assert "pre_narrow_count" not in extra
    finally:
        ctl.close()


def test_skill_match_narrowing_preserves_score_match_ordering(
    tmp_path: Path,
) -> None:
    cfg = _cfg(tmp_path, selection_rag_threshold=10, selection_rag_topk=5)
    ctl = Skill(cfg)
    try:
        _ingest_many(
            ctl,
            count=12,
            distinguished_id="text_match_only",
            distinguished_query_terms="ferret payload",
        )
        # Second distinguished skill: weaker text overlap but matches the
        # tool_id hint that _score_match boosts hard.
        ctl.ingest_text(
            name="Ferret SSH Helper",
            markdown=_build_skill_markdown(
                skill_id="tool_matched",
                name="Ferret SSH Helper",
                summary="ferret",
                when_to_use="when ferret payload work is needed",
                intent_phrase="ferret payload",
            ).replace("tools: [tool.shell]", "tools: [tool.ssh]"),
        )

        matches = ctl.match(
            intent_text="ferret payload work",
            step_hint={"risk": "low", "tool_id": "tool.ssh"},
            agent_id="agent.any",
            k=5,
        )

        ids = [m.skill_id for m in matches]
        assert "tool_matched" in ids, "Tool-matched survivor must reach _score_match"
        # _score_match's tool_id boost (+7.0) is larger than any text-only
        # signal — so the tool-matched skill must rank first among survivors.
        assert ids[0] == "tool_matched"
    finally:
        ctl.close()
