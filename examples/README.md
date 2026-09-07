# OpenMinion Examples

`examples/` is the canonical public examples directory.

Use it as the main starter surface for:

1. copy-first starter snippets,
2. bundle-style agent examples,
3. skill examples and fixture catalogs,
4. identity/config examples,
5. runnable example packages,
6. graph viewer fixtures.

## Family Map

### 1. Starter snippets

Copy-first one-file examples for common extension and runtime surfaces live
under:

1. `examples/starter/`

Start with:

1. `examples/starter/provider.py`
2. `examples/starter/channel.py`
3. `examples/starter/hello.py`
4. `examples/starter/tool.py`
5. `examples/starter/hello.manifest.json`
6. `examples/starter/quickstart.py`

### 2. Agent bundles

Small markdown-first agent bundles live under:

1. `examples/agents/`

Start with:

1. `examples/agents/hello/`

These bundle paths stay uppercase in this package by design:

1. `AGENT.md`
2. `SOUL.md`
3. `SKILLS/`
4. `NOTES/`

### 3. Skill examples and fixtures

Skill examples and checked-in skill fixtures live under:

1. `examples/skills/`

Start with:

1. `examples/skills/hello/`
2. `examples/skills/cli-chat-smoke/`
3. `examples/skills/cli-chat-smoke-invalid/`

Other skill examples follow the same lower-kebab-case family layout under:

1. `examples/skills/`

### 4. Identity/config examples

Configuration examples live under:

1. `examples/identity/`

Start with:

1. `examples/identity/README.md`
2. one of the checked-in sample profiles in `examples/identity/`

### 5. Runnable package examples

Runnable package-style examples live under:

1. `examples/modules/`

Current package:

1. see `examples/modules/README.md` for the current runnable sample
   package

### 6. Graph viewer fixtures

Visual graph examples live under:

1. `examples/graph-viewer/`

Start with:

1. `examples/graph-viewer/README.md`
2. `examples/graph-viewer/agents.json`
3. `examples/graph-viewer/repo-viewer-envelope.json`

## Naming Contract

1. Contract-owned uppercase bundle paths remain uppercase:
   `AGENT.md`, `SOUL.md`, `SKILLS/`, `NOTES/`, `SKILL.md`.
2. Starter snippets use short local names because the parent path already gives
   the context.
3. Skill scenario directories stay lower-kebab-case.
4. Runnable packages stay under `examples/modules/`.
5. Graph viewer examples stay under `examples/graph-viewer/`.
6. New example families should prefer folder context over `hello_*`-style flat
   filename sprawl.

## Canonical Paths

Contributor docs should point at the live owner:

1. `examples/...`

Use `examples/...` when referring to the checked-in public example
surface.
