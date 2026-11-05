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
| `delegation.py` | Peer handoffs (with the approval pause) and bounded subagents |
| `application_runtime.py` | Application-owned `APIRuntime` lifecycle |
| `provider_switching.py` | Selecting configured agents without changing code |
| `error_handling.py` | Provider, turn-deadline and output-validation failures |

Use `python <file> --help` to see each program's options. Provider setup and
switching commands live in `examples/providers/README.md`.

Structured-output examples ask providers for exact JSON. OpenMinion also keeps
the bounded `0.1.x` behavior that accepts one schema-valid JSON object wrapped
in provider prose.

## Handoff approval

`delegation.py --mode handoff` exposes the researcher as a
`transfer_to_researcher` tool. A `Handoff` declares no risk metadata, so the
policy keeps it on the unknown-reversibility default and pauses for
confirmation before the target runs.

- Without an `approval_callback`, the run ends at that pause: `result.text` is
  the operator prompt ("Policy confirmation required. …"), not the
  researcher's answer, and `result.metadata["respond_kind"]` is
  `policy_confirmation_prompt`. Application code that keeps its Agent open
  answers it on the waiting thread with `agent.run("yes",
  thread_id=result.thread_id)`; a plain later `run()` starts a new thread and
  does not answer it.
- With `approval_callback`, the callback decides for that call before the
  target runs: returning true allows that one invocation; returning false
  ends the turn with "Confirmation denied. The action was not run."; a
  callback that raises fails the run with that error. In both failure cases
  the researcher does not run.

The example's `--approval` flag exercises each path; the script prints the
operator's view and exits:

```bash
python examples/sdk/delegation.py --mode handoff
python examples/sdk/delegation.py --mode handoff --approval allow_once
python examples/sdk/delegation.py --mode handoff --approval deny
```

The example callback decides only for `transfer_to_researcher`: a
confirmation for any other tool is denied, and its allowance is consumed
once.
