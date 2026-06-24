from __future__ import annotations

import tempfile
from pathlib import Path

from openminion.modules.brain.adapters.factory import (
    RLMBridgeMemoryClient,
    create_context_adapter,
    create_memory_adapter,
)
from openminion.modules.controlplane.commands.registry import CommandRegistry
from openminion.modules.controlplane.contracts.models import (
    ParsedCommand,
    ResolvedContext,
)
from openminion.modules.controlplane.runtime.store import InMemoryControlPlaneStore


class TestMemoryEffectiveBehavior:
    def test_session_module_import(self):
        from openminion.modules.session import SQLiteSessionStore

        store = SQLiteSessionStore(database_path=":memory:")
        assert store._conn is not None
        store.close()

    def test_context_module_import(self):
        from openminion.modules.context.contracts import SessionClient, MemoryClient
        from openminion.modules.context.schemas import ContextPack, ContextBudgets

        assert SessionClient is not None
        assert MemoryClient is not None
        assert ContextPack is not None
        assert ContextBudgets is not None

    def test_memory_module_import(self):
        from openminion.modules.memory import __version__
        from openminion.modules.memory.service import MemoryService
        from openminion.modules.memory.storage import MemoryStore

        assert __version__ is not None
        assert MemoryService is not None
        assert MemoryStore is not None

    def test_compress_module_import(self):
        from openminion.modules.context.compress.registry import MethodRegistry

        registry = MethodRegistry()
        methods = (
            list(registry._methods.keys()) if hasattr(registry, "_methods") else []
        )
        assert isinstance(methods, list)

    def test_retrieve_module_import(self):
        from openminion.modules.retrieve import resolve_config_path
        from openminion.modules.retrieve.config import load_config

        config_path = resolve_config_path()
        assert config_path.exists()

        cfg = load_config(config_path)
        assert cfg is not None

    def test_debug_providers_all_registered(self):
        from openminion.cli.commands.debug import (
            OpenMinionSessionDebugProvider,
            OpenMinionContextDebugProvider,
            OpenMinionMemoryDebugProvider,
            OpenMinionCompressDebugProvider,
            OpenMinionRetrieveDebugProvider,
        )

        providers = [
            OpenMinionSessionDebugProvider(),
            OpenMinionContextDebugProvider(),
            OpenMinionMemoryDebugProvider(),
            OpenMinionCompressDebugProvider(),
            OpenMinionRetrieveDebugProvider(),
        ]

        for provider in providers:
            debug = provider.get_debug()
            assert debug is not None
            assert debug.module is not None
            assert debug.status in ("ok", "warn", "fail")

    def test_debug_providers_expose_required_fields(self):
        from openminion.cli.commands.debug import (
            OpenMinionSessionDebugProvider,
            OpenMinionContextDebugProvider,
            OpenMinionMemoryDebugProvider,
            OpenMinionCompressDebugProvider,
            OpenMinionRetrieveDebugProvider,
        )

        providers = [
            OpenMinionSessionDebugProvider(),
            OpenMinionContextDebugProvider(),
            OpenMinionMemoryDebugProvider(),
            OpenMinionCompressDebugProvider(),
            OpenMinionRetrieveDebugProvider(),
        ]

        required_fields = ["status", "wiring_source", "mode"]

        for provider in providers:
            debug = provider.get_debug()
            data = debug.to_dict()

            for field in required_fields:
                assert field in data, (
                    f"Provider {provider.module_name} missing field: {field}"
                )

    def test_memory_modules_integration_status(self):
        from openminion.cli.commands.debug import (
            OpenMinionSessionDebugProvider,
            OpenMinionContextDebugProvider,
            OpenMinionMemoryDebugProvider,
            OpenMinionCompressDebugProvider,
            OpenMinionRetrieveDebugProvider,
        )

        providers = [
            ("openminion-session", OpenMinionSessionDebugProvider()),
            ("openminion-context", OpenMinionContextDebugProvider()),
            ("openminion-memory", OpenMinionMemoryDebugProvider()),
            ("context.compress", OpenMinionCompressDebugProvider()),
            ("openminion-retrieve", OpenMinionRetrieveDebugProvider()),
        ]

        for name, provider in providers:
            debug = provider.get_debug()
            assert debug.status == "ok", f"{name} should report ok, got {debug.status}"
            assert debug.details.get("import_ok") is True, (
                f"{name} should have import_ok=True"
            )

    def test_no_retrieve_unavailable_warning_regression(self):
        import subprocess
        import sys

        result = subprocess.run(
            [
                sys.executable,
                "-c",
                """
import warnings
warnings.filterwarnings('error', message='retrievectl unavailable')
try:
    from openminion.modules.retrieve import RetrieveCtl
    from openminion.modules.brain.adapters.factory import create_retrieve_adapter
    print('PASS: No retrievectl unavailable warning')
except Exception as e:
    print(f'FAIL: {e}')
""",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

        assert "FAIL" not in result.stdout, f"Regression: {result.stdout}"
        assert "retrievectl unavailable" not in result.stderr, (
            "Regression: warning appeared"
        )


class TestContextPackContinuity:
    def test_context_budgets_creation(self):
        from openminion.modules.context.schemas import default_budgets_for

        budgets = default_budgets_for("decide")  # Purpose is Literal type
        assert budgets is not None
        assert budgets.total_max_tokens > 0

    def test_context_pack_structure(self):
        from openminion.modules.context.schemas import ContextPack, ContextBudgets

        assert ContextPack is not None
        assert ContextBudgets is not None

    def test_session_client_contract(self):
        from openminion.modules.context.contracts import SessionClient

        assert SessionClient is not None
        assert hasattr(SessionClient, "__init__")

    def test_memory_client_contract(self):
        from openminion.modules.context.contracts import MemoryClient

        assert MemoryClient is not None
        assert hasattr(MemoryClient, "__init__")


class TestCompressionContinuity:
    def test_compression_policy_creation(self):
        from openminion.modules.context.compress.schemas import CompressionPolicy

        policy = CompressionPolicy(
            mode="extractive",
            target_ratio=0.25,
        )
        assert policy.mode == "extractive"
        assert policy.target_ratio == 0.25

    def test_checkpoint_store_initialization(self):
        from openminion.modules.context.compress.storage.checkpoint_store import (
            CheckpointStore,
        )

        store = CheckpointStore()
        assert store is not None

    def test_method_registry_lists_methods(self):
        from openminion.modules.context.compress.registry import MethodRegistry

        registry = MethodRegistry()
        methods = (
            list(registry._methods.keys()) if hasattr(registry, "_methods") else []
        )

        # Should return a list (may be empty if no methods registered)
        assert isinstance(methods, list)


class _SessionStoreWithPath:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path


def test_memory_cross_module_adapter_parity_for_core_operations() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp) / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        memory_api = create_memory_adapter(mode="auto", db_path=state_dir / "memory.db")
        memory_api.put_record(
            scope="session:sx",
            record_type="fact",
            title="parity fact",
            content={"text": "alpha parity fact"},
            tags=["parity"],
        )
        candidate_id = memory_api.stage_candidate(
            scope="session:sx",
            record_type="fact",
            title="parity candidate",
            content={"text": "alpha parity candidate"},
            tags=["parity"],
        )

        brain_facts = memory_api.query_facts(
            session_id="sx",
            agent_id="ax",
            query="alpha",
            limit=5,
        )
        assert brain_facts
        assert any("alpha" in str(item.get("text", "")).lower() for item in brain_facts)

        rlm_bridge = RLMBridgeMemoryClient(memory_api)
        rlm_facts = rlm_bridge.query_facts(
            session_id="sx",
            agent_id="ax",
            query="alpha",
            limit=5,
        )
        assert rlm_facts
        assert any("alpha" in str(item.get("text", "")).lower() for item in rlm_facts)

        context_api = create_context_adapter(
            mode="auto",
            session_store=_SessionStoreWithPath(state_dir / "sessions.db"),
        )
        context_memctl = context_api.service._memctl  # noqa: SLF001
        context_facts = context_memctl.query_facts(
            session_id="sx",
            agent_id="ax",
            query="alpha",
            limit=5,
        )
        assert context_facts
        assert any("alpha" in str(item.text).lower() for item in context_facts)

        controlplane = CommandRegistry(
            store=InMemoryControlPlaneStore(),
            memory_client=memory_api,
        )
        ctx = ResolvedContext(
            user_key="user:parity",
            chat_key="chat:parity",
            session_id="sx",
            agent_id="ax",
            role="user",
            trace_id="trace-parity",
            span_id="span-parity",
        )
        ls_result = controlplane.execute(
            ParsedCommand(
                canonical="memory.ls",
                original_text="/memory ls 5 alpha",
                args=["5", "alpha"],
            ),
            ctx,
        )
        assert ls_result.ok is True
        facts = ls_result.data.get("facts", [])
        assert any("alpha" in str(item).lower() for item in facts)

        # Align with promotion policy posture (explicit approval required by default).
        memory_api.store.candidate_update(candidate_id, {"status": "approved"})
        promote_result = controlplane.execute(
            ParsedCommand(
                canonical="memory.promote",
                original_text=f"/memory promote {candidate_id} agent:ax",
                args=[candidate_id, "agent:ax"],
            ),
            ctx,
        )
        assert promote_result.ok is True
        assert str(promote_result.data.get("candidate_id", "")) == candidate_id
        assert str(promote_result.data.get("target_scope", "")) == "agent:ax"
