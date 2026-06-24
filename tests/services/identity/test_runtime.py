import asyncio
import logging
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

from openminion.base.config import OpenMinionConfig
from openminion.base.types import Message
from openminion.services.runtime.plugins import PluginRegistry
from openminion.modules.llm.providers.base import (
    LLMProvider,
    ProviderRequest,
    ProviderResponse,
)
from openminion.services.agent import AgentService
from openminion.services.context.adapter import ContextCtlGatewayAdapter
from openminion.services.identity.bootstrap import ensure_default_profile
from openminion.services.identity.client import IdentityBundleClient

from openminion.modules.identity.models import (
    AgentProfile,
    PersonalitySpec,
    RiskSpec,
    RoleSpec,
    ToolPostureSpec,
)
from openminion.modules.identity.runtime.service import IdentityCtl
from openminion.modules.identity.storage.store import SQLiteIdentityStore
from openminion.services.agent.identity_binding import AgentIdentityMixin
from tests._csc_fixtures import _csc_install_default_agent


class _CaptureProvider(LLMProvider):
    name = "identity-capture"

    def __init__(self) -> None:
        self.last_request: ProviderRequest | None = None

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.last_request = request
        return ProviderResponse(text="ok", model="capture-model")


def _sample_profile(agent_id: str = "openminion") -> AgentProfile:
    return AgentProfile(
        agent_id=agent_id,
        display_name="OpenMinion",
        profile_revision=1,
        role=RoleSpec(
            mission="Help developers debug code quickly.",
            responsibilities=["Provide concrete next steps"],
            hard_constraints=["MUST NOT fabricate facts"],
            domain=["software"],
            escalation_rules=[],
        ),
        personality=PersonalitySpec(
            tone="technical",
            verbosity="normal",
            formatting=["bullet points"],
            interaction_style=["concise"],
        ),
        risk=RiskSpec(
            risk_level="medium",
            confirm_before=["destructive_actions"],
            auto_proceed_rules=[],
        ),
        tool_posture=ToolPostureSpec(
            tool_use="allowed",
            blocked_patterns=[],
            allowed_tools=[],
            sandbox_root=None,
        ),
    )


class IdentityRuntimeInjectionTests(unittest.TestCase):
    def _build_service_without_identity_init(
        self,
        *,
        config: OpenMinionConfig,
        provider: LLMProvider | None = None,
    ) -> AgentService:
        runtime_provider = provider or _CaptureProvider()
        with patch.object(AgentService, "_init_identity_runtime", return_value=None):
            return AgentService(
                config=config,
                plugins=PluginRegistry([]),
                provider=runtime_provider,
                logger=logging.getLogger("openminion.tests.identity.runtime"),
            )

    def test_agent_service_keeps_identity_runtime_api_without_mixin_inheritance(
        self,
    ) -> None:
        self.assertFalse(issubclass(AgentService, AgentIdentityMixin))
        for name in (
            "_init_identity_runtime",
            "_sync_startup_yaml_profiles",
            "_import_identity_bundle_profile",
            "_resolve_bundle_root",
            "_discover_startup_yaml_profile_paths",
        ):
            self.assertIn(name, AgentService.__dict__)
            self.assertTrue(callable(getattr(AgentService, name)))

    def test_identityctl_replaces_real_identity_client_in_context_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "identity.db"
            ctl = IdentityCtl(store=SQLiteIdentityStore(sqlite_path=str(db_path)))
            ctl.upsert_profile(_sample_profile())

            adapter = ContextCtlGatewayAdapter()
            with patch.dict(
                "os.environ",
                {"OPENMINION_IDENTITY_DB": str(db_path)},
                clear=False,
            ):
                messages = adapter._call_ctxctl(  # noqa: SLF001 - intentional test for adapter internals
                    session_id="sess-1",
                    agent_id="openminion",
                    query="test query",
                    purpose="act",
                )

            rendered = "\n".join(m.content for m in messages)
            self.assertIn("Mission:", rendered)
            self.assertNotIn("# Agent Identity", rendered)

    def test_ensure_default_profile_creates_on_first_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "identity.db"
            ctl = IdentityCtl(store=SQLiteIdentityStore(sqlite_path=str(db_path)))
            self.assertIsNone(ctl.get_profile("openminion"))

            ensure_default_profile(
                ctl,
                "openminion",
                "You are OpenMinion. Keep outputs concise and actionable.",
            )

            profile = ctl.get_profile("openminion")
            self.assertIsNotNone(profile)
            self.assertIn("You are OpenMinion.", profile.role.mission)
            if profile is None:  # pragma: no cover
                self.fail("expected default profile")
            self.assertEqual(dict(profile.meta or {}).get("source"), "default")

    def test_ensure_default_profile_no_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "identity.db"
            ctl = IdentityCtl(store=SQLiteIdentityStore(sqlite_path=str(db_path)))
            original = _sample_profile()
            ctl.upsert_profile(original)

            ensure_default_profile(ctl, "openminion", "You are a different prompt.")
            profile = ctl.get_profile("openminion")
            self.assertIsNotNone(profile)
            self.assertEqual(profile.role.mission, original.role.mission)

    def test_agent_service_injects_identity_into_system_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "identity.db"
            ctl = IdentityCtl(store=SQLiteIdentityStore(sqlite_path=str(db_path)))
            ctl.upsert_profile(_sample_profile())

            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.identity.root = str(db_path)
            provider = _CaptureProvider()
            service = AgentService(
                config=config,
                plugins=PluginRegistry([]),
                provider=provider,
                logger=logging.getLogger("openminion.tests.identity.runtime"),
            )

            response = asyncio.run(
                service.run_turn(Message(channel="console", target="cli", body="hello"))
            )
            self.assertEqual(response.text, "ok")
            self.assertIsNotNone(provider.last_request)
            if provider.last_request is None:
                self.fail("expected captured provider request")
            self.assertIn("## Your Identity", provider.last_request.system_prompt)
            self.assertIn("Mission:", provider.last_request.system_prompt)

    def test_agent_service_imports_identity_bundle_into_identityctl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "identity.db"
            bundle_root = Path(tmp) / "bundles"
            agent_bundle = bundle_root / "agents" / "openminion"
            skill_root = agent_bundle / "SKILLS" / "triage"
            skill_root.mkdir(parents=True)
            (agent_bundle / "AGENT.md").write_text(
                "\n".join(
                    [
                        "## Mission",
                        "Investigate production incidents quickly.",
                        "",
                        "## Responsibilities",
                        "- Summarize evidence",
                    ]
                ),
                encoding="utf-8",
            )
            (agent_bundle / "SOUL.md").write_text(
                "\n".join(
                    [
                        "## Voice",
                        "- Calm and direct",
                        "",
                        "## Values",
                        "- Be explicit about unknowns",
                    ]
                ),
                encoding="utf-8",
            )
            (skill_root / "SKILL.md").write_text(
                "Prioritize root-cause hypotheses with supporting logs.",
                encoding="utf-8",
            )

            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.identity.root = str(db_path)
            config.identity.bundle_root = str(bundle_root)
            provider = _CaptureProvider()
            service = AgentService(
                config=config,
                plugins=PluginRegistry([]),
                provider=provider,
                logger=logging.getLogger("openminion.tests.identity.runtime"),
            )

            self.assertIsNotNone(service._identityctl)  # noqa: SLF001
            if service._identityctl is None:  # pragma: no cover
                self.fail("identity runtime should be initialized")

            imported = service._identityctl.get_profile("openminion")
            self.assertIsNotNone(imported)
            if imported is None:  # pragma: no cover
                self.fail("imported profile should exist")
            self.assertEqual(
                imported.role.mission, "Investigate production incidents quickly."
            )
            self.assertEqual(imported.personality.tone, "Calm and direct.")
            self.assertTrue(bool((imported.meta or {}).get("bundle_imported")))
            self.assertTrue(bool((imported.meta or {}).get("bundle_fingerprint")))
            self.assertEqual(dict(imported.meta or {}).get("source"), "bundle")

            response = asyncio.run(
                service.run_turn(Message(channel="console", target="cli", body="hello"))
            )
            self.assertEqual(response.text, "ok")
            self.assertIsNotNone(provider.last_request)
            if provider.last_request is None:  # pragma: no cover
                self.fail("expected captured provider request")
            self.assertIn(
                "Investigate production incidents quickly.",
                provider.last_request.system_prompt,
            )

    def test_identity_bundle_import_skips_unchanged_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "identity.db"
            bundle_root = Path(tmp) / "bundles"
            agent_bundle = bundle_root / "agents" / "openminion"
            agent_bundle.mkdir(parents=True)
            (agent_bundle / "AGENT.md").write_text(
                "## Mission\nKeep runtime identity in sync.\n",
                encoding="utf-8",
            )
            (agent_bundle / "SOUL.md").write_text(
                "## Voice\n- Precise\n",
                encoding="utf-8",
            )

            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.identity.root = str(db_path)
            config.identity.bundle_root = str(bundle_root)
            provider = _CaptureProvider()
            service = AgentService(
                config=config,
                plugins=PluginRegistry([]),
                provider=provider,
                logger=logging.getLogger("openminion.tests.identity.runtime"),
            )

            self.assertIsNotNone(service._identityctl)  # noqa: SLF001
            if service._identityctl is None:  # pragma: no cover
                self.fail("identity runtime should be initialized")
            first = service._identityctl.get_profile("openminion")
            self.assertIsNotNone(first)
            if first is None:  # pragma: no cover
                self.fail("expected first imported profile")

            with patch.object(
                service._identityctl,
                "upsert_profile",
                side_effect=AssertionError(
                    "upsert should be skipped when fingerprint unchanged"
                ),
            ):
                skipped = service._import_identity_bundle_profile()  # noqa: SLF001

            self.assertTrue(skipped)
            second = service._identityctl.get_profile("openminion")
            self.assertIsNotNone(second)
            if second is None:  # pragma: no cover
                self.fail("expected second profile lookup")
            self.assertEqual(second.profile_revision, first.profile_revision)

    def test_identity_bundle_import_skips_legacy_protected_profile_without_fingerprint(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "identity.db"
            bundle_root = Path(tmp) / "bundles"
            agent_bundle = bundle_root / "agents" / "openminion"
            agent_bundle.mkdir(parents=True)
            (agent_bundle / "AGENT.md").write_text(
                "## Mission\nBundle-owned mission.\n",
                encoding="utf-8",
            )
            (agent_bundle / "SOUL.md").write_text(
                "## Voice\n- Precise\n",
                encoding="utf-8",
            )

            seed_ctl = IdentityCtl(store=SQLiteIdentityStore(sqlite_path=str(db_path)))
            existing = _sample_profile()
            existing.role.mission = "YAML-like mission without fingerprint metadata."
            existing.profile_revision = 4
            existing.meta = {}
            seed_ctl.upsert_profile(existing)

            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.identity.root = str(db_path)
            config.identity.bundle_root = str(bundle_root)
            provider = _CaptureProvider()
            service = AgentService(
                config=config,
                plugins=PluginRegistry([]),
                provider=provider,
                logger=logging.getLogger("openminion.tests.identity.runtime"),
            )

            self.assertIsNotNone(service._identityctl)  # noqa: SLF001
            if service._identityctl is None:  # pragma: no cover
                self.fail("identity runtime should be initialized")
            imported = service._identityctl.get_profile("openminion")
            self.assertIsNotNone(imported)
            if imported is None:  # pragma: no cover
                self.fail("expected profile after bundle import")
            self.assertEqual(
                imported.role.mission,
                "YAML-like mission without fingerprint metadata.",
            )
            self.assertEqual(imported.profile_revision, 4)
            self.assertFalse(bool((imported.meta or {}).get("bundle_fingerprint")))
            summary = dict(getattr(service, "_identity_import_summary", {}) or {})
            self.assertEqual(summary.get("status"), "skipped_authority")
            self.assertEqual(
                summary.get("existing_source_classification"),
                "legacy-protected",
            )

    def test_identity_bundle_import_skips_yaml_managed_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "identity.db"
            bundle_root = Path(tmp) / "bundles"
            agent_bundle = bundle_root / "agents" / "openminion"
            agent_bundle.mkdir(parents=True)
            (agent_bundle / "AGENT.md").write_text(
                "## Mission\nBundle-owned mission.\n",
                encoding="utf-8",
            )
            (agent_bundle / "SOUL.md").write_text(
                "## Voice\n- Precise\n",
                encoding="utf-8",
            )

            seed_ctl = IdentityCtl(store=SQLiteIdentityStore(sqlite_path=str(db_path)))
            existing = _sample_profile()
            existing.role.mission = "YAML-managed mission."
            existing.profile_revision = 7
            existing.meta = {"source": "yaml"}
            seed_ctl.upsert_profile(existing)

            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.identity.root = str(db_path)
            config.identity.bundle_root = str(bundle_root)
            provider = _CaptureProvider()
            service = AgentService(
                config=config,
                plugins=PluginRegistry([]),
                provider=provider,
                logger=logging.getLogger("openminion.tests.identity.runtime"),
            )

            self.assertIsNotNone(service._identityctl)  # noqa: SLF001
            if service._identityctl is None:  # pragma: no cover
                self.fail("identity runtime should be initialized")
            imported = service._identityctl.get_profile("openminion")
            self.assertIsNotNone(imported)
            if imported is None:  # pragma: no cover
                self.fail("expected profile after startup")
            self.assertEqual(imported.role.mission, "YAML-managed mission.")
            self.assertEqual(imported.profile_revision, 7)
            summary = dict(getattr(service, "_identity_import_summary", {}) or {})
            self.assertEqual(summary.get("status"), "skipped_authority")
            self.assertEqual(summary.get("existing_source_classification"), "yaml")

    def test_identity_bundle_import_skips_default_source_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "identity.db"
            bundle_root = Path(tmp) / "bundles"
            agent_bundle = bundle_root / "agents" / "openminion"
            agent_bundle.mkdir(parents=True)
            (agent_bundle / "AGENT.md").write_text(
                "## Mission\nBundle-owned mission.\n",
                encoding="utf-8",
            )
            (agent_bundle / "SOUL.md").write_text(
                "## Voice\n- Precise\n",
                encoding="utf-8",
            )

            seed_ctl = IdentityCtl(store=SQLiteIdentityStore(sqlite_path=str(db_path)))
            existing = _sample_profile()
            existing.role.mission = "Default fallback mission."
            existing.profile_revision = 5
            existing.meta = {"source": "default"}
            seed_ctl.upsert_profile(existing)

            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.identity.root = str(db_path)
            config.identity.bundle_root = str(bundle_root)
            provider = _CaptureProvider()
            service = AgentService(
                config=config,
                plugins=PluginRegistry([]),
                provider=provider,
                logger=logging.getLogger("openminion.tests.identity.runtime"),
            )

            self.assertIsNotNone(service._identityctl)  # noqa: SLF001
            if service._identityctl is None:  # pragma: no cover
                self.fail("identity runtime should be initialized")
            imported = service._identityctl.get_profile("openminion")
            self.assertIsNotNone(imported)
            if imported is None:  # pragma: no cover
                self.fail("expected profile after startup")
            self.assertEqual(imported.role.mission, "Default fallback mission.")
            self.assertEqual(imported.profile_revision, 5)
            summary = dict(getattr(service, "_identity_import_summary", {}) or {})
            self.assertEqual(summary.get("status"), "skipped_authority")
            self.assertEqual(summary.get("existing_source_classification"), "default")

    def test_identity_bundle_import_refreshes_legacy_bundle_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "identity.db"
            bundle_root = Path(tmp) / "bundles"
            agent_bundle = bundle_root / "agents" / "openminion"
            agent_bundle.mkdir(parents=True)
            (agent_bundle / "AGENT.md").write_text(
                "## Mission\nBundle mission refreshed.\n",
                encoding="utf-8",
            )
            (agent_bundle / "SOUL.md").write_text(
                "## Voice\n- Precise\n",
                encoding="utf-8",
            )

            seed_ctl = IdentityCtl(store=SQLiteIdentityStore(sqlite_path=str(db_path)))
            existing = _sample_profile()
            existing.role.mission = "Legacy bundle mission."
            existing.profile_revision = 3
            existing.meta = {"bundle_fingerprint": "legacy-fingerprint-old"}
            seed_ctl.upsert_profile(existing)

            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.identity.root = str(db_path)
            config.identity.bundle_root = str(bundle_root)
            provider = _CaptureProvider()
            service = AgentService(
                config=config,
                plugins=PluginRegistry([]),
                provider=provider,
                logger=logging.getLogger("openminion.tests.identity.runtime"),
            )

            self.assertIsNotNone(service._identityctl)  # noqa: SLF001
            if service._identityctl is None:  # pragma: no cover
                self.fail("identity runtime should be initialized")
            imported = service._identityctl.get_profile("openminion")
            self.assertIsNotNone(imported)
            if imported is None:  # pragma: no cover
                self.fail("expected profile after bundle refresh")
            self.assertEqual(imported.role.mission, "Bundle mission refreshed.")
            self.assertEqual(imported.profile_revision, 4)
            self.assertEqual(dict(imported.meta or {}).get("source"), "bundle")
            self.assertTrue(bool((imported.meta or {}).get("bundle_fingerprint")))
            summary = dict(getattr(service, "_identity_import_summary", {}) or {})
            self.assertEqual(summary.get("status"), "imported")
            self.assertEqual(
                summary.get("existing_source_classification"),
                "legacy-bundle",
            )

    def test_identity_bundle_import_increments_revision_on_changed_fingerprint(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "identity.db"
            bundle_root = Path(tmp) / "bundles"
            agent_bundle = bundle_root / "agents" / "openminion"
            agent_bundle.mkdir(parents=True)
            agent_md = agent_bundle / "AGENT.md"
            soul_md = agent_bundle / "SOUL.md"
            agent_md.write_text("## Mission\nVersion one mission.\n", encoding="utf-8")
            soul_md.write_text("## Voice\n- Precise\n", encoding="utf-8")

            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.identity.root = str(db_path)
            config.identity.bundle_root = str(bundle_root)
            provider = _CaptureProvider()
            service = AgentService(
                config=config,
                plugins=PluginRegistry([]),
                provider=provider,
                logger=logging.getLogger("openminion.tests.identity.runtime"),
            )

            self.assertIsNotNone(service._identityctl)  # noqa: SLF001
            if service._identityctl is None:  # pragma: no cover
                self.fail("identity runtime should be initialized")
            before = service._identityctl.get_profile("openminion")
            self.assertIsNotNone(before)
            if before is None:  # pragma: no cover
                self.fail("expected imported profile before change")
            self.assertEqual(before.profile_revision, 1)

            agent_md.write_text("## Mission\nVersion two mission.\n", encoding="utf-8")
            imported = service._import_identity_bundle_profile()  # noqa: SLF001
            self.assertTrue(imported)

            after = service._identityctl.get_profile("openminion")
            self.assertIsNotNone(after)
            if after is None:  # pragma: no cover
                self.fail("expected imported profile after change")
            self.assertEqual(after.profile_revision, before.profile_revision + 1)
            self.assertEqual(after.role.mission, "Version two mission.")
            self.assertEqual(dict(after.meta or {}).get("source"), "bundle")

    def test_identity_bundle_import_records_defaulted_field_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "identity.db"
            bundle_root = Path(tmp) / "bundles"
            agent_bundle = bundle_root / "agents" / "openminion"
            agent_bundle.mkdir(parents=True)
            (agent_bundle / "AGENT.md").write_text(
                "## Responsibilities\n- Keep outputs crisp\n",
                encoding="utf-8",
            )
            (agent_bundle / "SOUL.md").write_text(
                "## Values\n- Be clear about uncertainty\n",
                encoding="utf-8",
            )

            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.identity.root = str(db_path)
            config.identity.bundle_root = str(bundle_root)
            provider = _CaptureProvider()
            service = AgentService(
                config=config,
                plugins=PluginRegistry([]),
                provider=provider,
                logger=logging.getLogger("openminion.tests.identity.runtime"),
            )

            self.assertIsNotNone(service._identityctl)  # noqa: SLF001
            if service._identityctl is None:  # pragma: no cover
                self.fail("identity runtime should be initialized")
            profile = service._identityctl.get_profile("openminion")
            self.assertIsNotNone(profile)
            if profile is None:  # pragma: no cover
                self.fail("expected imported profile")

            self.assertTrue(bool(profile.role.mission))
            self.assertEqual(profile.personality.tone, "professional")
            meta = dict(profile.meta or {})
            defaulted = list(meta.get("bundle_import_defaulted_fields", []) or [])
            warnings = list(meta.get("bundle_import_warnings", []) or [])
            self.assertIn("role.mission", defaulted)
            self.assertIn("personality.tone", defaulted)
            self.assertTrue(
                any("default role.mission applied" in str(item) for item in warnings)
            )
            self.assertTrue(
                any(
                    "default personality.tone applied" in str(item) for item in warnings
                )
            )

    def test_startup_bundle_import_skips_default_profile_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "identity.db"
            bundle_root = Path(tmp) / "bundles"
            agent_bundle = bundle_root / "agents" / "openminion"
            agent_bundle.mkdir(parents=True)
            (agent_bundle / "AGENT.md").write_text(
                "## Mission\nBundle mission source.\n",
                encoding="utf-8",
            )
            (agent_bundle / "SOUL.md").write_text(
                "## Voice\n- Technical\n",
                encoding="utf-8",
            )

            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.identity.root = str(db_path)
            config.identity.bundle_root = str(bundle_root)
            provider = _CaptureProvider()

            with (
                patch.dict(
                    "os.environ",
                    {"OPENMINION_IDENTITY_ROOT": "", "OPENMINION_IDENTITY_DB": ""},
                    clear=False,
                ),
                patch(
                    "openminion.services.identity.bootstrap.ensure_default_profile",
                    wraps=ensure_default_profile,
                ) as ensure_mock,
            ):
                service = AgentService(
                    config=config,
                    plugins=PluginRegistry([]),
                    provider=provider,
                    logger=logging.getLogger("openminion.tests.identity.runtime"),
                )

            self.assertEqual(ensure_mock.call_count, 0)
            self.assertIsNotNone(service._identityctl)  # noqa: SLF001
            if service._identityctl is None:  # pragma: no cover
                self.fail("identity runtime should be initialized")
            profile = service._identityctl.get_profile("openminion")
            self.assertIsNotNone(profile)
            if profile is None:  # pragma: no cover
                self.fail("expected profile from bundle import")
            self.assertEqual(profile.role.mission, "Bundle mission source.")
            self.assertEqual(dict(profile.meta or {}).get("source"), "bundle")

    def test_startup_without_bundle_uses_default_profile_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "identity.db"
            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.identity.root = str(db_path)
            config.identity.bundle_root = ""
            provider = _CaptureProvider()

            with (
                patch.dict(
                    "os.environ",
                    {"OPENMINION_IDENTITY_ROOT": "", "OPENMINION_IDENTITY_DB": ""},
                    clear=False,
                ),
                patch(
                    "openminion.services.identity.bootstrap.ensure_default_profile",
                    wraps=ensure_default_profile,
                ) as ensure_mock,
            ):
                service = AgentService(
                    config=config,
                    plugins=PluginRegistry([]),
                    provider=provider,
                    logger=logging.getLogger("openminion.tests.identity.runtime"),
                )

            self.assertEqual(ensure_mock.call_count, 1)
            self.assertIsNotNone(service._identityctl)  # noqa: SLF001
            if service._identityctl is None:  # pragma: no cover
                self.fail("identity runtime should be initialized")
            profile = service._identityctl.get_profile("openminion")
            self.assertIsNotNone(profile)
            if profile is None:  # pragma: no cover
                self.fail("expected fallback default profile")
            self.assertEqual(dict(profile.meta or {}).get("source"), "default")

    def test_startup_fallback_runs_after_yaml_and_bundle_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "identity.db"
            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.identity.root = str(db_path)
            config.identity.bundle_root = ""
            provider = _CaptureProvider()
            events: list[str] = []

            original_sync = AgentService._sync_startup_yaml_profiles
            original_import = AgentService._import_identity_bundle_profile

            def _sync_with_event(service: AgentService) -> dict[str, object]:
                events.append("yaml")
                return original_sync(service)

            def _import_with_event(service: AgentService) -> bool:
                events.append("bundle")
                return original_import(service)

            def _ensure_with_event(*args: object, **kwargs: object) -> None:
                events.append("fallback")
                ensure_default_profile(*args, **kwargs)

            with (
                patch.object(
                    AgentService, "_sync_startup_yaml_profiles", _sync_with_event
                ),
                patch.object(
                    AgentService, "_import_identity_bundle_profile", _import_with_event
                ),
                patch(
                    "openminion.services.identity.bootstrap.ensure_default_profile",
                    side_effect=_ensure_with_event,
                ),
            ):
                AgentService(
                    config=config,
                    plugins=PluginRegistry([]),
                    provider=provider,
                    logger=logging.getLogger("openminion.tests.identity.runtime"),
                )

            self.assertEqual(events, ["yaml", "bundle", "fallback"])

    def test_startup_yaml_sync_runs_before_bundle_import(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            identity_root = Path(tmp) / "identity-root"
            yaml_agent_root = identity_root / "openminion"
            yaml_agent_root.mkdir(parents=True)
            (yaml_agent_root / "profile.yaml").write_text(
                "\n".join(
                    [
                        "agent_id: openminion",
                        "display_name: OpenMinion YAML",
                        "profile_revision: 3",
                        "role:",
                        '  mission: "Mission from YAML startup sync."',
                        "personality:",
                        '  tone: "grounded"',
                        "risk:",
                        "  risk_level: medium",
                        "tool_posture:",
                        "  tool_use: restricted",
                    ]
                ),
                encoding="utf-8",
            )

            bundle_root = Path(tmp) / "bundles"
            agent_bundle = bundle_root / "agents" / "openminion"
            agent_bundle.mkdir(parents=True)
            (agent_bundle / "AGENT.md").write_text(
                "## Mission\nMission from bundle import.\n",
                encoding="utf-8",
            )
            (agent_bundle / "SOUL.md").write_text(
                "## Voice\n- Technical\n",
                encoding="utf-8",
            )

            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.identity.root = str(identity_root)
            config.identity.db_path = str(Path(tmp) / "identity.db")
            config.identity.bundle_root = str(bundle_root)
            provider = _CaptureProvider()

            with (
                patch.dict(
                    "os.environ",
                    {"OPENMINION_IDENTITY_ROOT": "", "OPENMINION_IDENTITY_DB": ""},
                    clear=False,
                ),
                patch(
                    "openminion.services.identity.bootstrap.ensure_default_profile",
                    wraps=ensure_default_profile,
                ) as ensure_mock,
            ):
                service = AgentService(
                    config=config,
                    plugins=PluginRegistry([]),
                    provider=provider,
                    logger=logging.getLogger("openminion.tests.identity.runtime"),
                )

            self.assertEqual(ensure_mock.call_count, 0)
            self.assertIsNotNone(service._identityctl)  # noqa: SLF001
            if service._identityctl is None:  # pragma: no cover
                self.fail("identity runtime should be initialized")

            profile = service._identityctl.get_profile("openminion")  # noqa: SLF001
            self.assertIsNotNone(profile)
            if profile is None:  # pragma: no cover
                self.fail("expected YAML-synced profile")
            self.assertEqual(profile.role.mission, "Mission from YAML startup sync.")
            self.assertEqual(dict(profile.meta or {}).get("source"), "yaml")

            yaml_summary = dict(
                getattr(service, "_identity_yaml_sync_summary", {}) or {}
            )
            self.assertEqual(yaml_summary.get("status"), "synced")
            self.assertEqual(yaml_summary.get("upserted_profiles_count"), 1)

            import_summary = dict(
                getattr(service, "_identity_import_summary", {}) or {}
            )
            self.assertEqual(import_summary.get("status"), "skipped_authority")
            self.assertEqual(
                import_summary.get("existing_source_classification"), "yaml"
            )

    def test_startup_yaml_sync_prevents_default_fallback_when_profile_exists(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            identity_root = Path(tmp) / "identity-root"
            yaml_agent_root = identity_root / "openminion"
            yaml_agent_root.mkdir(parents=True)
            (yaml_agent_root / "profile.yaml").write_text(
                "\n".join(
                    [
                        "agent_id: openminion",
                        "display_name: OpenMinion YAML",
                        "profile_revision: 2",
                        "role:",
                        '  mission: "Mission from YAML only startup."',
                        "personality:",
                        '  tone: "steady"',
                        "risk:",
                        "  risk_level: medium",
                        "tool_posture:",
                        "  tool_use: restricted",
                    ]
                ),
                encoding="utf-8",
            )

            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.identity.root = str(identity_root)
            config.identity.db_path = str(Path(tmp) / "identity.db")
            config.identity.bundle_root = ""
            provider = _CaptureProvider()

            with (
                patch.dict(
                    "os.environ",
                    {"OPENMINION_IDENTITY_ROOT": "", "OPENMINION_IDENTITY_DB": ""},
                    clear=False,
                ),
                patch(
                    "openminion.services.identity.bootstrap.ensure_default_profile",
                    wraps=ensure_default_profile,
                ) as ensure_mock,
            ):
                service = AgentService(
                    config=config,
                    plugins=PluginRegistry([]),
                    provider=provider,
                    logger=logging.getLogger("openminion.tests.identity.runtime"),
                )

            self.assertEqual(ensure_mock.call_count, 0)
            self.assertIsNotNone(service._identityctl)  # noqa: SLF001
            if service._identityctl is None:  # pragma: no cover
                self.fail("identity runtime should be initialized")
            profile = service._identityctl.get_profile("openminion")  # noqa: SLF001
            self.assertIsNotNone(profile)
            if profile is None:  # pragma: no cover
                self.fail("expected YAML-synced profile")
            self.assertEqual(profile.role.mission, "Mission from YAML only startup.")
            self.assertEqual(dict(profile.meta or {}).get("source"), "yaml")

            yaml_summary = dict(
                getattr(service, "_identity_yaml_sync_summary", {}) or {}
            )
            self.assertEqual(yaml_summary.get("status"), "synced")

            import_summary = dict(
                getattr(service, "_identity_import_summary", {}) or {}
            )
            self.assertEqual(import_summary.get("status"), "bundle_invalid")

    def test_startup_logs_identity_sync_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "identity.db"
            bundle_root = Path(tmp) / "bundles"
            agent_bundle = bundle_root / "agents" / "openminion"
            agent_bundle.mkdir(parents=True)
            (agent_bundle / "AGENT.md").write_text(
                "## Responsibilities\n- Keep outputs crisp\n",
                encoding="utf-8",
            )
            (agent_bundle / "SOUL.md").write_text(
                "## Values\n- Be explicit\n",
                encoding="utf-8",
            )

            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.identity.root = str(db_path)
            config.identity.bundle_root = str(bundle_root)
            provider = _CaptureProvider()
            logger_name = "openminion.tests.identity.runtime.startup"

            with self.assertLogs(logger_name, level="INFO") as captured:
                AgentService(
                    config=config,
                    plugins=PluginRegistry([]),
                    provider=provider,
                    logger=logging.getLogger(logger_name),
                )

            startup_lines = [
                line for line in captured.output if "identity startup sync" in line
            ]
            self.assertTrue(startup_lines)
            summary = startup_lines[-1]
            self.assertIn("status=imported", summary)
            self.assertIn("fallback_default=False", summary)
            self.assertRegex(summary, r"defaulted_fields=\d+")
            self.assertRegex(summary, r"warnings=\d+")

    def test_identity_budget_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "identity.db"
            ctl = IdentityCtl(store=SQLiteIdentityStore(sqlite_path=str(db_path)))
            verbose = _sample_profile()
            verbose.role.hard_constraints = [
                f"MUST NOT do unsafe thing {idx}" for idx in range(25)
            ]
            verbose.personality.interaction_style = [
                f"style-{idx}" for idx in range(30)
            ]
            ctl.upsert_profile(verbose)

            snippet = ctl.render("openminion", purpose="act", max_tokens=50)
            self.assertLessEqual(snippet.budget.used_tokens, 50)

    def test_identity_metadata_in_response(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "identity.db"
            ctl = IdentityCtl(store=SQLiteIdentityStore(sqlite_path=str(db_path)))
            ctl.upsert_profile(_sample_profile())

            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.identity.root = str(db_path)
            provider = _CaptureProvider()
            service = AgentService(
                config=config,
                plugins=PluginRegistry([]),
                provider=provider,
                logger=logging.getLogger("openminion.tests.identity.runtime"),
            )
            response = asyncio.run(
                service.run_turn(Message(channel="console", target="cli", body="hello"))
            )
            self.assertNotEqual(
                response.metadata.get("identity_profile_version", "none"),
                "none",
            )

    def test_identity_runtime_uses_inbound_purpose_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "identity.db"
            ctl = IdentityCtl(store=SQLiteIdentityStore(sqlite_path=str(db_path)))
            ctl.upsert_profile(_sample_profile())

            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.identity.root = str(db_path)
            provider = _CaptureProvider()
            service = AgentService(
                config=config,
                plugins=PluginRegistry([]),
                provider=provider,
                logger=logging.getLogger("openminion.tests.identity.runtime"),
            )
            response = asyncio.run(
                service.run_turn(
                    Message(
                        channel="console",
                        target="cli",
                        body="hello",
                        metadata={"purpose": "plan"},
                    )
                )
            )
            self.assertEqual(response.metadata.get("identity_purpose"), "plan")

    def test_identity_runtime_normalizes_alias_purpose_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "identity.db"
            ctl = IdentityCtl(store=SQLiteIdentityStore(sqlite_path=str(db_path)))
            ctl.upsert_profile(_sample_profile())

            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.identity.root = str(db_path)
            provider = _CaptureProvider()
            service = AgentService(
                config=config,
                plugins=PluginRegistry([]),
                provider=provider,
                logger=logging.getLogger("openminion.tests.identity.runtime"),
            )
            cases = (
                ({"purpose": "decision"}, "decide"),
                ({"turn_purpose": "verify"}, "judge"),
                ({"phase": "follow-up"}, "act"),
            )
            for metadata, expected in cases:
                with self.subTest(metadata=metadata, expected=expected):
                    response = asyncio.run(
                        service.run_turn(
                            Message(
                                channel="console",
                                target="cli",
                                body="hello",
                                metadata=metadata,
                            )
                        )
                    )
                    self.assertEqual(
                        response.metadata.get("identity_purpose"), expected
                    )

    def test_identity_runtime_preserves_metadata_key_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "identity.db"
            ctl = IdentityCtl(store=SQLiteIdentityStore(sqlite_path=str(db_path)))
            ctl.upsert_profile(_sample_profile())

            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.identity.root = str(db_path)
            provider = _CaptureProvider()
            service = AgentService(
                config=config,
                plugins=PluginRegistry([]),
                provider=provider,
                logger=logging.getLogger("openminion.tests.identity.runtime"),
            )
            response = asyncio.run(
                service.run_turn(
                    Message(
                        channel="console",
                        target="cli",
                        body="hello",
                        metadata={
                            "identity_purpose": "verify",
                            "turn_purpose": "planning",
                            "purpose": "decision",
                        },
                    )
                )
            )
            self.assertEqual(response.metadata.get("identity_purpose"), "judge")

    def test_identity_runtime_unknown_purpose_falls_back_to_act(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "identity.db"
            ctl = IdentityCtl(store=SQLiteIdentityStore(sqlite_path=str(db_path)))
            ctl.upsert_profile(_sample_profile())

            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.identity.root = str(db_path)
            provider = _CaptureProvider()
            service = AgentService(
                config=config,
                plugins=PluginRegistry([]),
                provider=provider,
                logger=logging.getLogger("openminion.tests.identity.runtime"),
            )
            response = asyncio.run(
                service.run_turn(
                    Message(
                        channel="console",
                        target="cli",
                        body="hello",
                        metadata={"turn_purpose": "unknown-phase"},
                    )
                )
            )
            self.assertEqual(response.metadata.get("identity_purpose"), "act")

    def test_identity_runtime_uses_config_backed_plan_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "identity.db"
            ctl = IdentityCtl(store=SQLiteIdentityStore(sqlite_path=str(db_path)))
            ctl.upsert_profile(_sample_profile())

            fake_cfg = SimpleNamespace(
                rendering=SimpleNamespace(
                    default_budgets={
                        "act": SimpleNamespace(max_tokens=111),
                        "plan": SimpleNamespace(max_tokens=333),
                    }
                )
            )
            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.identity.root = str(db_path)
            provider = _CaptureProvider()

            with patch(
                "openminion.services.agent.identity_binding.from_base_config",
                return_value=fake_cfg,
            ):
                service = AgentService(
                    config=config,
                    plugins=PluginRegistry([]),
                    provider=provider,
                    logger=logging.getLogger("openminion.tests.identity.runtime"),
                )
                response = asyncio.run(
                    service.run_turn(
                        Message(
                            channel="console",
                            target="cli",
                            body="hello",
                            metadata={"purpose": "plan"},
                        )
                    )
                )

            self.assertEqual(response.metadata.get("identity_purpose"), "plan")
            self.assertEqual(response.metadata.get("identity_budget_max_tokens"), "333")

    def test_identity_runtime_warns_once_when_llm_policy_ref_unenforced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "identity.db"
            ctl = IdentityCtl(store=SQLiteIdentityStore(sqlite_path=str(db_path)))
            profile = _sample_profile()
            profile.llm_policy_ref = "policy://strict-guard"
            ctl.upsert_profile(profile)

            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.identity.root = str(db_path)
            provider = _CaptureProvider()
            logger_name = "openminion.tests.identity.runtime.llm_policy_ref"

            with self.assertLogs(logger_name, level="WARNING") as captured:
                service = AgentService(
                    config=config,
                    plugins=PluginRegistry([]),
                    provider=provider,
                    logger=logging.getLogger(logger_name),
                )
                first = asyncio.run(
                    service.run_turn(
                        Message(channel="console", target="cli", body="hello")
                    )
                )
                second = asyncio.run(
                    service.run_turn(
                        Message(channel="console", target="cli", body="hello again")
                    )
                )

            warning_lines = [
                line
                for line in captured.output
                if "llm_policy_ref present but unenforced" in line
            ]
            self.assertEqual(len(warning_lines), 1)
            self.assertEqual(
                first.metadata.get("identity_llm_policy_ref"),
                "policy://strict-guard",
            )
            self.assertEqual(
                first.metadata.get("identity_llm_policy_ref_enforced"),
                "false",
            )
            self.assertEqual(
                first.metadata.get("identity_llm_policy_ref_warning"),
                "true",
            )
            self.assertEqual(
                second.metadata.get("identity_llm_policy_ref_warning"),
                "true",
            )

    def test_identity_bundle_client_instantiates(self) -> None:
        client = IdentityBundleClient(agent_id="openminion", root=".")
        self.assertIsNotNone(client)

    def test_identity_bundle_client_constructor_agent_controls_bundle_source(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            alpha_root = root / "agents" / "alpha"
            beta_root = root / "agents" / "beta"
            alpha_root.mkdir(parents=True)
            beta_root.mkdir(parents=True)
            (alpha_root / "AGENT.md").write_text("alpha-agent", encoding="utf-8")
            (alpha_root / "SOUL.md").write_text("alpha-soul", encoding="utf-8")
            (beta_root / "AGENT.md").write_text("beta-agent", encoding="utf-8")
            (beta_root / "SOUL.md").write_text("beta-soul", encoding="utf-8")

            client = IdentityBundleClient(agent_id="alpha", root=root)
            snippet = client.render(agent_id="beta", purpose="act", max_tokens=200)

            # Compatibility lock: rendered snippet agent_id comes from render() call,
            # but markdown source remains bound to constructor agent_id.
            self.assertEqual(snippet.agent_id, "beta")
            self.assertIn("alpha-agent", snippet.text)
            self.assertNotIn("beta-agent", snippet.text)

    def test_identity_bundle_client_keeps_profile_version_when_files_change(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle_root = root / "agents" / "openminion"
            bundle_root.mkdir(parents=True)
            (bundle_root / "AGENT.md").write_text("agent-v1", encoding="utf-8")
            (bundle_root / "SOUL.md").write_text("soul-v1", encoding="utf-8")

            client = IdentityBundleClient(agent_id="openminion", root=root)
            first = client.render(agent_id="openminion", purpose="act", max_tokens=200)

            (bundle_root / "AGENT.md").write_text("agent-v2", encoding="utf-8")
            second = client.render(agent_id="openminion", purpose="act", max_tokens=200)

            # Compatibility lock: rendered text reflects latest file content,
            # while cached bundle fingerprint/profile_version remains unchanged.
            self.assertIn("agent-v2", second.text)
            self.assertEqual(second.profile_version, first.profile_version)
            self.assertEqual(second.render_version, first.render_version)

    def test_agent_service_bundle_root_only_activates_identity_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle_root = Path(tmp) / "bundle"
            bundle_root.mkdir(parents=True)

            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.identity.root = ""
            provider = _CaptureProvider()

            with patch.dict(
                "os.environ",
                {
                    "OPENMINION_IDENTITY_ROOT": str(bundle_root),
                    "OPENMINION_IDENTITY_DB": "",
                },
                clear=False,
            ):
                service = AgentService(
                    config=config,
                    plugins=PluginRegistry([]),
                    provider=provider,
                    logger=logging.getLogger("openminion.tests.identity.runtime"),
                )
                self.assertIsNotNone(service._identityctl)  # noqa: SLF001
                response = asyncio.run(
                    service.run_turn(
                        Message(channel="console", target="cli", body="hello")
                    )
                )

            self.assertEqual(response.text, "ok")
            self.assertNotEqual(
                response.metadata.get("identity_profile_version", "none"),
                "none",
            )

    def test_resolve_bundle_root_prefers_env_over_config_and_legacy(self) -> None:
        config = OpenMinionConfig()
        _csc_install_default_agent(config)  # type: ignore[attr-defined]
        config.identity.bundle_root = "/tmp/config-bundle-root"
        config.identity.root = "/tmp/legacy-bundle-root"
        service = self._build_service_without_identity_init(config=config)

        with patch.dict(
            "os.environ",
            {"OPENMINION_IDENTITY_ROOT": "/tmp/env-bundle-root"},
            clear=False,
        ):
            resolved = service._resolve_bundle_root()  # noqa: SLF001

        self.assertEqual(resolved, str(Path("/tmp/env-bundle-root").resolve()))

    def test_resolve_bundle_root_prefers_config_over_legacy(self) -> None:
        config = OpenMinionConfig()
        _csc_install_default_agent(config)  # type: ignore[attr-defined]
        config.identity.bundle_root = "/tmp/config-bundle-root"
        config.identity.root = "/tmp/legacy-bundle-root"
        service = self._build_service_without_identity_init(config=config)

        with patch.dict("os.environ", {"OPENMINION_IDENTITY_ROOT": ""}, clear=False):
            resolved = service._resolve_bundle_root()  # noqa: SLF001

        self.assertEqual(resolved, str(Path("/tmp/config-bundle-root").resolve()))

    def test_resolve_bundle_root_uses_legacy_alias_when_bundle_root_missing(
        self,
    ) -> None:
        config = OpenMinionConfig()
        _csc_install_default_agent(config)  # type: ignore[attr-defined]
        config.identity.bundle_root = ""
        config.identity.root = "/tmp/legacy-bundle-root"
        service = self._build_service_without_identity_init(config=config)

        with patch.dict("os.environ", {"OPENMINION_IDENTITY_ROOT": ""}, clear=False):
            resolved = service._resolve_bundle_root()  # noqa: SLF001

        self.assertEqual(resolved, str(Path("/tmp/legacy-bundle-root").resolve()))

    def test_resolve_bundle_root_ignores_legacy_db_alias_path(self) -> None:
        config = OpenMinionConfig()
        _csc_install_default_agent(config)  # type: ignore[attr-defined]
        config.identity.bundle_root = ""
        config.identity.root = "/tmp/identity.db"
        service = self._build_service_without_identity_init(config=config)

        with patch.dict("os.environ", {"OPENMINION_IDENTITY_ROOT": ""}, clear=False):
            resolved = service._resolve_bundle_root()  # noqa: SLF001

        self.assertEqual(resolved, "")

    def test_sync_startup_yaml_profiles_missing_root(self) -> None:
        config = OpenMinionConfig()
        _csc_install_default_agent(config)  # type: ignore[attr-defined]
        config.identity.root = "/tmp/identity-startup-missing"
        service = self._build_service_without_identity_init(config=config)
        with tempfile.TemporaryDirectory() as tmp:
            service._identityctl = IdentityCtl(  # noqa: SLF001
                store=SQLiteIdentityStore(sqlite_path=str(Path(tmp) / "identity.db"))
            )
            summary = service._sync_startup_yaml_profiles()  # noqa: SLF001
        self.assertEqual(summary.get("status"), "identity_root_missing")
        self.assertEqual(summary.get("profile_files_count"), 0)

    def test_sync_startup_yaml_profiles_empty_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "identity-root"
            root.mkdir(parents=True)
            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.identity.root = str(root)
            service = self._build_service_without_identity_init(config=config)
            service._identityctl = IdentityCtl(  # noqa: SLF001
                store=SQLiteIdentityStore(sqlite_path=str(Path(tmp) / "identity.db"))
            )
            summary = service._sync_startup_yaml_profiles()  # noqa: SLF001
        self.assertEqual(summary.get("status"), "no_yaml_profiles")
        self.assertEqual(summary.get("profile_files_count"), 0)

    def test_sync_startup_yaml_profiles_multi_agent_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "identity-root"
            agent_a = root / "agent-a"
            agent_b = root / "agent-b"
            agent_a.mkdir(parents=True)
            agent_b.mkdir(parents=True)
            (agent_a / "profile.yaml").write_text(
                "\n".join(
                    [
                        "agent_id: agent-a",
                        "display_name: Agent A",
                        "profile_revision: 1",
                        "role:",
                        '  mission: "Mission A"',
                        "personality:",
                        '  tone: "direct"',
                        "risk:",
                        "  risk_level: medium",
                        "tool_posture:",
                        "  tool_use: restricted",
                    ]
                ),
                encoding="utf-8",
            )
            (agent_b / "profile.yaml").write_text(
                "\n".join(
                    [
                        "agent_id: agent-b",
                        "display_name: Agent B",
                        "profile_revision: 1",
                        "role:",
                        '  mission: "Mission B"',
                        "personality:",
                        '  tone: "focused"',
                        "risk:",
                        "  risk_level: medium",
                        "tool_posture:",
                        "  tool_use: restricted",
                    ]
                ),
                encoding="utf-8",
            )

            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.identity.root = str(root)
            service = self._build_service_without_identity_init(config=config)
            service._identityctl = IdentityCtl(  # noqa: SLF001
                store=SQLiteIdentityStore(sqlite_path=str(Path(tmp) / "identity.db"))
            )
            summary = service._sync_startup_yaml_profiles()  # noqa: SLF001

            self.assertEqual(summary.get("status"), "synced")
            self.assertEqual(summary.get("profile_files_count"), 2)
            self.assertEqual(summary.get("upserted_profiles_count"), 2)
            self.assertEqual(summary.get("upserted_profiles"), ["agent-a", "agent-b"])

            profile_a = service._identityctl.get_profile("agent-a")  # noqa: SLF001
            profile_b = service._identityctl.get_profile("agent-b")  # noqa: SLF001
            self.assertIsNotNone(profile_a)
            self.assertIsNotNone(profile_b)
            if profile_a is None or profile_b is None:  # pragma: no cover
                self.fail("expected synced YAML profiles")
            self.assertEqual(dict(profile_a.meta or {}).get("source"), "yaml")
            self.assertEqual(dict(profile_b.meta or {}).get("source"), "yaml")
            self.assertTrue((agent_a / "AGENT.md").is_file())
            self.assertTrue((agent_a / "SOUL.md").is_file())
            self.assertTrue((agent_a / "README.md").is_file())
            self.assertTrue((agent_b / "AGENT.md").is_file())
            self.assertTrue((agent_b / "SOUL.md").is_file())
            self.assertTrue((agent_b / "README.md").is_file())

    def test_sync_startup_yaml_profiles_skips_unchanged_profile_churn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "identity-root"
            agent_root = root / "openminion"
            agent_root.mkdir(parents=True)
            (agent_root / "profile.yaml").write_text(
                "\n".join(
                    [
                        "agent_id: openminion",
                        "display_name: OpenMinion",
                        "profile_revision: 1",
                        "role:",
                        '  mission: "Stable YAML mission"',
                        "personality:",
                        '  tone: "steady"',
                        "risk:",
                        "  risk_level: medium",
                        "tool_posture:",
                        "  tool_use: restricted",
                    ]
                ),
                encoding="utf-8",
            )

            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.identity.root = str(root)
            service = self._build_service_without_identity_init(config=config)
            store = SQLiteIdentityStore(sqlite_path=str(Path(tmp) / "identity.db"))
            service._identityctl = IdentityCtl(store=store)  # noqa: SLF001

            first_summary = service._sync_startup_yaml_profiles()  # noqa: SLF001
            self.assertEqual(first_summary.get("status"), "synced")
            self.assertEqual(first_summary.get("upserted_profiles_count"), 1)

            first_row = store.get_profile("openminion")
            self.assertIsNotNone(first_row)
            if first_row is None:  # pragma: no cover
                self.fail("expected profile after first startup sync")

            time.sleep(0.01)
            second_summary = service._sync_startup_yaml_profiles()  # noqa: SLF001
            self.assertEqual(second_summary.get("status"), "synced")
            self.assertEqual(second_summary.get("profile_files_count"), 1)
            self.assertEqual(second_summary.get("upserted_profiles_count"), 0)
            self.assertEqual(second_summary.get("upserted_profiles"), [])

            second_row = store.get_profile("openminion")
            self.assertIsNotNone(second_row)
            if second_row is None:  # pragma: no cover
                self.fail("expected profile after second startup sync")
            self.assertEqual(second_row.updated_at, first_row.updated_at)

    def test_resolve_identity_db_path_defaults_to_runtime_identity_db_name(
        self,
    ) -> None:
        config = OpenMinionConfig()
        _csc_install_default_agent(config)  # type: ignore[attr-defined]
        config.identity.db_path = ""
        config.identity.root = ""
        service = self._build_service_without_identity_init(config=config)

        with patch.dict(
            "os.environ",
            {
                "OPENMINION_IDENTITY_DB": "",
                "OPENMINION_DATA_ROOT": "/tmp/runtime-data-root",
            },
            clear=False,
        ):
            resolved = service._resolve_identity_db_path()  # noqa: SLF001

        self.assertEqual(
            resolved,
            str(
                (Path("/tmp/runtime-data-root") / "identity" / "identity.db").resolve()
            ),
        )

    def test_resolve_identity_db_path_legacy_root_fallback_requires_db_suffix(
        self,
    ) -> None:
        config = OpenMinionConfig()
        _csc_install_default_agent(config)  # type: ignore[attr-defined]
        config.identity.db_path = ""
        config.identity.root = "/tmp/legacy-identity-root"
        service = self._build_service_without_identity_init(config=config)

        with patch.dict(
            "os.environ",
            {
                "OPENMINION_IDENTITY_DB": "",
                "OPENMINION_DATA_ROOT": "/tmp/runtime-data-root",
            },
            clear=False,
        ):
            resolved = service._resolve_identity_db_path()  # noqa: SLF001

        self.assertEqual(
            resolved,
            str(
                (Path("/tmp/runtime-data-root") / "identity" / "identity.db").resolve()
            ),
        )

    def test_resolve_identity_db_path_legacy_root_db_alias_is_honored(self) -> None:
        config = OpenMinionConfig()
        _csc_install_default_agent(config)  # type: ignore[attr-defined]
        config.identity.db_path = ""
        config.identity.root = "legacy/identity.db"
        service = self._build_service_without_identity_init(config=config)

        with patch.dict(
            "os.environ",
            {
                "OPENMINION_IDENTITY_DB": "",
                "OPENMINION_DATA_ROOT": "/tmp/runtime-data-root",
            },
            clear=False,
        ):
            resolved = service._resolve_identity_db_path()  # noqa: SLF001

        self.assertEqual(
            resolved,
            str((Path("/tmp/runtime-data-root") / "legacy" / "identity.db").resolve()),
        )

    def test_classify_profile_source_missing_source_with_fingerprint_is_legacy_bundle(
        self,
    ) -> None:
        service = self._build_service_without_identity_init(config=OpenMinionConfig())
        profile = _sample_profile()
        profile.meta = {"bundle_fingerprint": "legacy-fp-001"}

        classification = service._classify_profile_source(profile)  # noqa: SLF001

        self.assertEqual(classification, "legacy-bundle")

    def test_classify_profile_source_missing_profile_is_missing(self) -> None:
        service = self._build_service_without_identity_init(config=OpenMinionConfig())
        classification = service._classify_profile_source(None)  # noqa: SLF001
        self.assertEqual(classification, "missing")

    def test_classify_profile_source_missing_source_without_fingerprint_is_legacy_protected(
        self,
    ) -> None:
        service = self._build_service_without_identity_init(config=OpenMinionConfig())
        profile = _sample_profile()
        profile.meta = {}

        classification = service._classify_profile_source(profile)  # noqa: SLF001

        self.assertEqual(classification, "legacy-protected")

    def test_classify_profile_source_preserves_explicit_source(self) -> None:
        service = self._build_service_without_identity_init(config=OpenMinionConfig())
        profile = _sample_profile()
        profile.meta = {"source": "yaml"}

        classification = service._classify_profile_source(profile)  # noqa: SLF001

        self.assertEqual(classification, "yaml")
