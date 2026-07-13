from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest


# ── Minimal stub fake-runtime for the shared adapter ─────────────────────────


class _FakeSessionRecord(SimpleNamespace):
    pass


class _FakeSessions:
    def __init__(self) -> None:
        self._records: list[_FakeSessionRecord] = []
        self._by_id: dict[str, _FakeSessionRecord] = {}

    def add(self, **fields) -> _FakeSessionRecord:
        record = _FakeSessionRecord(
            id=fields.get("id", ""),
            session_key=fields.get("session_key", ""),
            channel=fields.get("channel", "cli"),
            target=fields.get("target", "tui"),
            status=fields.get("status", "active"),
            updated_at=fields.get("updated_at", "2026-04-22T00:00:00Z"),
            last_activity_at=fields.get("last_activity_at", ""),
            created_at=fields.get("created_at", "2026-04-22T00:00:00Z"),
            metadata={},
        )
        self._records.append(record)
        self._by_id[record.id] = record
        return record

    def list_sessions(self, *, limit: int = 100):
        del limit
        return list(self._records)

    def list_messages(self, *, session_id: str, limit: int = 3):
        del session_id, limit
        return []

    def resolve_session(
        self, *, agent_id, channel, target, session_id=None, metadata=None
    ):
        del metadata
        if session_id and session_id in self._by_id:
            return self._by_id[session_id]
        existing_key = f"agent:{agent_id}|channel:{channel}|target:{target}"
        for record in self._records:
            if record.session_key == existing_key:
                return record
        new_id = session_id or f"sess-{len(self._records) + 1}"
        record = self.add(
            id=new_id,
            session_key=existing_key,
            channel=channel,
            target=target,
        )
        return record

    def get_session(self, session_id: str):
        return self._by_id.get(session_id)

    def update_session_metadata(self, *, session_id: str, patch):
        record = self._by_id.get(session_id)
        if record is not None:
            record.metadata.update(patch)

    def count_sessions(self) -> int:
        return len(self._records)


class _FakeGateway:
    pass


class _FakeAPIRuntime:
    def __init__(self, *, agent_id: str = "alpha") -> None:
        self.config = SimpleNamespace(
            agent=SimpleNamespace(
                name=agent_id,
                default_channel="cli",
                provider="openai",
            ),
            agents={
                "alpha": SimpleNamespace(name="alpha", default_channel="cli"),
                "beta": SimpleNamespace(name="beta", default_channel="cli"),
            },
            default_agent=agent_id,
            providers=SimpleNamespace(
                openai=SimpleNamespace(model="gpt-4.1-mini"),
                anthropic=SimpleNamespace(model="claude"),
            ),
        )
        self.sessions = _FakeSessions()
        self.tools = SimpleNamespace(list=lambda: {})
        self._agents = {
            "alpha": SimpleNamespace(name="alpha", provider="openai"),
            "beta": SimpleNamespace(name="beta", provider="anthropic"),
        }

    def list_registered_agents(self):
        return list(self._agents.keys())

    def resolve_agent_profile(self, agent_id: str | None = None):
        return self._agents.get(
            str(agent_id or "").strip() or "alpha",
            SimpleNamespace(name=str(agent_id or "alpha"), provider="openai"),
        )

    def resolve_gateway(self, agent_id: str | None = None):
        return _FakeGateway()


# ── TUIDS-01: prompt_on_resume deferred-bind behavior ────────────────────────


def _make_runtime(*, prompt_on_resume: bool, seed_sessions: list[dict] | None = None):
    from openminion.cli.tui.providers.runtime import OpenMinionRuntime

    api = _FakeAPIRuntime(agent_id="alpha")
    for entry in seed_sessions or []:
        api.sessions.add(**entry)
    runtime = OpenMinionRuntime(
        api,
        target="tui",
        channel="cli",
        prompt_on_resume=prompt_on_resume,
    )
    return runtime, api


def test_prompt_on_resume_true_defers_binding_when_candidate_exists() -> None:
    runtime, api = _make_runtime(
        prompt_on_resume=True,
        seed_sessions=[
            {
                "id": "14dfaad65246",
                "session_key": "agent:alpha|channel:cli|target:tui",
                "channel": "cli",
                "target": "tui",
                "updated_at": "2026-04-22T00:00:00Z",
            }
        ],
    )
    assert runtime.prompt_on_resume is True
    assert runtime.is_bound is False
    candidate = runtime.pending_candidate_session
    assert candidate is not None
    assert candidate.id == "14dfaad65246"
    # consume_* returns + clears.
    popped = runtime.consume_pending_candidate_session()
    assert popped is candidate
    assert runtime.pending_candidate_session is None


def test_prompt_on_resume_true_with_no_candidate_leaves_unbound() -> None:
    runtime, api = _make_runtime(prompt_on_resume=True)
    assert runtime.is_bound is False
    assert runtime.pending_candidate_session is None


def test_prompt_on_resume_false_preserves_eager_bind() -> None:
    runtime, api = _make_runtime(prompt_on_resume=False)
    assert runtime.prompt_on_resume is False
    # Eager resolve creates (or finds) a session and binds immediately.
    assert runtime.is_bound is True
    assert runtime.session_id


# ── TUIDS-02: list_sessions(scope=) filter ───────────────────────────────────


def test_list_sessions_scope_current_agent_filters_by_target_and_agent() -> None:
    runtime, api = _make_runtime(
        prompt_on_resume=True,
        seed_sessions=[
            {
                "id": "tui-alpha",
                "session_key": "agent:alpha|channel:cli|target:tui",
                "target": "tui",
                "channel": "cli",
            },
            {
                "id": "tui-beta",
                "session_key": "agent:beta|channel:cli|target:tui",
                "target": "tui",
                "channel": "cli",
            },
            {
                "id": "focus-alpha",
                "session_key": "agent:alpha|channel:cli|target:focus",
                "target": "focus",
                "channel": "cli",
            },
        ],
    )
    # Default scope returns everything.
    assert len(runtime.list_sessions()) == 3
    # Current-agent scope returns only the alpha+cli+tui row.
    filtered = runtime.list_sessions(scope="current_agent")
    assert len(filtered) == 1
    assert filtered[0].id == "tui-alpha"


def test_list_sessions_default_scope_preserves_legacy_contract() -> None:
    runtime, api = _make_runtime(prompt_on_resume=False)
    # Both zero-arg and explicit scope="all" return the same un-filtered shape.
    assert runtime.list_sessions() == runtime.list_sessions(scope="all")


# ── TUIDS-03: session_type classifier ───────────────────────────────────────


@pytest.mark.parametrize(
    "record,expected",
    [
        (
            _FakeSessionRecord(
                id="14dfaad65246",
                session_key="agent:alpha|channel:cli|target:tui",
                target="tui",
                channel="cli",
                updated_at="",
            ),
            "default",
        ),
        (
            _FakeSessionRecord(
                id="sess-abc",
                session_key="agent:alpha|channel:cli|target:tui|session:sess-abc",
                target="tui",
                channel="cli",
                updated_at="",
            ),
            "named",
        ),
        (
            _FakeSessionRecord(
                id="focus-xyz",
                session_key="",
                target="focus",
                channel="cli",
                updated_at="",
            ),
            "focus",
        ),
        (
            _FakeSessionRecord(
                id="room-123",
                session_key="",
                target="room",
                channel="cli",
                updated_at="",
            ),
            "room",
        ),
    ],
)
def test_session_type_classifier_identifies_each_family(record, expected) -> None:
    runtime, _api = _make_runtime(prompt_on_resume=True)
    assert runtime._classify_session_type(record) == expected


def test_session_type_classifier_keys_default_off_session_key_not_id_prefix() -> None:
    runtime, _api = _make_runtime(prompt_on_resume=True)
    # Bare-hex id but session_key points at another agent → "other", NOT default.
    foreign_bare_hex = _FakeSessionRecord(
        id="14dfaad65246",
        session_key="agent:beta|channel:cli|target:tui",
        target="tui",
        channel="cli",
        updated_at="",
    )
    assert runtime._classify_session_type(foreign_bare_hex) == "other"

    # Named prefix + non-matching key → named (explicit prefix wins).
    named = _FakeSessionRecord(
        id="sess-xyz",
        session_key="agent:beta|channel:cli|target:tui",
        target="tui",
        channel="cli",
        updated_at="",
    )
    assert runtime._classify_session_type(named) == "named"

    # Bare hex with NO session_key + unknown prefix → "other" (cannot
    # confirm it's our surface's default).
    unknown = _FakeSessionRecord(
        id="deadbeef1234",
        session_key="",
        target="tui",
        channel="cli",
        updated_at="",
    )
    assert runtime._classify_session_type(unknown) == "other"


# ── TUIDS-04: switch_agent is prompt-aware ───────────────────────────────────


def test_switch_agent_under_prompt_on_resume_refreshes_candidate() -> None:
    runtime, api = _make_runtime(
        prompt_on_resume=True,
        seed_sessions=[
            {
                "id": "tui-beta",
                "session_key": "agent:beta|channel:cli|target:tui",
                "target": "tui",
                "channel": "cli",
            }
        ],
    )
    assert runtime.is_bound is False
    runtime.switch_agent("beta")
    assert runtime.is_bound is False
    candidate = runtime.pending_candidate_session
    assert candidate is not None
    assert candidate.id == "tui-beta"


def test_switch_agent_without_prompt_preserves_eager_bind() -> None:
    runtime, api = _make_runtime(prompt_on_resume=False)
    before_session = runtime.session_id
    runtime.switch_agent("beta")
    # Eager path: a session is resolved for the new agent (may be same or new).
    assert runtime.is_bound is True
    assert runtime.session_id  # non-empty
    # New agent → session key for beta is different from alpha's.
    assert runtime.session_id != before_session or runtime.agent_id == "beta"


# ── Dashboard adoption proofs (TUIDS-05/06/07/08) ─────────────────────────────


def test_commands_tui_constructs_runtime_with_prompt_on_resume() -> None:
    from openminion.cli.commands import tui

    src = inspect.getsource(tui)
    assert "prompt_on_resume=True" in src, (
        "`openminion tui` command must construct OpenMinionRuntime with "
        "prompt_on_resume=True so the dashboard can prompt before resuming."
    )


def test_chat_tab_resolves_pending_session_on_mount() -> None:
    from openminion.cli.tui.tabs.chat import ChatTab

    src = inspect.getsource(ChatTab)
    assert "_resolve_pending_session" in src
    assert (
        "pending_candidate_session" in src or "consume_pending_candidate_session" in src
    )


def test_resume_prompt_gates_slash_commands_too() -> None:
    from openminion.cli.tui.tabs.chat import ChatTab

    submit_src = inspect.getsource(ChatTab.on_chat_input_bar_submitted)
    assert "_resume_prompt_active" in submit_src, (
        "on_chat_input_bar_submitted must check _resume_prompt_active "
        "BEFORE dispatching to _handle_command or _send_message."
    )
    assert "_handle_resume_prompt_reply" in submit_src, (
        "on_chat_input_bar_submitted must call _handle_resume_prompt_reply "
        "so slash commands also resolve the pending prompt."
    )


def test_fresh_session_branches_reset_chat_transcript() -> None:
    from openminion.cli.tui.tabs.chat import ChatTab

    reply_src = inspect.getsource(ChatTab._handle_resume_prompt_reply)
    fresh_src = inspect.getsource(ChatTab._start_fresh_session)
    # Fresh-session reset is centralized in `_start_fresh_session`; both
    # explicit "n" and arbitrary-message fall-through must route through it.
    assert reply_src.count("_start_fresh_session()") >= 2, (
        "Fresh-session branches must route through the shared reset helper "
        "so UI state does not diverge from the newly-bound runtime session."
    )
    assert "set_messages([])" in fresh_src, (
        "Fresh-session branches must reset the visible transcript so UI "
        "state does not diverge from the newly-bound runtime session."
    )


def test_on_mount_suppresses_new_session_banner_when_prompt_active() -> None:
    from openminion.cli.tui.tabs.chat import ChatTab

    mount_src = inspect.getsource(ChatTab.on_mount)
    assert "_resume_prompt_active" in mount_src, (
        "on_mount must short-circuit when _resume_prompt_active so the "
        "New session banner is suppressed during the resume prompt."
    )
    assert "New session · type a message" in mount_src, (
        "on_mount still owns the normal empty-history banner for the "
        "non-prompt path; this assertion pins the banner presence."
    )


def test_chat_tab_stashes_candidate_instead_of_auto_binding() -> None:
    from openminion.cli.tui.tabs.chat import ChatTab

    src = inspect.getsource(ChatTab)
    assert "_pending_resume_candidate" in src, (
        "ChatTab must stash the candidate in `_pending_resume_candidate` "
        "instead of calling `bind_session` on mount."
    )
    assert "_resume_prompt_active" in src, (
        "ChatTab must track `_resume_prompt_active` so the prompt blocks "
        "the runtime from being bound until the user responds."
    )
    assert "_handle_resume_prompt_reply" in src, (
        "ChatTab must route the user's Y/N reply through "
        "`_handle_resume_prompt_reply` rather than auto-binding."
    )
    # `_resolve_pending_session` must not bind directly; binding waits for Y.
    resolve_src = inspect.getsource(ChatTab._resolve_pending_session)
    assert "bind_session" not in resolve_src, (
        "`_resolve_pending_session` must not call `bind_session` — that "
        "is the auto-resume bug Phase 7 fixes. Binding belongs in "
        "`_handle_resume_prompt_reply` after the user picks Y."
    )
    # Conversely, `_handle_resume_prompt_reply` must be able to bind OR
    # create a fresh session depending on the user's reply.
    reply_src = inspect.getsource(ChatTab._handle_resume_prompt_reply)
    assert "bind_session" in reply_src
    assert "create_new_session" in reply_src


def test_chat_tab_sidebar_uses_current_agent_scope() -> None:
    from openminion.cli.tui.tabs.chat import ChatTab

    src = inspect.getsource(ChatTab)
    assert 'scope="current_agent"' in src, (
        "Dashboard `ChatTab._refresh_sidebar` must call "
        '`list_sessions(scope="current_agent")` so cross-target sessions '
        "do not appear in the dashboard sidebar."
    )


def test_chat_tab_agent_switch_respects_prompt_on_resume() -> None:
    from openminion.cli.tui.tabs.chat import ChatTab

    src = inspect.getsource(ChatTab._do_switch_agent)
    assert "prompt_on_resume" in src
    assert "_resolve_pending_session" in src


# ── Focus-mode regression gate (TUIDS-13) ────────────────────────────────────


def test_focus_screen_not_touched_by_phase_7() -> None:
    import openminion.cli.interactive.screen as focus_screen

    src = inspect.getsource(focus_screen)
    # Focus must not reach into shared-adapter prompt_on_resume plumbing
    # — it keeps its own `find_candidate_session()` + `_initialize_session`.
    assert "prompt_on_resume" not in src, (
        "Focus mode should not consume the shared-adapter `prompt_on_resume` "
        "kwarg — it has its own resume flow owned by the focus tracker."
    )
