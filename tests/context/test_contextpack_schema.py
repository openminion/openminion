import json

import pytest

from openminion.modules.context.schemas import ContextPack, RenderMessage


def _raw_context_pack(
    *,
    session_id: str,
    agent_id: str,
    profile_version: str,
    render_version: str,
    slice_version: str,
    messages: list[dict],
    extra_fields: dict | None = None,
) -> dict:
    raw = {
        "session_id": session_id,
        "agent_id": agent_id,
        "purpose": "act",
        "messages": messages,
        "profile_version": profile_version,
        "render_version": render_version,
        "slice_version": slice_version,
        "context_manifest": {
            "identity": {
                "agent_id": agent_id,
                "profile_version": profile_version,
                "render_version": render_version,
            },
            "session": {
                "slice_version": slice_version,
                "turn_ids_included": [],
            },
            "segment_ids": [],
            "included_segment_ids": [],
            "dropped_segment_ids": [],
        },
    }
    if extra_fields:
        raw.update(extra_fields)
    return raw


def test_roundtrip_required_fields() -> None:
    raw = _raw_context_pack(
        session_id="sess-123",
        agent_id="agent-abc",
        profile_version="prof:v1",
        render_version="rend:v2",
        slice_version="slice:v7",
        messages=[{"role": "system", "content": "Stay on policy"}],
        extra_fields={
            "pack_version": "pack:v3",
            "pack_hash": "sha256:dummy",
            "token_budget_report": {
                "total_cap_tokens": 2000,
                "total_used_tokens": 1500,
                "buckets": {},
            },
            "warnings": [],
        },
    )
    pack = ContextPack.model_validate(raw)
    assert pack.profile_version == "prof:v1"
    assert pack.context_manifest.session.slice_version == "slice:v7"
    reserialized = json.loads(pack.model_dump_json())
    for key in (
        "messages",
        "profile_version",
        "render_version",
        "slice_version",
        "pack_version",
        "context_manifest",
        "token_budget_report",
    ):
        assert key in reserialized


def test_missing_required_field_raises() -> None:
    raw = _raw_context_pack(
        session_id="sess-456",
        agent_id="agent-def",
        profile_version="prof:v9",
        render_version="rend:v9",
        slice_version="slice:v9",
        messages=[RenderMessage(role="system", content="Policy").model_dump()],
        extra_fields={
            "budget_report": {
                "total_cap_tokens": 500,
                "total_used_tokens": 100,
                "sections": {},
            }
        },
    )
    with pytest.raises(ValueError):
        ContextPack.model_validate(raw)
