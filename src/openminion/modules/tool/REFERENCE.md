# Tool Module System Reference

This file holds the long-form Tool Module System tutorial and reference
content that previously lived inline in `README.md`. The charter for this
module stays in `README.md` so the modules/* README family reads as one
consistent micro-charter set.

---

# Tool Module System

**Version:** 2.0 (Manifest-Based with Unified Interface)
**Status:** ACTIVE ✅
**Last Updated:** 2026-03-14

This is the canonical tool runtime module for OpenMinion. All tool modules must follow the unified interface contract defined in this package.

---

## Quick Start: Adding a New Tool Module

### 1. Create Module Directory

```bash
mkdir -p src/openminion/tools/your_module
```

### 2. Create `__init__.py` with REGISTRAR

```python
# src/openminion/tools/your_module/__init__.py
from __future__ import annotations

from typing import Any, Dict
from pydantic import BaseModel, Field

from openminion.modules.tool.contracts import (
    ModelToolDef, RuntimeBindingDef, ToolBindingManifest,
)
from openminion.modules.tool.runtime.registrar import ToolRegisterContext
from openminion.modules.tool.registry import ToolRegistry, ToolSpec


class YourActionArgs(BaseModel):
    param: str = Field(..., description="Required parameter")


def _h_your_action(args: Dict[str, Any], ctx: Any) -> Dict[str, Any]:
    parsed = YourActionArgs(**args)
    return {"ok": True, "content": f"Did {parsed.param}"}


class YourToolModuleRegistrar:
    module_id = "your_module"

    @staticmethod
    def get_manifest(ctx: ToolRegisterContext) -> ToolBindingManifest:
        return ToolBindingManifest(
            module_id="your_module",
            model_tools=(
                ModelToolDef(
                    model_tool_id="your_module.action",
                    description="Do something",
                    parameters={
                        "type": "object",
                        "required": ["param"],
                        "properties": {
                            "param": {"type": "string"},
                        },
                    },
                    aliases=("your_action",),
                ),
            ),
            runtime_bindings=(
                RuntimeBindingDef(
                    runtime_binding_id="your_module.action",
                    model_tool_id="your_module.action",
                    runtime_candidates=("your_action",),
                ),
            ),
        )

    @staticmethod
    def register(registry: ToolRegistry, ctx: ToolRegisterContext) -> None:
        registry.add(
            ToolSpec(
                name="your_action",
                args_model=YourActionArgs,
                min_scope="READ_ONLY",
                handler=_h_your_action,
            )
        )


REGISTRAR = YourToolModuleRegistrar
```

### 3. Verify Registration

```bash
cd openminion
PYTHONPATH=src .venv/bin/python3.11 -c "
from openminion.tools.your_module import REGISTRAR
manifest = REGISTRAR.get_manifest(None)
print(f'✅ Module: {manifest.module_id}, Tools: {len(manifest.model_tools)}')
"
```

---

## Interface Contract

### ALL Modules Must Implement

```python
from openminion.modules.tool.runtime.registrar import ToolModuleRegistrar, ToolRegisterContext
from openminion.modules.tool.contracts import ToolBindingManifest

class YourToolModuleRegistrar:
    module_id: str

    @staticmethod
    def get_manifest(ctx: ToolRegisterContext) -> ToolBindingManifest:
        """PRIMARY registration surface - REQUIRED"""
        ...

    @staticmethod
    def register(registry: ToolRegistry, ctx: ToolRegisterContext) -> None:
        """Optional for manifest-only modules"""
        ...

# REQUIRED: Export REGISTRAR
REGISTRAR = YourToolModuleRegistrar
```

### Key Components

| Component | Required | Purpose |
| --- | --- | --- |
| `module_id` | ✅ | Unique module identifier |
| `get_manifest(ctx)` | ✅ | Returns ToolBindingManifest (authoritative) |
| `register(registry, ctx)` | ⚠️ Optional | Register runtime tools (omit for simple modules) |
| `REGISTRAR` export | ✅ | Bootstrap auto-discovers via this |

---

## Module Structure

### Standard Layout

```
src/openminion/tools/your_module/
├── __init__.py       # REQUIRED: REGISTRAR + manifest
├── schemas.py        # Pydantic argument models
├── handlers.py       # Tool handler functions
├── provider.py       # Provider implementation (if applicable)
└── interfaces.py     # Protocol definitions (if applicable)
```

### Minimal Layout (Recommended)

```
src/openminion/tools/your_module/
├── __init__.py       # Everything in one file
└── schemas.py        # Argument models
```

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    ALL TOOL MODULES                          │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  ToolModuleRegistrar Protocol                       │   │
│  │  • get_manifest(ctx) -> ToolBindingManifest         │   │
│  │  • register(registry, ctx) -> None                  │   │
│  └─────────────────────────────────────────────────────┘   │
│                          ↓                                   │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  REGISTRAR Export                                   │   │
│  │  REGISTRAR = <YourPluginClass>                      │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                          ↓
              bootstrap.py auto-discovers
                          ↓
              ToolRegistryManager.compile()
                          ↓
              Authoritative source for all tools
```

### Key Principles

1. **ONE interface, ALL modules, NO exceptions**
2. **Manifests are authoritative** (replaces `contracts/map.py`)
3. **Bootstrap auto-discovers** via `REGISTRAR` export
4. **Backward compatibility** via deprecated helpers in `contracts/normalization.py`

## Internal Runtime Layout

The canonical multi-file helper clusters now live under real packages:

```text
src/openminion/modules/tool/
├── cli/
│   ├── __init__.py
│   ├── runtime.py
│   ├── runtime_invocation.py
│   ├── core_commands.py
│   ├── exec_commands.py
│   └── pinchtab_commands.py
├── family/
│   ├── __init__.py
│   ├── runtime.py
│   ├── policy.py
│   └── events.py
├── registry/
│   ├── __init__.py
│   ├── catalog.py
│   └── exposure.py
├── policy.py
├── runtime.py
└── bootstrap.py
```

The high-fan-out core owners remain at the package root. The `cli/` and
`family/` packages are the canonical helper paths for internal imports.

---

## Tool Binding Manifest

### Structure

```python
from openminion.modules.tool.contracts import (
    ModelToolDef, RuntimeBindingDef, ToolBindingManifest,
)

manifest = ToolBindingManifest(
    module_id="your_module",
    model_tools=(
        ModelToolDef(
            model_tool_id="your_module.action",
            description="Do something useful",
            parameters={
                "type": "object",
                "required": ["param"],
                "properties": {
                    "param": {"type": "string", "description": "Parameter"},
                },
            },
            aliases=(),  # Optional alternative names
        ),
    ),
    runtime_bindings=(
        RuntimeBindingDef(
            runtime_binding_id="runtime.your_module.action",
            model_tool_id="your_module.action",
            runtime_candidates=("your_module_action",),
        ),
    ),
)
```

### Fields

| Field | Purpose |
| --- | --- |
| `module_id` | Unique module identifier |
| `model_tools` | Model-facing tool definitions |
| `model_tool_id` | Canonical tool ID (e.g., `file.read`) |
| `description` | Human-readable description |
| `parameters` | JSON Schema for arguments |
| `aliases` | Alternative names (optional) |
| `runtime_bindings` | Binding to runtime implementations |
| `runtime_candidates` | Actual tool names in registry |

---

## Provider-Only Modules

Some modules provide implementations without direct tools (e.g., `fetch_scrapling`, `browser_pinchtab`):

```python
# src/openminion/tools/your_provider/__init__.py
from openminion.modules.tool.contracts import ToolBindingManifest
from openminion.modules.tool.runtime.registrar import ToolRegisterContext
from openminion.modules.tool.registry import ToolRegistry

from .provider import YourProvider


class YourProviderPlugin:
    module_id = "your_provider"

    @staticmethod
    def get_manifest(ctx: ToolRegisterContext) -> ToolBindingManifest:
        """Minimal manifest (provider-only, no direct tools)."""
        return ToolBindingManifest(
            module_id="your_provider",
            model_tools=(),      # No direct tools
            runtime_bindings=(), # Tools provided via parent module
        )

    @staticmethod
    def register(registry: ToolRegistry, ctx: ToolRegisterContext) -> None:
        """Register provider implementation."""
        del ctx
        from your_parent_module import register_provider
        register_provider(YourProvider())


REGISTRAR = YourProviderPlugin
```

---

## Validation Checklist

Before committing a new module:

- [ ] `REGISTRAR` export exists
- [ ] `get_manifest(ctx)` returns valid `ToolBindingManifest`
- [ ] `register(registry, ctx)` works (if implemented)
- [ ] `module_id` is unique
- [ ] All `model_tool_id` values are unique
- [ ] All `runtime_binding_id` values are unique
- [ ] `runtime_candidates` match registered tool names
- [ ] Pydantic schemas validate correctly

---

## Testing

### Quick Test

```bash
cd openminion
PYTHONPATH=src .venv/bin/python3.11 -c "
from openminion.tools.your_module import REGISTRAR
from openminion.modules.tool.runtime.registrar import ToolRegisterContext

manifest = REGISTRAR.get_manifest(None)
print(f'✅ Module: {manifest.module_id}')
print(f'✅ Tools: {len(manifest.model_tools)}')
print(f'✅ Bindings: {len(manifest.runtime_bindings)}')
"
```

### Run Registry Tests

```bash
PYTHONPATH=src .venv/bin/python3.11 -m pytest -q \
  tests/test_tool_module_registrar.py \
  tests/test_tool_registry_manager.py \
  tests/test_tool_registry.py
```

### E2E Test

```bash
PYTHONPATH=src .venv/bin/python3.11 -m openminion \
  --config test-configs/per-agent-alibaba-minimax.json \
  chat --agent alibaba-minimax --session test --quiet --no-progress <<EOF
hello
/exit
EOF
```

---

## Existing Module Examples

| Module | Tools | Pattern | File |
| --- | --- | --- | --- |
| `file/` | 5 | Simple tool module | `src/openminion/tools/file/__init__.py` |
| `exec/` | 8 | Simple tool module | `src/openminion/tools/exec/plugin.py` |
| `time/` | 9 | Simple tool module | `src/openminion/tools/time/plugin.py` |
| `browser_playwright/` | 15 | Many tools | `src/openminion/tools/browser/providers/playwright/plugin.py` |
| `fetch_scrapling/` | 0 | Provider-only | `src/openminion/tools/fetch/providers/scrapling/plugin.py` |
| `search_tavily/` | 1 | Single tool | `src/openminion/tools/search/providers/tavily/plugin.py` |

---

## Migration from Legacy

### ❌ OLD (Don't Use)

```python
# Using contracts/map.py (DEPRECATED)
from openminion.modules.tool.contracts.map import RUNTIME_BINDING_MAP

# Manual registration
def register(registry):
    registry.add(ToolSpec(...))
```

### ✅ NEW (Correct)

```python
# Using ToolBindingManifest
from openminion.modules.tool.contracts import (
    ModelToolDef, RuntimeBindingDef, ToolBindingManifest,
)

class YourToolModuleRegistrar:
    @staticmethod
    def get_manifest(ctx) -> ToolBindingManifest:
        return ToolBindingManifest(
            module_id="your_module",
            model_tools=(...),
            runtime_bindings=(...),
        )

REGISTRAR = YourToolModuleRegistrar
```

---

## Deprecated Compatibility

The following helpers are available for backward compatibility (TMR-E03):

```python
from openminion.modules.tool.contracts import (
    model_tool_id_for_runtime_tool_name,       # DEPRECATED
    runtime_binding_id_for_model_tool_id,      # DEPRECATED
    runtime_candidates_for_binding,            # DEPRECATED
    binding_group_for_runtime_binding_id,      # DEPRECATED
    validate_binding_map,                      # DEPRECATED (no-op)
)
```

**Use `ToolRegistryManager` methods instead:**
- `manager.normalize_raw_name()` instead of `model_tool_id_for_runtime_tool_name()`
- `manager.resolve_binding()` instead of `runtime_binding_id_for_model_tool_id()`
- `manager.runtime_candidates()` instead of `runtime_candidates_for_binding()`

---

## Related Documentation

| Document | Location |
| --- | --- |
| **Interface Contract** | `src/openminion/modules/tool/register.py` |
| **Manifest Types** | `src/openminion/modules/tool/contracts/manifest.py` |
| **Bootstrap** | `src/openminion/modules/tool/bootstrap.py` |
| **Manager** | `src/openminion/modules/tool/manager.py` |
| **Full Guide** | package tool registration guide |
| **Interface Doc** | package tool registration interface doc |
| **Binding Contract** | tool binding contract |
| **Tracker** | tool registration normalization tracker |

---

## Status

| Metric | Value |
| --- | --- |
| **Total Modules** | 20 |
| **Compliance** | 100% ✅ |
| **Tests Passing** | 352+ |
| **E2E Gate** | ✅ PASS |
| **map.py** | ❌ DELETED (TMR-E03) |

---

**Contract Version:** 2.0 (Manifest-Based with Unified Interface)
**Effective Date:** 2026-03-14
**Compliance:** ALL 20 modules ✅

---

## Environment Variable Best Practices

### Use EnvironmentConfig Pattern

**ALL new code MUST use `EnvironmentConfig` for environment variable access.** This ensures:

1. **Maintainability** - All env vars defined in one place
2. **Type Safety** - Automatic parsing and validation
3. **Testability** - Easy to mock without process env mutation
4. **Documentation** - Self-documenting in dataclass
5. **Validation** - Required vars validated at runtime

### Correct Pattern

```python
# ✅ CORRECT: Use EnvironmentConfig
from openminion.base.config import EnvironmentConfig

class YourTool:
    def __init__(self, env: EnvironmentConfig):
        # Typed, validated, documented
        self._api_key = env.tavily_api_key
        self._timeout = env.openminion_turn_timeout_seconds
        # Or use generic getter
        self._custom = env.get("YOUR_CUSTOM_VAR", "default")
```

### Incorrect Patterns (Don't Use)

```python
# ❌ WRONG: Scattered, untyped, hard to test
api_key = "<read from process env directly>"
timeout = "<read from process env directly>"

# ❌ WRONG: Hidden dependency
def process():
    key = "<implicit process-env key>"  # Where does this come from?
```

### Enforcement

The environment guard script catches violations:

```bash
# Run during development
python scripts/validate/direct_env_calls.py --warn

# Strict mode (CI)
python scripts/validate/direct_env_calls.py --fail-on-violation

# Via pytest (when enabled)
pytest --env-guard
```

**See:** the environment access policy for the complete policy.
