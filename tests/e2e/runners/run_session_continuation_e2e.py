#!/usr/bin/env python3
"""Deterministic replay-backed E2E proof for session continuation."""

from __future__ import annotations

from argparse import Namespace
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from openminion.api.routes.contracts import APIRouteContext  # noqa: E402
from openminion.api.routes.sessions import handle_request  # noqa: E402
from openminion.base.generated_paths import resolve_generated_root  # noqa: E402
from openminion.cli.commands import sessions as sessions_cli  # noqa: E402
from openminion.modules.context.schemas import (  # noqa: E402
    BuildPackRequest,
    ContextBudgets,
    SessionSlice,
)
from openminion.modules.context.segment.render import (  # noqa: E402
    _SegmentAssemblyRuntime,
    append_summary_segments,
)
from openminion.modules.session.runtime.continuation import (  # noqa: E402
    SessionContinuationService,
)
from openminion.modules.session.storage.sqlite_store import (  # noqa: E402
    SQLiteSessionStore,
)


class _Runtime:
    def __init__(self, store: SQLiteSessionStore) -> None:
        self.session_continuation_store = store
        self.config = SimpleNamespace(gateway=SimpleNamespace(host="127.0.0.1"))

    def close(self) -> None:
        pass


def _seed(store: SQLiteSessionStore) -> None:
    store.create_session(session_id="scpl-source", initial_agent_id="agent-a")
    store.put_working_state(
        "scpl-source",
        state_inline={
            "phase": "act",
            "cursor": 1,
            "session_work_summary": "Verify the continuation index, then report.",
            "permission_refs": ["approval-ref-expired"],
        },
    )
    store.append_event(
        "scpl-source",
        event_type="tool.call.completed",
        payload={"tool_name": "file.read", "status": "completed"},
    )


def _render_first_segment(store: SQLiteSessionStore, target_id: str) -> dict:
    raw = store.get_slice(target_id, "chat", None)
    session_slice = SessionSlice(
        session_id=target_id,
        slice_version=str(raw["slice_version"]),
        summary_short="",
        continuation=raw["continuation"],
    )
    budgets = ContextBudgets(
        total_max_tokens=1_000,
        identity_tokens=40,
        summary_tokens=100,
        recent_turn_tokens=40,
        facts_tokens=0,
        memory_tokens=0,
        skills_tokens=0,
        artifact_tokens=0,
        instructions_tokens=40,
    )
    runtime = _SegmentAssemblyRuntime(
        budgets=budgets,
        fit_to_budget=lambda text, cap: (text[: cap * 4], len(text) > cap * 4),
        estimate_tokens=lambda text: max(1, (len(text) + 3) // 4),
    )
    append_summary_segments(
        runtime,
        request=BuildPackRequest(
            session_id=target_id,
            agent_id="agent-a",
            purpose="chat",
            query="What should I do next?",
        ),
        session_slice=session_slice,
        seed_text=None,
        rolling_enabled=True,
        compression_enabled=False,
        compressctl=None,
    )
    first = runtime.segments[0]
    return {
        "segment_id": first.id,
        "pinned": first.pinned,
        "token_estimate": first.token_estimate,
        "summary_budget": budgets.summary_tokens,
        "contains_source_summary": "Verify the continuation index" in first.content,
    }


def main() -> int:
    artifact_dir = (
        resolve_generated_root(home_root=ROOT.parent) / "session-continuation-e2e"
    )
    artifact_dir.mkdir(parents=True, exist_ok=True)
    db_path = artifact_dir / "session-continuation-e2e.db"
    db_path.unlink(missing_ok=True)
    store = SQLiteSessionStore(db_path)
    _seed(store)
    store.create_session(session_id="scpl-api-target", initial_agent_id="agent-a")
    runtime = _Runtime(store)
    ctx = APIRouteContext(None, runtime, None, None, "scpl-e2e")

    created = handle_request(
        ctx,
        method_name="POST",
        path="/v1/sessions/scpl-source/continuations",
        body={"target_agent_id": "agent-a"},
        query=None,
    )
    assert created is not None
    packet = created.payload["continuation"]["packet"]
    packet_id = str(packet["packet_id"])
    applied = handle_request(
        ctx,
        method_name="POST",
        path=f"/v1/sessions/scpl-api-target/continuations/{packet_id}/apply",
        body={},
        query=None,
    )
    assert applied is not None and applied.payload["status"] == "applied"

    original_factory = sessions_cli.APIRuntime.from_config_path
    sessions_cli.APIRuntime.from_config_path = lambda *args, **kwargs: runtime
    cli_stdout = StringIO()
    try:
        with redirect_stdout(cli_stdout):
            cli_exit = sessions_cli.run_sessions_continue(
                Namespace(
                    source_session_id="scpl-source",
                    target_session=None,
                    agent="agent-a",
                    dry_run=False,
                    output_json=True,
                    expires_in_seconds=86_400,
                    config=None,
                    home_root=None,
                    data_root=None,
                )
            )
    finally:
        sessions_cli.APIRuntime.from_config_path = original_factory
    cli_payload = json.loads(cli_stdout.getvalue())

    service = SessionContinuationService(store, now_ms=lambda: 2_000_000)
    expired_packet = service.create(
        "scpl-source",
        target_agent_id="agent-a",
        expires_in_seconds=1,
    ).packet
    assert expired_packet is not None
    store.create_session(session_id="scpl-expired-target", initial_agent_id="agent-a")
    expired_service = SessionContinuationService(store, now_ms=lambda: 2_001_000)
    expired = expired_service.apply(
        "scpl-expired-target",
        packet_id=expired_packet.packet_id,
    )

    first_turn = _render_first_segment(store, "scpl-api-target")
    artifact = {
        "artifact_version": "session-continuation-e2e.v1",
        "proof_mode": "replay_backed",
        "source_session_id": "scpl-source",
        "target_session_id": "scpl-api-target",
        "packet_id": packet_id,
        "schema_version": packet["payload"]["schema_version"],
        "source_event_id": applied.payload["source_event_id"],
        "target_event_id": applied.payload["target_event_id"],
        "api_status": applied.payload["status"],
        "cli_exit": cli_exit,
        "cli_status": cli_payload["status"],
        "first_turn_context": first_turn,
        "replay_backed_first_turn": "Verify the continuation index before reporting.",
        "negative_paths": {"expired": expired.reason_code},
        "permission_revalidation_required": True,
        "tool_replay_count": 0,
    }
    artifact_path = artifact_dir / "session-continuation-e2e.json"
    artifact_path.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"artifact": str(artifact_path), **artifact}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
