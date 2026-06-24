from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from openminion.modules.brain.adapters.factory import create_rlm_adapter
from openminion.modules.brain.adapters.recursive import LocalRLMAdapter, RLMAdapter
from openminion.modules.brain.interfaces import BRAIN_ADAPTER_INTERFACE_VERSION
from openminion.modules.brain.schemas.state import BrainMode
from openminion.services.brain.factory import rlm


def test_brain_mode_autonomous_is_typed_enum_value() -> None:

    assert BrainMode.AUTONOMOUS.value == "autonomous"
    assert BrainMode.AUTONOMOUS in set(BrainMode)


def test_local_rlm_adapter_shape_and_source() -> None:

    adapter = LocalRLMAdapter()
    assert adapter.recursive_source == "local_mock"
    assert adapter.contract_version == BRAIN_ADAPTER_INTERFACE_VERSION

    result = adapter.generate(
        session_id="sess-1",
        agent_id="agent-1",
        purpose="act",
        query="hello world",
    )

    for key in (
        "status",
        "final_text",
        "structured_output",
        "ticks_used",
        "stop_reason",
        "evidence_refs",
        "write_intents",
        "total_input_tokens",
        "total_output_tokens",
    ):
        assert key in result, f"LocalRLMAdapter.generate missing {key!r}"

    assert result["status"] == "completed"
    assert result["stop_reason"] == "completed"
    assert result["ticks_used"] == 1
    assert isinstance(result["evidence_refs"], list)
    assert isinstance(result["write_intents"], list)


class _StubRLMService:
    def __init__(self) -> None:
        self._called_with: dict[str, Any] = {}

    def generate(self, **kwargs: Any) -> Any:
        self._called_with = kwargs
        tick_report = SimpleNamespace(input_tokens=7, output_tokens=11)
        telemetry = SimpleNamespace(
            ticks_used=2,
            stop_reason="completed",
            tick_reports=[tick_report],
        )
        return SimpleNamespace(
            final_text="stub-final",
            structured_output={"k": "v"},
            telemetry=telemetry,
            evidence_refs=[],
            memory_write_intents=[],
        )


def test_rlm_adapter_marks_recursive_source_real_and_passes_through_generate() -> None:

    service = _StubRLMService()
    adapter = RLMAdapter(service)
    assert adapter.recursive_source == "real_rlm"
    assert adapter.contract_version == BRAIN_ADAPTER_INTERFACE_VERSION

    result = adapter.generate(
        session_id="sess-2",
        agent_id="agent-2",
        purpose="act",
        query="pin real adapter",
        ts={"retry_count": 0},
        budgets={"max_ticks": 4},
    )

    assert service._called_with["session_id"] == "sess-2"
    assert service._called_with["agent_id"] == "agent-2"
    assert service._called_with["query"] == "pin real adapter"
    assert service._called_with["purpose"] == "act"
    assert service._called_with["ts"] == {"retry_count": 0}
    assert service._called_with["budgets"] == {"max_ticks": 4}

    assert result["status"] == "completed"
    assert result["final_text"] == "stub-final"
    assert result["structured_output"] == {"k": "v"}
    assert result["ticks_used"] == 2
    assert result["stop_reason"] == "completed"
    assert result["total_input_tokens"] == 7
    assert result["total_output_tokens"] == 11
    assert result["evidence_refs"] == []
    assert result["write_intents"] == []


def test_create_rlm_adapter_returns_local_when_service_is_none() -> None:

    adapter = create_rlm_adapter(mode="auto", service=None)
    assert isinstance(adapter, LocalRLMAdapter)
    assert adapter.recursive_source == "local_mock"


def test_create_rlm_adapter_returns_real_when_service_supplied() -> None:

    adapter = create_rlm_adapter(mode="auto", service=_StubRLMService())
    assert isinstance(adapter, RLMAdapter)
    assert adapter.recursive_source == "real_rlm"


class _SilentLogger:
    def info(self, *_args: Any, **_kwargs: Any) -> None: ...
    def warning(self, *_args: Any, **_kwargs: Any) -> None: ...
    def debug(self, *_args: Any, **_kwargs: Any) -> None: ...


def test_init_rlm_adapter_returns_local_when_config_disables_rlm() -> None:

    adapter = rlm.init_rlm_adapter(
        mode="auto",
        config=SimpleNamespace(rlm=SimpleNamespace(enabled=False)),
        session_api=object(),
        context_api=object(),
        llm_api=object(),
        memory_api=None,
        skill_api=None,
        retrieve_api=None,
        logger=_SilentLogger(),
    )
    assert isinstance(adapter, LocalRLMAdapter)
    assert adapter.recursive_source == "local_mock"


def test_init_rlm_adapter_returns_local_when_config_missing_rlm_block() -> None:

    adapter = rlm.init_rlm_adapter(
        mode="auto",
        config=SimpleNamespace(),
        session_api=object(),
        context_api=object(),
        llm_api=object(),
        memory_api=None,
        skill_api=None,
        retrieve_api=None,
        logger=_SilentLogger(),
    )
    assert isinstance(adapter, LocalRLMAdapter)


def test_init_rlm_adapter_falls_back_to_local_when_real_service_import_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    import openminion.modules.brain.loop.recursive.service as recursive_service_mod

    original = recursive_service_mod.RLMService

    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("forced import-path failure for test")

    monkeypatch.setattr(recursive_service_mod, "RLMService", _boom)

    try:
        adapter = rlm.init_rlm_adapter(
            mode="auto",
            config=SimpleNamespace(rlm=SimpleNamespace(enabled=True)),
            session_api=object(),
            context_api=object(),
            llm_api=object(),
            memory_api=None,
            skill_api=None,
            retrieve_api=None,
            logger=_SilentLogger(),
        )
    finally:
        monkeypatch.setattr(recursive_service_mod, "RLMService", original)

    assert isinstance(adapter, LocalRLMAdapter)


def test_canonical_recursive_event_names_are_stable() -> None:

    from openminion.modules.brain.execution import recursive as recursive_exec

    import inspect

    source = inspect.getsource(recursive_exec)
    for event_name in (
        "brain.recursive_turn.started",
        "brain.recursive_turn.completed",
        "brain.recursive_turn.error",
        "brain.recursive_turn.blocked",
        "brain.recursive_turn.writeback_error",
    ):
        assert event_name in source, (
            f"Canonical recursive event {event_name!r} missing from "
            f"execution/recursive.py — BRLI-04 would break telemetry "
            f"consumers if this name changed."
        )


def test_run_recursive_turn_exports_from_execution_package() -> None:

    from openminion.modules.brain.execution import run_recursive_turn

    assert callable(run_recursive_turn)
    import inspect

    sig = inspect.signature(run_recursive_turn)
    assert set(sig.parameters) >= {"runner", "state", "user_input", "logger"}


def test_no_legacy_rlm_imports_remain_in_src_after_brli_05() -> None:

    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[3]
    src_root = repo_root / "src" / "openminion"
    rlm_legacy_dir = src_root / "modules" / "rlm"

    offenders: list[str] = []
    for py_file in src_root.rglob("*.py"):
        if py_file.is_relative_to(rlm_legacy_dir):
            continue
        text = py_file.read_text()
        for idx, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if (
                "from openminion.modules.rlm" in stripped
                or "import openminion.modules.rlm" in stripped
            ):
                offenders.append(f"{py_file.relative_to(repo_root)}:{idx}")

    assert not offenders, (
        f"Found {len(offenders)} legacy `openminion.modules.rlm` "
        f"imports in src/ after BRLI-05: {offenders}. "
        f"Rewire to `openminion.modules.brain.loop.recursive.*`."
    )
