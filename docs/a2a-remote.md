# A2A remote collaboration over HTTP

The A2A module ships an HTTP transport so two OpenMinion machines can call each
other's agents across a network instead of only in-process. The server is the
`a2actl serve` subcommand; the client is `a2actl call --url ...` (or the
`HTTPTransport` class directly).

> The A2A control CLI is `a2actl` (the `openminion.modules.a2a.cli:main`
> entry point), not `openminion a2a`.

> **This is an OpenMinion custom endpoint HTTP transport, not a complete Google
> A2A protocol server.** The endpoints below (`/a2a/call`, `/a2a/job/start`,
> `/a2a/job/status`, `/a2a/job/cancel`) speak OpenMinion's own `Envelope` JSON,
> and only the agent card at `/.well-known/agent.json` borrows the
> Google-A2A-style shape (`name`, `protocolVersion`, `capabilities`, `skills`).
> A generic Google A2A client cannot call these endpoints, and an OpenMinion
> `HTTPTransport` cannot call an arbitrary Google A2A server. Interoperation is
> between OpenMinion machines running this transport.

## Built-in demo agents vs. configured application agents

`a2actl serve` registers two **built-in demo agents** to exercise the transport
— `agent.echo` (mirrors the request back) and `agent.worker` (sleeps and returns
a result for async job tests). These exist only so the two-machine walkthrough
below has something to call out of the box.

They are **not** your configured application agents, and the fact that
`agent.echo`/`agent.worker` appear in the agent card and answer cross-machine
calls does **not** demonstrate that configured application agents are reachable
over HTTP. Wiring real application agents into the A2A runtime — and confirming
their handlers execute across the wire — is a separate step beyond this
transport.

## Two-machine walkthrough

Machine A runs the server. It must pick a shareable secret:

```bash
# Machine A
a2actl serve --host 0.0.0.0 --port 18790 --token <secret>
```

Omit `--token` to have `a2actl` generate a random secret and print it to stderr:

```bash
# Machine A (generate + print the token)
a2actl serve --host 0.0.0.0 --port 18790
# a2actl serve: generated token: <32-char url-safe secret>
```

Machine B calls across the wire with the same token:

```bash
# Machine B
a2actl call \
  --url http://A:18790 \
  --token <secret> \
  --from-agent client \
  --agent agent.echo \
  --method echo.hello \
  --payload-json '{"text":"hi"}'
```

`--agent` is an alias for `--to-agent` and `--payload-json` is an alias for
`--params`. The response Envelope is printed as JSON. Without `--url`, `call`
keeps its existing in-process behavior.

The same endpoints are reachable from Python:

```python
from openminion.modules.a2a.models import Envelope
from openminion.modules.a2a.transport import HTTPTransport

transport = HTTPTransport("http://A:18790", token="<secret>")
result = transport.call(
    Envelope.new(
        from_agent="client",
        to_agent="agent.echo",
        to_capability=None,
        type="call",
        method="echo.hello",
        params={"text": "hi"},
        idempotency_key="unique-key",
    )
)
```

`HTTPTransport` also exposes `job_start`, `job_status`, and `job_cancel` for
async jobs across the wire.

## Security model

- **Token auth is required.** Every endpoint compares
  `Authorization: Bearer <token>` using a constant-time comparison. A missing
  or mismatched token returns `401`. `a2actl serve` never starts with an empty
  token: it generates one and prints it to stderr if `--token` is omitted, and
  `A2AHTTPServer`/`serve_a2a` reject an empty or whitespace-only token.
- **Default bind is loopback only** (`--host 127.0.0.1`). A server bound to
  `127.0.0.1` is not reachable from another machine. Passing
  `--host 0.0.0.0` (or a specific interface address) exposes the port on the
  network — do this only when you intend remote access.
- **No TLS.** The transport is plain HTTP. Run it on a trusted network only, or
  tunnel it over SSH (e.g. `ssh -L 18790:127.0.0.1:18790 machine-a`), or put it
  behind a TLS-terminating reverse proxy. Never expose the A2A port directly to
  the public internet.

## Endpoints

- `POST /a2a/call` — `Envelope` JSON in, `Envelope` JSON out.
- `POST /a2a/job/start` — `Envelope` JSON in, `{"task_id": "..."}` out.
- `POST /a2a/job/status` — `{"task_id": "..."}` in, `JobRecord` JSON out.
- `POST /a2a/job/cancel` — `{"task_id": "..."}` in, `JobRecord` JSON out.
- `GET /.well-known/agent.json` — the agent card, including an `agents` array
  listing the registered agents and their capabilities.

Errors are JSON `{"error": {"code": "...", "message": "...", "details": ...}}`
with `400` for an invalid envelope, `404` for an unknown agent/job, `403` for a
policy denial, and `500` for internal failures. The client preserves the server's
error code, message, and details; only genuinely malformed or non-envelope
responses are reported as `INTERNAL_ERROR`.

## Rooms on one machine

For local multi-agent collaboration inside a single OpenMinion process, the
interactive CLI's room commands cover the same ground without the network:

- `/room` — create or open a collaboration room.
- `/participants` — show room participants and routing.
- `/routing` — show or set room routing (`addressed`, `broadcast`,
  `sequential`).
- `/message @agent <note>` — queue a non-waking note for a room agent.
- `/delegate @agent <instruction>` — delegate work to a peer agent.
- `/handoff @agent --task <step-id>` — preview and confirm a room task handoff.

Use HTTP A2A when the agents live on different machines; use rooms when they
share one process.
