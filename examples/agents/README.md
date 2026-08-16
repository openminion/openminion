# Agent Examples

`openminion/examples/agents/` holds small bundle-style agent examples.

Start with:

1. `hello/` for the minimal greeting-oriented identity bundle shape.

The bundle demonstrates `AGENT.md`, `SOUL.md`, `SKILLS/`, and `NOTES/`; it is
not a pre-registered runtime agent. Import and render it after initializing a
local configuration:

```bash
openminion config init --provider echo
openminion identity import --from-bundle examples/agents/hello
openminion identity render hello --purpose act --max-tokens 180
```

The matching current tool contract is shown separately in
`examples/starter/tool.py`.
