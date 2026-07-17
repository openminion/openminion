from __future__ import annotations

import unittest
from typing import Any

from pydantic import BaseModel, ConfigDict

from openminion.modules.tool.contracts.manifest import (
    ModelToolDef,
    RuntimeBindingDef,
    ToolBindingManifest,
)
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.exposure import ToolExposureProfile
from openminion.modules.tool.framework import (
    GeneratedRegistrar,
    ToolDecl,
    ToolFamilySpec,
    build_registrar,
    derive_manifest,
    derive_model_tool_id,
    derive_runtime_binding_id,
    derive_tool_specs,
)
from openminion.modules.tool.registry import ToolRegistry
from openminion.modules.tool.runtime.registrar import ToolModuleRegistrar


class _StrictArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _AlphaArgs(_StrictArgs):
    label: str


class _BetaArgs(_StrictArgs):
    count: int = 0


class _GammaArgs(_StrictArgs):
    pass


def _h_alpha(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    return {"echo": args.get("label", "")}


def _h_beta(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    return {"count": args.get("count", 0)}


def _h_gamma(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    return {"ok": True}


FIXTURE_FAMILY = ToolFamilySpec(
    module_id="fixture",
    min_scope_default="WRITE_SAFE",
    common_tags=("plugin", "fixture"),
    common_capabilities=("write_safe", "fixture"),
    tools=(
        ToolDecl(
            name="fixture.alpha",
            args_model=_AlphaArgs,
            handler=_h_alpha,
            description="Alpha tool — uses family defaults.",
            idempotent=False,
        ),
        ToolDecl(
            name="fixture.beta",
            args_model=_BetaArgs,
            handler=_h_beta,
            description="Beta tool — declares READ_ONLY override.",
            min_scope="READ_ONLY",
            idempotent=True,
            tags=("beta_extra",),
            capabilities=("read_only",),
        ),
        ToolDecl(
            name="fixture.gamma",
            args_model=_GammaArgs,
            handler=_h_gamma,
            description="Gamma tool — marked dangerous.",
            dangerous=True,
            aliases=("fixture.gamma_alias",),
        ),
    ),
)


class DerivationHelperTests(unittest.TestCase):
    def test_model_tool_id_is_the_tool_name(self) -> None:
        self.assertEqual(derive_model_tool_id("plan.set"), "plan.set")
        self.assertEqual(derive_model_tool_id("git.status"), "git.status")
        self.assertEqual(derive_model_tool_id("fixture.alpha"), "fixture.alpha")

    def test_runtime_binding_id_has_runtime_prefix(self) -> None:
        self.assertEqual(derive_runtime_binding_id("plan.set"), "runtime.plan.set")
        self.assertEqual(derive_runtime_binding_id("git.status"), "runtime.git.status")


class DeriveToolSpecsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.specs = derive_tool_specs(FIXTURE_FAMILY)
        self.by_name = {spec.name: spec for spec in self.specs}

    def test_emits_one_spec_per_tool(self) -> None:
        self.assertEqual(
            {spec.name for spec in self.specs},
            {"fixture.alpha", "fixture.beta", "fixture.gamma"},
        )

    def test_inherits_family_min_scope_default(self) -> None:
        self.assertEqual(self.by_name["fixture.alpha"].min_scope, "WRITE_SAFE")
        self.assertEqual(self.by_name["fixture.gamma"].min_scope, "WRITE_SAFE")

    def test_per_tool_min_scope_override_wins(self) -> None:
        self.assertEqual(self.by_name["fixture.beta"].min_scope, "READ_ONLY")

    def test_family_tags_appear_before_per_tool_tags(self) -> None:
        self.assertEqual(self.by_name["fixture.alpha"].tags, ("plugin", "fixture"))
        self.assertEqual(
            self.by_name["fixture.beta"].tags,
            ("plugin", "fixture", "beta_extra"),
        )

    def test_family_capabilities_appear_before_per_tool_capabilities(self) -> None:
        self.assertEqual(
            self.by_name["fixture.alpha"].capabilities,
            ("write_safe", "fixture"),
        )
        self.assertEqual(
            self.by_name["fixture.beta"].capabilities,
            ("write_safe", "fixture", "read_only"),
        )

    def test_dangerous_flag_propagated(self) -> None:
        self.assertFalse(self.by_name["fixture.alpha"].dangerous)
        self.assertTrue(self.by_name["fixture.gamma"].dangerous)

    def test_idempotent_flag_propagated(self) -> None:
        self.assertFalse(self.by_name["fixture.alpha"].idempotent)
        self.assertTrue(self.by_name["fixture.beta"].idempotent)

    def test_args_model_propagated(self) -> None:
        self.assertIs(self.by_name["fixture.alpha"].args_model, _AlphaArgs)
        self.assertIs(self.by_name["fixture.beta"].args_model, _BetaArgs)
        self.assertIs(self.by_name["fixture.gamma"].args_model, _GammaArgs)

    def test_handler_propagated(self) -> None:
        self.assertIs(self.by_name["fixture.alpha"].handler, _h_alpha)
        self.assertIs(self.by_name["fixture.beta"].handler, _h_beta)
        self.assertIs(self.by_name["fixture.gamma"].handler, _h_gamma)


class DeriveManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = derive_manifest(FIXTURE_FAMILY)
        self.model_tools_by_id = {
            entry.model_tool_id: entry for entry in self.manifest.model_tools
        }
        self.runtime_bindings_by_id = {
            entry.runtime_binding_id: entry for entry in self.manifest.runtime_bindings
        }

    def test_manifest_is_a_toolbindingmanifest(self) -> None:
        self.assertIsInstance(self.manifest, ToolBindingManifest)
        self.assertEqual(self.manifest.module_id, "fixture")

    def test_one_model_tool_def_per_tool(self) -> None:
        self.assertEqual(
            set(self.model_tools_by_id),
            {"fixture.alpha", "fixture.beta", "fixture.gamma"},
        )
        for entry in self.manifest.model_tools:
            self.assertIsInstance(entry, ModelToolDef)

    def test_model_tool_descriptions_propagated(self) -> None:
        self.assertIn(
            "uses family defaults",
            self.model_tools_by_id["fixture.alpha"].description,
        )

    def test_aliases_propagated(self) -> None:
        self.assertEqual(
            self.model_tools_by_id["fixture.gamma"].aliases,
            ("fixture.gamma_alias",),
        )

    def test_parameters_left_empty_so_args_model_is_canonical(self) -> None:
        for entry in self.manifest.model_tools:
            self.assertEqual(entry.parameters, {})

    def test_one_runtime_binding_per_tool(self) -> None:
        self.assertEqual(
            set(self.runtime_bindings_by_id),
            {"runtime.fixture.alpha", "runtime.fixture.beta", "runtime.fixture.gamma"},
        )
        for entry in self.manifest.runtime_bindings:
            self.assertIsInstance(entry, RuntimeBindingDef)

    def test_runtime_binding_points_at_its_own_tool(self) -> None:
        binding = self.runtime_bindings_by_id["runtime.fixture.alpha"]
        self.assertEqual(binding.model_tool_id, "fixture.alpha")
        self.assertEqual(binding.runtime_candidates, ("fixture.alpha",))


class BuildRegistrarTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registrar = build_registrar(FIXTURE_FAMILY)

    def test_returns_a_generatedregistrar(self) -> None:
        self.assertIsInstance(self.registrar, GeneratedRegistrar)

    def test_conforms_to_toolmoduleregistrar_protocol(self) -> None:
        self.assertIsInstance(self.registrar, ToolModuleRegistrar)

    def test_module_id_and_is_provider_only_match_family(self) -> None:
        self.assertEqual(self.registrar.module_id, "fixture")
        self.assertFalse(self.registrar.is_provider_only)

    def test_register_adds_all_tools_to_registry(self) -> None:
        registry = ToolRegistry([])
        self.registrar.register(registry, ctx=None)
        names = set(registry.list().keys())
        self.assertEqual(names, {"fixture.alpha", "fixture.beta", "fixture.gamma"})

    def test_get_manifest_returns_derived_manifest_shape(self) -> None:
        manifest = self.registrar.get_manifest(ctx=None)
        self.assertIsInstance(manifest, ToolBindingManifest)
        self.assertEqual(manifest.module_id, "fixture")
        self.assertEqual(len(manifest.model_tools), 3)
        self.assertEqual(len(manifest.runtime_bindings), 3)

    def test_register_adds_family_owned_exposure_profiles(self) -> None:
        profile = ToolExposureProfile(
            profile_id="fixture_alpha",
            title="Fixture alpha",
            summary="Expose alpha for a focused fixture.",
            tool_names=frozenset({"fixture.alpha"}),
        )
        family = ToolFamilySpec(
            module_id="profiled_fixture",
            tools=(FIXTURE_FAMILY.tools[0],),
            exposure_profiles=(profile,),
        )
        registry = ToolRegistry([])

        build_registrar(family).register(registry, ctx=None)

        self.assertEqual(registry.exposure_service.profile("fixture_alpha"), profile)

    def test_family_profile_cannot_claim_another_family_tool(self) -> None:
        profile = ToolExposureProfile(
            profile_id="external",
            title="External",
            summary="Invalid external ownership.",
            tool_names=frozenset({"other.read"}),
        )

        with self.assertRaises(ToolRuntimeError):
            ToolFamilySpec(
                module_id="fixture",
                tools=(FIXTURE_FAMILY.tools[0],),
                exposure_profiles=(profile,),
            )


class ProviderOnlyFamilyTests(unittest.TestCase):
    def test_is_provider_only_propagates_to_registrar(self) -> None:
        provider_family = ToolFamilySpec(
            module_id="search.fixture",
            is_provider_only=True,
            tools=(),
        )
        registrar = build_registrar(provider_family)
        self.assertTrue(registrar.is_provider_only)

    def test_provider_registration_callback_fires_on_register(self) -> None:
        calls: list[str] = []

        def _register_fixture_provider() -> None:
            calls.append("registered")

        provider_family = ToolFamilySpec(
            module_id="search.fixture",
            is_provider_only=True,
            tools=(),
            provider_registration=_register_fixture_provider,
        )
        registrar = build_registrar(provider_family)
        registrar.register(ToolRegistry([]), ctx=None)
        self.assertEqual(calls, ["registered"])

    def test_no_callback_is_a_noop(self) -> None:
        family = ToolFamilySpec(
            module_id="search.fixture",
            is_provider_only=True,
            tools=(),
        )
        registrar = build_registrar(family)
        registrar.register(ToolRegistry([]), ctx=None)
