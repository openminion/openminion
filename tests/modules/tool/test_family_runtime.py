from __future__ import annotations

import json
from pathlib import Path

from openminion.modules.tool.runtime.policy import Policy
from openminion.modules.tool.runtime import RuntimeContext
from openminion.modules.tool.family.runtime import StopChain, run_provider_chain


def _make_ctx(tmp_path: Path) -> RuntimeContext:
    workspace = tmp_path / "workspace"
    run_root = tmp_path / "run"
    workspace.mkdir(parents=True, exist_ok=True)
    run_root.mkdir(parents=True, exist_ok=True)
    raw: dict = {
        "workspace_root": str(workspace),
        "paths": {
            "read_allow": [str(workspace)],
            "write_allow": [str(workspace)],
            "deny": [],
        },
        "commands": {"mode": "allowlist", "allow": []},
    }
    return RuntimeContext(
        policy=Policy(raw=raw),
        workspace=workspace,
        run_root=run_root,
        scope="READ_ONLY",
        confirm=False,
    )


def _read_audit(ctx: RuntimeContext) -> list[dict]:
    audit_path = ctx.run_root / "audit.jsonl"
    if not audit_path.exists():
        return []
    return [
        json.loads(line)
        for line in audit_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _simple_payload_fn(provider_name: str, attempt_index: int, total: int) -> dict:
    return {"selected_provider": provider_name, "attempt_index": attempt_index}


def _simple_fallback(chain: list, failures: list) -> dict:
    last_exc = failures[-1][1] if failures else Exception("no providers")
    return {"ok": False, "error": str(last_exc)}


def test_run_provider_chain_succeeds_on_first_provider(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path)
    calls: list[str] = []

    def invoke(provider_name: str, attempt_index: int) -> dict:
        calls.append(provider_name)
        return {"ok": True, "source": provider_name}

    result = run_provider_chain(
        ctx,
        chain=["tavily", "brave"],
        attempt_event="search.provider.selected",
        attempt_payload_fn=_simple_payload_fn,
        invoke_fn=invoke,
        fallback_result_fn=_simple_fallback,
    )

    assert result == {"ok": True, "source": "tavily"}
    assert calls == ["tavily"]


def test_run_provider_chain_falls_back_on_first_failure(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path)
    calls: list[str] = []

    def invoke(provider_name: str, attempt_index: int) -> dict:
        calls.append(provider_name)
        if provider_name == "brave":
            raise RuntimeError("brave failed")
        return {"ok": True, "source": provider_name}

    result = run_provider_chain(
        ctx,
        chain=["brave", "tavily"],
        attempt_event="search.provider.selected",
        attempt_payload_fn=_simple_payload_fn,
        invoke_fn=invoke,
        fallback_result_fn=_simple_fallback,
    )

    assert result["ok"] is True
    assert result["source"] == "tavily"
    assert calls == ["brave", "tavily"]


def test_run_provider_chain_calls_fallback_when_all_fail(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path)

    def invoke(provider_name: str, attempt_index: int) -> dict:
        raise RuntimeError(f"{provider_name} failed")

    result = run_provider_chain(
        ctx,
        chain=["brave", "tavily"],
        attempt_event="search.provider.selected",
        attempt_payload_fn=_simple_payload_fn,
        invoke_fn=invoke,
        fallback_result_fn=_simple_fallback,
    )

    assert result["ok"] is False
    assert "tavily failed" in result["error"]


def test_run_provider_chain_empty_chain_returns_fallback(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path)
    invocations: list = []

    def invoke(provider_name: str, attempt_index: int) -> dict:
        invocations.append(provider_name)
        return {"ok": True}

    result = run_provider_chain(
        ctx,
        chain=[],
        attempt_event="test.event",
        attempt_payload_fn=_simple_payload_fn,
        invoke_fn=invoke,
        fallback_result_fn=lambda chain, failures: {
            "ok": False,
            "error": "empty chain",
        },
    )

    assert result == {"ok": False, "error": "empty chain"}
    assert invocations == []


def test_run_provider_chain_stop_chain_halts_immediately(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path)
    calls: list[str] = []

    def invoke(provider_name: str, attempt_index: int) -> dict:
        calls.append(provider_name)
        raise StopChain(
            {"ok": False, "error": "POLICY_DENIED", "source": provider_name}
        )

    result = run_provider_chain(
        ctx,
        chain=["brave", "tavily"],
        attempt_event="search.provider.selected",
        attempt_payload_fn=_simple_payload_fn,
        invoke_fn=invoke,
        fallback_result_fn=_simple_fallback,
    )

    assert result["error"] == "POLICY_DENIED"
    assert calls == ["brave"]


def test_run_provider_chain_emits_attempt_event_per_provider(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path)

    def invoke(provider_name: str, attempt_index: int) -> dict:
        if provider_name == "brave":
            raise RuntimeError("failed")
        return {"ok": True, "source": provider_name}

    run_provider_chain(
        ctx,
        chain=["brave", "tavily"],
        attempt_event="search.provider.selected",
        attempt_payload_fn=_simple_payload_fn,
        invoke_fn=invoke,
        fallback_result_fn=_simple_fallback,
    )

    records = _read_audit(ctx)
    events = [r for r in records if r.get("event") == "search.provider.selected"]
    assert len(events) == 2
    assert events[0]["selected_provider"] == "brave"
    assert events[0]["attempt_index"] == 1
    assert events[1]["selected_provider"] == "tavily"
    assert events[1]["attempt_index"] == 2


def test_run_provider_chain_attempt_payload_receives_correct_total(
    tmp_path: Path,
) -> None:
    ctx = _make_ctx(tmp_path)
    totals_seen: list[int] = []

    def payload_fn(provider_name: str, attempt_index: int, total: int) -> dict:
        totals_seen.append(total)
        return {"selected_provider": provider_name, "attempt_index": attempt_index}

    run_provider_chain(
        ctx,
        chain=["brave", "tavily", "serpapi"],
        attempt_event="search.provider.selected",
        attempt_payload_fn=payload_fn,
        invoke_fn=lambda n, i: {"ok": True},
        fallback_result_fn=_simple_fallback,
    )

    assert totals_seen == [3]


def test_run_provider_chain_attempt_index_increments_per_attempt(
    tmp_path: Path,
) -> None:
    ctx = _make_ctx(tmp_path)
    indices_seen: list[int] = []

    def invoke(provider_name: str, attempt_index: int) -> dict:
        indices_seen.append(attempt_index)
        if attempt_index < 3:
            raise RuntimeError("fail")
        return {"ok": True}

    run_provider_chain(
        ctx,
        chain=["a", "b", "c"],
        attempt_event="test.attempt",
        attempt_payload_fn=lambda n, i, t: {"selected_provider": n, "attempt_index": i},
        invoke_fn=invoke,
        fallback_result_fn=_simple_fallback,
    )

    assert indices_seen == [1, 2, 3]


def test_run_provider_chain_fallback_receives_all_failures(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path)
    received_failures: list[tuple] = []

    def invoke(provider_name: str, attempt_index: int) -> dict:
        raise ValueError(f"err-{provider_name}")

    def fallback(chain: list, failures: list) -> dict:
        received_failures.extend(failures)
        return {"ok": False}

    run_provider_chain(
        ctx,
        chain=["a", "b"],
        attempt_event="test.attempt",
        attempt_payload_fn=lambda n, i, t: {},
        invoke_fn=invoke,
        fallback_result_fn=fallback,
    )

    assert len(received_failures) == 2
    provider_names = [f[0] for f in received_failures]
    assert provider_names == ["a", "b"]
