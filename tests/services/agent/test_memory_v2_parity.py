from __future__ import annotations

import unittest

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


class TestMemoryPatchResultParity(unittest.TestCase):
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
            with self.subTest(attr=attr):
                self.assertTrue(hasattr(v1, attr), f"V1 missing: {attr}")
                self.assertTrue(hasattr(v2, attr), f"V2 missing: {attr}")

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
        self.assertIsInstance(v2.facts_added, int)
        self.assertIsInstance(v2.todos_added, int)
        self.assertIsInstance(v2.todos_completed, int)
        self.assertIsInstance(v2.patch_id, str)
        self.assertIsInstance(v2.generation, int)
        self.assertIsInstance(v2.replayed_patches, int)
        self.assertIsInstance(v2.lock_recovered, bool)


class TestEnabledPropertyParity(unittest.TestCase):
    def test_v2_adapter_enabled_is_bool(self) -> None:
        adapter = _v2_adapter()
        self.assertIsInstance(adapter.enabled, bool)
        self.assertTrue(adapter.enabled)

    def test_disabled_adapter_enabled_is_false(self) -> None:
        adapter = DisabledMemoryGatewayAdapter(agent_id="disabled-agent")
        self.assertIsInstance(adapter.enabled, bool)
        self.assertFalse(adapter.enabled)


class TestRecordTurnSignatureParity(unittest.TestCase):
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
        self.assertIsInstance(result, V2MemoryPatchResult)

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
        self.assertTrue(hasattr(result, "facts_added"))
        self.assertTrue(hasattr(result, "todos_added"))
        self.assertTrue(hasattr(result, "todos_completed"))
        self.assertTrue(hasattr(result, "patch_id"))
        self.assertTrue(hasattr(result, "generation"))
        self.assertTrue(hasattr(result, "replayed_patches"))
        self.assertTrue(hasattr(result, "lock_recovered"))


class TestBuildContextSignatureParity(unittest.TestCase):
    def test_build_context_signature(self) -> None:
        adapter = _v2_adapter()
        result = adapter.build_context(session_id="s", user_message="hello")
        self.assertIsInstance(result, str)

    def test_build_context_with_metadata_signature(self) -> None:
        adapter = _v2_adapter()
        result = adapter.build_context_with_metadata(
            session_id="s", user_message="hello"
        )
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)
        content, meta = result
        self.assertIsInstance(content, str)
        self.assertIsInstance(meta, dict)

    def test_build_context_with_metadata_has_envelope_keys(self) -> None:
        adapter = _v2_adapter()
        _, meta = adapter.build_context_with_metadata(session_id="s", user_message="")
        self.assertIn("memory_envelope_truncated", meta)
        self.assertIn("memory_envelope_limit_chars", meta)

    def test_build_retrieval_context_signature(self) -> None:
        adapter = _v2_adapter()
        result = adapter.build_retrieval_context(session_id="s", user_message="query")
        self.assertIsInstance(result, str)

    def test_build_retrieval_context_with_metadata_signature(self) -> None:
        adapter = _v2_adapter()
        result = adapter.build_retrieval_context_with_metadata(
            session_id="s", user_message="query"
        )
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)
        content, meta = result
        self.assertIsInstance(content, str)
        self.assertIsInstance(meta, dict)


class TestDerivePatchIdParity(unittest.TestCase):
    def test_derive_patch_id_available(self) -> None:
        adapter = _v2_adapter()
        # getattr pattern used in gateway/memory.py
        derive = getattr(adapter, "derive_patch_id", None)
        self.assertTrue(callable(derive))

    def test_derive_patch_id_returns_str(self) -> None:
        adapter = _v2_adapter()
        result = adapter.derive_patch_id(
            session_id="s", run_id="r", request_id="req", user_message="msg"
        )
        self.assertIsInstance(result, str)

    def test_derive_patch_id_12_chars(self) -> None:
        adapter = _v2_adapter()
        pid = adapter.derive_patch_id(
            session_id="s", run_id="r", request_id="req", user_message="msg"
        )
        self.assertEqual(len(pid), 12)


class TestGatewayDuckTypingParity(unittest.TestCase):
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
            with self.subTest(method=method):
                self.assertTrue(
                    callable(getattr(adapter, method, None)),
                    f"adapter missing {method}",
                )

    def test_enabled_property_exists(self) -> None:
        adapter = _v2_adapter()
        enabled = adapter.enabled
        self.assertIsInstance(enabled, bool)
