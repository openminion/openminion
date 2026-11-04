"""Completed child conclusions must survive research's bounded handoffs."""

from types import SimpleNamespace

import pytest

from openminion.modules.brain.loop.strategies.research.findings import (
    build_synthesis_prompt,
    research_source_coverage,
)
from openminion.modules.brain.schemas import ActionResult
from openminion.modules.task import TaskManager
from tests.brain.modes.test_research_contract import _ctx, _make_mode, _state

MARKER = "APPROVED_LIMIT=731"
URL = "https://evidence.test/limits"
DISPATCH = (
    "openminion.modules.brain.loop.strategies.research.handler.invoke_decision_direct"
)


def _child(
    *,
    source,
    message=MARKER,
    summary=MARKER,
    status="done",
    ok=True,
    action_status="success",
    working_only=False,
):
    def invoke(runner, **kwargs):
        state = kwargs["state"]
        results = [
            {
                "tool_name": "web.fetch",
                "ok": ok,
                "call_id": "read-1",
                "content": source,
                "data": {"url": URL, "published_at": "2026-10-30"},
            }
        ]
        return SimpleNamespace(
            status=status,
            message=message,
            action_result=ActionResult(
                command_id="read-completed",
                status=action_status,
                summary=summary,
                outputs={} if working_only else {"tool_results": results},
            ),
            working_state=(
                SimpleNamespace(scratchpad={"adaptive.tool_results": results})
                if working_only
                else state
            ),
        )

    return invoke


@pytest.mark.parametrize("long_source", [False, True])
@pytest.mark.parametrize("working_only", [False, True])
@pytest.mark.parametrize("message", [MARKER, "  "])
def test_completed_answer_reaches_finding_and_both_prompts(
    tmp_path,
    monkeypatch,
    long_source,
    working_only,
    message,
):
    tm = TaskManager.for_lifecycle_db(db_path=tmp_path / "tasks.db")
    ctx, services = _ctx(tm)
    mode = _make_mode(max_iterations=1)
    source = ("Background material. " * 55 if long_source else "Brief. ") + MARKER
    monkeypatch.setattr(
        DISPATCH,
        _child(
            source=source,
            message=message,
            working_only=working_only,
        ),
    )
    mode.execute(ctx)
    finding = mode._findings[0]
    assert finding["content"] == MARKER
    assert finding["source_tools"] == ["web.fetch"]
    assert finding["source_urls"] == [URL]
    assert finding["readable_source_urls"] == [URL]
    assert finding["evidence_refs"] == ["read-1", URL]
    assert finding["evidence_dates"] == ["2026-10-30"]
    assert research_source_coverage([finding]) == 1
    goal = mode._build_iteration_goal(
        query="limit", scope="", findings=[finding], iteration=1
    )
    assert MARKER in goal
    prompt = services.runner.llm_api.calls[-1]["context"]["user_input"]
    assert MARKER in prompt
    assert URL in prompt


@pytest.mark.parametrize("status", ["waiting_user", "error"])
@pytest.mark.parametrize("ok", [False, True])
def test_unfinished_child_keeps_only_successful_tool_evidence(
    tmp_path, monkeypatch, status, ok
):
    tm = TaskManager.for_lifecycle_db(db_path=tmp_path / "tasks.db")
    ctx, _ = _ctx(tm)
    monkeypatch.setattr(
        DISPATCH, _child(source="Partial evidence.", status=status, ok=ok)
    )
    finding = _make_mode()._execute_search_iteration(
        ctx, iteration=0, query="limit", findings_so_far=[]
    )
    assert finding.content == ("Partial evidence." if ok else "")
    assert MARKER not in finding.content
    assert finding.evidence_refs == (["read-1", URL] if ok else [])


@pytest.mark.parametrize(
    "ok,action_status", [(True, "success"), (False, "success"), (True, "failed")]
)
def test_no_completed_message_does_not_promote_unbacked_or_failed_summary(
    tmp_path,
    monkeypatch,
    ok,
    action_status,
):
    tm = TaskManager.for_lifecycle_db(db_path=tmp_path / "tasks.db")
    ctx, _ = _ctx(tm)
    monkeypatch.setattr(
        DISPATCH, _child(source="", message="", ok=ok, action_status=action_status)
    )
    finding = _make_mode()._execute_search_iteration(
        ctx, iteration=0, query="limit", findings_so_far=[]
    )
    assert finding.content == ""


def test_completed_child_without_usable_answer_retains_source_fallback(
    tmp_path, monkeypatch
):
    tm = TaskManager.for_lifecycle_db(db_path=tmp_path / "tasks.db")
    ctx, _ = _ctx(tm)
    source = "Background material. " * 55 + MARKER
    monkeypatch.setattr(DISPATCH, _child(source=source, message=" ", summary=" "))
    finding = _make_mode()._execute_search_iteration(
        ctx, iteration=0, query="limit", findings_so_far=[]
    )
    assert finding.content == source[:500]
    assert finding.evidence_refs == ["read-1", URL]


def test_multiple_answers_survive_persisted_checkpoint_and_resume(
    tmp_path, monkeypatch
):
    tm = TaskManager.for_lifecycle_db(db_path=tmp_path / "tasks.db")
    ctx, _ = _ctx(tm, state=_state(ticks=1))
    mode = _make_mode(max_iterations=3)
    source = "Background material. " * 55 + MARKER
    monkeypatch.setattr(DISPATCH, _child(source=source))
    assert mode.execute(ctx).status == "waiting_user"
    checkpoint_id = ctx.state.task_backed_checkpoint_id
    persisted = tm.get_latest_checkpoint(ctx.state.task_backed_task_id)[1]["payload"]
    assert persisted["findings"][0]["content"] == MARKER

    resumed = _make_mode(max_iterations=3)
    resumed_state = ctx.state.model_copy(deep=True)
    resumed_state.budgets_remaining.ticks = 20
    resumed_ctx, services = _ctx(tm, state=resumed_state)
    resumed_ctx.state.task_backed_resume_state = resumed.resume(
        resumed_ctx, checkpoint_id
    )
    second = "SECOND_LIMIT=29"
    goals = []
    invoke = _child(source=source + second, message=second, summary=second)

    def child(runner, **kwargs):
        goals.append(kwargs["user_input"])
        return invoke(runner, **kwargs)

    monkeypatch.setattr(DISPATCH, child)
    resumed.execute(resumed_ctx)
    assert MARKER in goals[0]
    assert [f["content"] for f in resumed._findings][:2] == [MARKER, second]
    assert resumed._findings[0] == persisted["findings"][0]
    prompt = services.runner.llm_api.calls[-1]["context"]["user_input"]
    assert MARKER in prompt and second in prompt
    assert all(f["evidence_refs"] == ["read-1", URL] for f in resumed._findings)


@pytest.mark.parametrize("length", [200, 201, 800, 801])
def test_existing_projection_limits_are_unchanged(tmp_path, monkeypatch, length):
    """Compact conclusions fit; longer prose still has explicit positional limits."""
    answer = "A" * (length - len(MARKER)) + MARKER
    tm = TaskManager.for_lifecycle_db(db_path=tmp_path / "tasks.db")
    ctx, _ = _ctx(tm)
    mode = _make_mode()
    monkeypatch.setattr(
        DISPATCH, _child(source="Background. " * 100 + answer, message=answer)
    )
    finding = mode._execute_search_iteration(
        ctx, iteration=0, query="limit", findings_so_far=[]
    )
    assert finding.content == answer
    findings = [finding.model_dump(mode="python")]
    goal = mode._build_iteration_goal(
        query="limit", scope="", findings=findings, iteration=1
    )
    prompt = build_synthesis_prompt(query="limit", findings=findings)
    assert f"Prior findings (latest 1): {answer[:200]}\nIteration: 2" in goal
    assert f"- Iteration 0: {answer[:800]}\n  sources={URL}" in prompt
    assert (MARKER in goal) == (length <= 200)
    assert (MARKER in prompt) == (length <= 800)


def test_completed_message_wins_over_action_summary(tmp_path, monkeypatch):
    tm = TaskManager.for_lifecycle_db(db_path=tmp_path / "tasks.db")
    ctx, _ = _ctx(tm)
    monkeypatch.setattr(
        DISPATCH,
        _child(source="Background. " * 100 + MARKER, summary="Read succeeded."),
    )
    finding = _make_mode()._execute_search_iteration(
        ctx, iteration=0, query="limit", findings_so_far=[]
    )
    assert finding.content == MARKER


def test_failed_action_summary_does_not_replace_successful_partial_evidence(
    tmp_path, monkeypatch
):
    tm = TaskManager.for_lifecycle_db(db_path=tmp_path / "tasks.db")
    ctx, _ = _ctx(tm)
    monkeypatch.setattr(
        DISPATCH, _child(source="Partial evidence.", message="", action_status="failed")
    )
    finding = _make_mode()._execute_search_iteration(
        ctx, iteration=0, query="limit", findings_so_far=[]
    )
    assert finding.content == "Partial evidence."
