# Tool module reference

Last updated: 2026-09-06

OpenMinion keeps three tool identities separate:

1. A model tool ID is the stable name shown to the model, such as
   `file.read`.
2. A runtime binding ID is the stable dispatch seam, such as
   `runtime.file.read`.
3. A runtime candidate is the concrete registered tool name that executes the
   call.

Tool manifests connect those identities. The registry owns executable tools,
and `ToolRegistryManager` compiles manifests into deterministic lookup maps.

## Core contracts

`ToolSpec` is the executable registration record. Its required fields are:

- `name`
- `args_model`, a Pydantic model
- `min_scope`: `READ_ONLY`, `WRITE_SAFE`, `POWER_USER`, or `UI_AUTOMATION`
- `handler`

It can also declare risk, idempotency, capabilities, dependencies, sandbox
kind, blast radius, and whether read-only mode blocks the tool.

`ToolBindingManifest` contains:

- `module_id`
- `ModelToolDef` records
- `RuntimeBindingDef` records

Each non-provider-only tool package exports one `REGISTRAR` implementing
`ToolModuleRegistrar`. Bootstrap asks it for a manifest, registers its runtime
tools, verifies that every manifest candidate exists, and passes the manifest
to `ToolRegistryManager`.

## Preferred family declaration

Use `ToolFamilySpec` when a family is a direct list of typed tools. It derives
the runtime specs, model definitions, one-to-one bindings, and registrar from a
single declaration.

```python
from pydantic import BaseModel

from openminion.modules.tool.framework import (
    ToolDecl,
    ToolFamilySpec,
    build_registrar,
)


class LookupArgs(BaseModel):
    key: str


def _lookup(args: dict, context: object) -> dict:
    del context
    return {"ok": True, "key": args["key"]}


FAMILY = ToolFamilySpec(
    module_id="example",
    min_scope_default="READ_ONLY",
    common_tags=("plugin", "example"),
    tools=(
        ToolDecl(
            name="example.lookup",
            args_model=LookupArgs,
            handler=_lookup,
            description="Look up one example value.",
            idempotent=True,
            capabilities=("read_only",),
        ),
    ),
)

REGISTRAR = build_registrar(FAMILY)
```

Export `REGISTRAR` from the family package's `__init__.py`. Add the package to
`modules/tool/bootstrap/entries.py` only when it is built into OpenMinion.

## Manual registrar

Use a manual registrar only when a family needs provider preparation,
non-one-to-one candidate routing, or other behavior the family declaration
does not represent.

```python
from openminion.modules.tool.contracts import (
    ModelToolDef,
    RuntimeBindingDef,
    ToolBindingManifest,
)
from openminion.modules.tool.registry import ToolSpec


class ExampleRegistrar:
    module_id = "example"
    is_provider_only = False

    def register(self, registry, ctx=None) -> None:
        del ctx
        registry.add(
            ToolSpec(
                name="example.lookup",
                args_model=LookupArgs,
                min_scope="READ_ONLY",
                handler=_lookup,
                idempotent=True,
                capabilities=("read_only",),
            )
        )

    def get_manifest(self, ctx=None) -> ToolBindingManifest:
        del ctx
        return ToolBindingManifest(
            module_id=self.module_id,
            model_tools=(
                ModelToolDef(
                    model_tool_id="example.lookup",
                    description="Look up one example value.",
                    parameters={},
                ),
            ),
            runtime_bindings=(
                RuntimeBindingDef(
                    runtime_binding_id="runtime.example.lookup",
                    model_tool_id="example.lookup",
                    runtime_candidates=("example.lookup",),
                ),
            ),
        )


REGISTRAR = ExampleRegistrar()
```

Keep `ModelToolDef.parameters` empty. Runtime schemas are derived from the
registered tool's Pydantic argument model through `ToolRegistryManager`.

## Provider-only packages

A provider-only package extends a parent family and does not create a second
model-facing tool identity. Set `is_provider_only = True`, perform provider
registration in `register(...)`, and return an empty manifest. Search and
fetch providers follow this pattern.

Provider choice, fallback order, and failure behavior belong to the owning
family. The shared tool layer transports structured facts; it does not infer a
provider from response text.

## Exposure and policy

Registration makes a tool executable; exposure decides whether it is visible
for a session. `ToolExposureProfile` groups family-owned tools with:

- read, plan, or apply tier
- target kinds
- credential and dependency requirements
- evidence expectations and stop rules
- optional default activation

Apply-tier profiles require approval and cannot be active by default. Exposure
never replaces policy: scope, target binding, command rules, sandbox
requirements, and runtime approvals are still enforced at execution.

## Dependencies

Declare external binaries with `ToolDependencyDecl` and the helpers in
`modules/tool/runtime/dependencies.py`. Dependency checks report readiness and
installation guidance; they do not silently install software or bypass the
selected tool profile.

## External plugins

External plugins use the versioned plugin contracts under
`modules/tool/plugin_contract/`. Their model IDs must use
`plugin.<plugin-id>.*`, runtime binding IDs must use
`runtime.plugin.<plugin-id>.*`, and candidate names must stay inside the same
namespace. Bootstrap rejects ownership or namespace conflicts.

## Where to look

- Executable specs and registry: `modules/tool/registry/`
- Manifest contracts: `modules/tool/contracts/manifest.py`
- Binding manager: `modules/tool/runtime/manager.py`
- Registrar protocol: `modules/tool/runtime/registrar.py`
- Built-in bootstrap catalog: `modules/tool/bootstrap/entries.py`
- Family declaration helper: `modules/tool/framework.py`
- Exposure profiles: `modules/tool/exposure/`
- External plugin contracts: `modules/tool/plugin_contract/`

Tests under `tests/modules/tool/`, `tests/tools/`, and the package validation
gates protect registration, manifest ownership, exposure, policy, dependency,
and dispatch behavior.
