# CLI

`cli/` is the argparse command-line entry surface for OpenMinion.

Entry:
- `main.py` — `python -m openminion` dispatches here

Top-level settings:
- `config.py`
- `constants.py`

Subpackages:
- `chat/` — interactive chat REPL and prompt/session UX
- `commands/` — subcommand handlers bound by the parser
- `status/` — status-line rendering helpers
- `tui/` — full-screen Textual UI

Grouped helpers:
- `bootstrap/` — CLI-specific config resolution and path setup
- `parser/` — argparse construction and CLI contracts
- `identity/` — identity resolution at the CLI boundary
- `transport/` — daemon and in-process runtime transport helpers
- `presentation/` — ANSI styling and output formatting helpers

Compatibility note:
- CLC moved grouped helpers out of the `cli/` root, but `openminion.cli.<module>` compatibility imports remain supported through `openminion.cli.__init__` aliases so older public imports keep resolving without reintroducing flat root files.
