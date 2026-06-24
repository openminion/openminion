from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from openminion.modules.brain.execution.closure import _active_plan_at_closure


@dataclass
class _FastPathStore:
    plan: dict[str, Any] | None
    calls: list[str] = field(default_factory=list)

    def get_active_task_plan(self, session_id: str) -> dict[str, Any] | None:
        self.calls.append(session_id)
        return self.plan


@dataclass
class _SlicePathStore:
    slice_value: dict[str, Any]
    calls: list[tuple[str, str, dict[str, Any] | None]] = field(default_factory=list)

    def get_slice(
        self,
        session_id: str,
        purpose: str = "decide",
        limits: Any = None,
    ) -> dict[str, Any]:
        self.calls.append((session_id, purpose, dict(limits or {})))
        return self.slice_value


@dataclass
class _BareSessionApi:
    store: Any


@dataclass
class _SessionApiOnly:
    plan: dict[str, Any] | None

    def get_active_task_plan(self, session_id: str) -> dict[str, Any] | None:
        return self.plan


@dataclass
class _FaultyStore:
    def get_active_task_plan(self, session_id: str) -> dict[str, Any] | None:
        raise RuntimeError("session-store-down")

    def get_slice(self, *args, **kwargs) -> dict[str, Any]:
        raise RuntimeError("session-store-down")


@dataclass
class _Runner:
    session_api: Any | None


@dataclass
class _State:
    session_id: str = "sess-1"


def test_returns_none_when_runner_has_no_session_api() -> None:
    runner = _Runner(session_api=None)
    state = _State()
    assert _active_plan_at_closure(runner, state) is None


def test_returns_none_when_session_id_empty() -> None:
    runner = _Runner(session_api=_BareSessionApi(store=_FastPathStore(plan={})))
    state = _State(session_id="")
    assert _active_plan_at_closure(runner, state) is None


def test_returns_none_when_store_has_no_callable_lookup() -> None:
    @dataclass
    class _EmptyStore:
        pass

    runner = _Runner(session_api=_BareSessionApi(store=_EmptyStore()))
    state = _State()
    assert _active_plan_at_closure(runner, state) is None


def test_uses_fast_path_when_available() -> None:
    plan = {"plan_id": "p1", "steps": [{"step_id": "s1", "status": "pending"}]}
    store = _FastPathStore(plan=plan)
    runner = _Runner(session_api=_BareSessionApi(store=store))
    state = _State(session_id="sess-42")
    result = _active_plan_at_closure(runner, state)
    assert result == plan
    assert store.calls == ["sess-42"]
    assert result is not plan


def test_fast_path_returning_none_falls_back_to_slice_or_none() -> None:
    @dataclass
    class _BothStore:
        slice_value: dict[str, Any]

        def get_active_task_plan(self, session_id: str) -> dict[str, Any] | None:
            return None

        def get_slice(
            self, session_id: str, purpose: str = "decide", limits: Any = None
        ) -> dict[str, Any]:
            return self.slice_value

    plan = {"plan_id": "p1", "steps": [{"step_id": "s1", "status": "completed"}]}
    store = _BothStore(slice_value={"active_task_plan": plan})
    runner = _Runner(session_api=_BareSessionApi(store=store))
    state = _State()
    assert _active_plan_at_closure(runner, state) == plan


def test_session_api_acting_as_store_directly() -> None:
    plan = {"plan_id": "p1", "steps": [{"step_id": "s1", "status": "blocked"}]}
    session_api = _SessionApiOnly(plan=plan)
    runner = _Runner(session_api=session_api)
    state = _State()
    assert _active_plan_at_closure(runner, state) == plan


def test_slice_path_extracts_active_task_plan_key() -> None:
    plan = {"plan_id": "p1", "steps": []}
    store = _SlicePathStore(slice_value={"active_task_plan": plan})
    runner = _Runner(session_api=_BareSessionApi(store=store))
    state = _State()
    result = _active_plan_at_closure(runner, state)
    assert result == plan
    assert len(store.calls) == 1
    sid, purpose, limits = store.calls[0]
    assert sid == "sess-1"
    assert purpose == "decide"
    assert limits == {"max_turns": 1, "max_tool_events": 0}


def test_slice_path_returns_none_when_active_task_plan_missing() -> None:
    store = _SlicePathStore(slice_value={"some_other_key": {}})
    runner = _Runner(session_api=_BareSessionApi(store=store))
    state = _State()
    assert _active_plan_at_closure(runner, state) is None


def test_slice_path_returns_none_when_slice_returns_non_dict() -> None:
    @dataclass
    class _NonDictSliceStore:
        def get_slice(self, *args, **kwargs):
            return "broken"

    runner = _Runner(session_api=_BareSessionApi(store=_NonDictSliceStore()))
    state = _State()
    assert _active_plan_at_closure(runner, state) is None


def test_slice_path_handles_keyword_only_signature() -> None:
    @dataclass
    class _KwargsOnlyStore:
        plan: dict[str, Any]
        calls: list[dict[str, Any]] = field(default_factory=list)

        def get_slice(
            self, *, session_id: str, purpose: str, limits: Any
        ) -> dict[str, Any]:
            self.calls.append(
                {"session_id": session_id, "purpose": purpose, "limits": dict(limits)}
            )
            return {"active_task_plan": self.plan}

    plan = {"plan_id": "p1", "steps": [{"step_id": "s1", "status": "completed"}]}
    store = _KwargsOnlyStore(plan=plan)
    runner = _Runner(session_api=_BareSessionApi(store=store))
    state = _State()
    assert _active_plan_at_closure(runner, state) == plan
    assert store.calls and store.calls[0]["purpose"] == "decide"


def test_returns_none_when_lookup_raises() -> None:
    runner = _Runner(session_api=_BareSessionApi(store=_FaultyStore()))
    state = _State()
    assert _active_plan_at_closure(runner, state) is None
