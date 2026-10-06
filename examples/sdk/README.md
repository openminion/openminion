# Python SDK Examples

These programs are copyable references for OpenMinion's public Python API.
They expect a configured model provider unless the example says otherwise.

Install the current SDK preview and configure a provider first:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install \
  "openminion @ git+https://github.com/OpenMinion/openminion.git@d77f1cbc9b5332c80a0bb59d8d2cf9b707754f3f"
openminion setup --no-chat
openminion doctor --check-turn
```

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
| `delegation.py` | Peer handoffs and bounded subagents |
| `application_runtime.py` | Application-owned `APIRuntime` lifecycle |
| `provider_switching.py` | Selecting configured agents without changing code |
| `error_handling.py` | Provider and output-validation failures |

Use `python <file> --help` to see each program's options. Provider setup and
switching commands live in `examples/providers/README.md`.
