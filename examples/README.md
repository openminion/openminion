# OpenMinion Examples

`openminion/examples/` is the canonical public examples directory.

Use it as the main starter surface for:

1. copy-first starter snippets,
2. bundle-style agent examples,
3. skill examples and fixture catalogs,
4. identity/config examples,
5. runnable example packages.

## Family Map

### 1. Starter snippets

Copy-first one-file examples for common extension and runtime surfaces live
under:

1. `openminion/examples/starter/`

Start with:

1. `openminion/examples/starter/provider.py`
2. `openminion/examples/starter/channel.py`
3. `openminion/examples/starter/plugin.py`
4. `openminion/examples/starter/tool.py`
5. `openminion/examples/starter/plugin.json`
6. `openminion/examples/starter/quickstart.py`

### 2. Agent bundles

Small markdown-first agent bundles live under:

1. `openminion/examples/agents/`

Start with:

1. `openminion/examples/agents/hello/`

These bundle paths stay uppercase in this package by design:

1. `AGENT.md`
2. `SOUL.md`
3. `SKILLS/`
4. `NOTES/`

### 3. Skill examples and fixtures

Skill examples and checked-in skill fixtures live under:

1. `openminion/examples/skills/`

Start with:

1. `openminion/examples/skills/hello/`
2. `openminion/examples/skills/cli-chat-smoke/`
3. `openminion/examples/skills/cli-chat-smoke-invalid/`

Other skill examples follow the same lower-kebab-case family layout under:

1. `openminion/examples/skills/`

### 4. Identity/config examples

Configuration examples live under:

1. `openminion/examples/identity/`

Start with:

1. `openminion/examples/identity/README.md`
2. one of the checked-in sample profiles in `openminion/examples/identity/`

### 5. Runnable package examples

Runnable package-style examples live under:

1. `openminion/examples/modules/`

Current package:

1. see `openminion/examples/modules/README.md` for the current runnable sample
   package

## Naming Contract

1. Contract-owned uppercase bundle paths remain uppercase:
   `AGENT.md`, `SOUL.md`, `SKILLS/`, `NOTES/`, `SKILL.md`.
2. Starter snippets use short local names because the parent path already gives
   the context.
3. Skill scenario directories stay lower-kebab-case.
4. Runnable packages stay under `openminion/examples/modules/`.
5. New example families should prefer folder context over `hello_*`-style flat
   filename sprawl.

## Canonical Paths

Contributor docs should point at the live owner:

1. `openminion/examples/...`

Use `openminion/examples/...` when referring to the checked-in public example
surface.
