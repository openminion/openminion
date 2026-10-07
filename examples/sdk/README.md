# Python SDK Examples

These programs are copyable references for OpenMinion's public Python API.
They expect a configured model provider unless the example says otherwise.

Install the current package and configure a provider first:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install openminion
openminion setup --no-chat
openminion doctor --check-turn
```

For a source preview, clone the repository and run `python -m pip install -e .`
from that checkout instead of pinning an old commit.

Run an example from the repository root:

```bash
python examples/sdk/tool_agent.py
```

## Example map

| File | Use it for |
| --- | --- |
| `tool_agent.py` | Decorated Python tools and a forced first tool call |
| `structured_output.py` | Pydantic-validated output |
| `sessions.py` | Shared and isolated session IDs |
| `streaming_progress.py` | Synchronous runs with progress callbacks |
| `submitted_stream.py` | Typed iterator streaming and approval resolution |
| `delegation.py` | Peer handoffs and bounded subagents |
| `application_runtime.py` | Application-owned `APIRuntime` lifecycle |
| `provider_switching.py` | Selecting configured agents without changing code |
| `error_handling.py` | Provider and output-validation failures |

Use `python <file> --help` to see each program's options. Provider setup and
switching commands live in `examples/providers/README.md`.

Structured-output examples ask providers for exact JSON. OpenMinion also keeps
the bounded `0.1.x` behavior that accepts one schema-valid JSON object wrapped
in provider prose.
