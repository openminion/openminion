from __future__ import annotations

from openminion.modules.memory.models import MemoryPatchResult as V2MemoryPatchResult
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.memory import InMemoryMemoryStore
from openminion.services.agent.memory import MemoryPatchResult as V1MemoryPatchResult
from openminion.services.agent.memory.gateway_adapter import (
    DisabledMemoryGatewayAdapter,
    MemoryServiceGatewayAdapter,
)


def _v2_adapter() -> MemoryServiceGatewayAdapter:
    store = InMemoryMemoryStore()
    service = MemoryService(store=store)
    return MemoryServiceGatewayAdapter(service, agent_id="parity-agent")


class TestMemoryPatchResultParity:
    def test_v1_and_v2_have_same_required_attributes(self) -> None:
        required = {
            "facts_added",
            "todos_added",
            "todos_completed",
            "patch_id",
            "generation",
            "replayed_patches",
            "lock_recovered",
        }
        v1 = V1MemoryPatchResult(facts_added=0, todos_added=0, todos_completed=0)
        v2 = V2MemoryPatchResult(facts_added=0, todos_added=0, todos_completed=0)
        for attr in required:
            assert hasattr(v1, attr), f"V1 missing: {attr}"
            assert hasattr(v2, attr), f"V2 missing: {attr}"

    def test_patch_result_attribute_types_match(self) -> None:
        v2 = V2MemoryPatchResult(
            facts_added=3,
            todos_added=1,
            todos_completed=2,
            patch_id="abc123",
            generation=5,
            replayed_patches=0,
            lock_recovered=False,
        )
        assert isinstance(v2.facts_added, int)
        assert isinstance(v2.todos_added, int)
        assert isinstance(v2.todos_completed, int)
        assert isinstance(v2.patch_id, str)
        assert isinstance(v2.generation, int)
        assert isinstance(v2.replayed_patches, int)
        assert isinstance(v2.lock_recovered, bool)


class TestEnabledPropertyParity:
    def test_v2_adapter_enabled_is_bool(self) -> None:
        adapter = _v2_adapter()
        assert isinstance(adapter.enabled, bool)
        assert adapter.enabled

    def test_disabled_adapter_enabled_is_false(self) -> None:
        adapter = DisabledMemoryGatewayAdapter(agent_id="disabled-agent")
        assert isinstance(adapter.enabled, bool)
        assert not adapter.enabled


class TestRecordTurnSignatureParity:
    def test_record_turn_accepts_all_v1_kwargs(self) -> None:
        adapter = _v2_adapter()
        result = adapter.record_turn(
            session_id="sess",
            run_id="run",
            request_id="req",
            channel="test-channel",
            target="test-target",
            user_message="fact: parity test",
            assistant_message="parity ok",
        )
        assert isinstance(result, V2MemoryPatchResult)

    def test_record_turn_result_has_required_attrs(self) -> None:
        adapter = _v2_adapter()
        result = adapter.record_turn(
            session_id="sess",
            run_id="run",
            request_id="req",
            channel="c",
            target="t",
            user_message="todo: do something",
            assistant_message="",
        )
        assert hasattr(result, "facts_added")
        assert hasattr(result, "todos_added")
        assert hasattr(result, "todos_completed")
        assert hasattr(result, "patch_id")
        assert hasattr(result, "generation")
        assert hasattr(result, "replayed_patches")
        assert hasattr(result, "lock_recovered")


class TestBuildContextSignatureParity:
    def test_build_context_signature(self) -> None:
        adapter = _v2_adapter()
        result = adapter.build_context(session_id="s", user_message="hello")
        assert isinstance(result, str)

    def test_build_context_with_metadata_signature(self) -> None:
        adapter = _v2_adapter()
        result = adapter.build_context_with_metadata(
            session_id="s", user_message="hello"
        )
        assert isinstance(result, tuple)
        assert len(result) == 2
        content, meta = result
        assert isinstance(content, str)
        assert isinstance(meta, dict)

    def test_build_context_with_metadata_has_envelope_keys(self) -> None:
        adapter = _v2_adapter()
        _, meta = adapter.build_context_with_metadata(session_id="s", user_message="")
        assert "memory_envelope_truncated" in meta
        assert "memory_envelope_limit_chars" in meta

    def test_build_retrieval_context_signature(self) -> None:
        adapter = _v2_adapter()
        result = adapter.build_retrieval_context(session_id="s", user_message="query")
        assert isinstance(result, str)

    def test_build_retrieval_context_with_metadata_signature(self) -> None:
        adapter = _v2_adapter()
        result = adapter.build_retrieval_context_with_metadata(
            session_id="s", user_message="query"
        )
        assert isinstance(result, tuple)
        assert len(result) == 2
        content, meta = result
        assert isinstance(content, str)
        assert isinstance(meta, dict)


class TestDerivePatchIdParity:
    def test_derive_patch_id_available(self) -> None:
        adapter = _v2_adapter()
        # getattr pattern used in gateway/memory.py
        derive = getattr(adapter, "derive_patch_id", None)
        assert callable(derive)

    def test_derive_patch_id_returns_str(self) -> None:
        adapter = _v2_adapter()
        result = adapter.derive_patch_id(
            session_id="s", run_id="r", request_id="req", user_message="msg"
        )
        assert isinstance(result, str)

    def test_derive_patch_id_is_32_char_lowercase_hex(self) -> None:
        adapter = _v2_adapter()
        pid = adapter.derive_patch_id(
            session_id="s", run_id="r", request_id="req", user_message="msg"
        )
        assert len(pid) == 32
        assert pid == pid.lower()
        assert all(character in "0123456789abcdef" for character in pid)


class TestGatewayDuckTypingParity:
    def test_all_required_methods_present(self) -> None:
        adapter = _v2_adapter()
        required_methods = [
            "build_context",
            "build_context_with_metadata",
            "build_retrieval_context",
            "build_retrieval_context_with_metadata",
            "record_turn",
        ]
        for method in required_methods:
            assert callable(getattr(adapter, method, None)), f"adapter missing {method}"

    def test_enabled_property_exists(self) -> None:
        adapter = _v2_adapter()
        enabled = adapter.enabled
        assert isinstance(enabled, bool)
