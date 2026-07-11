# Typed Memory Namespace Queries

Status: alpha
Last updated: 2026-07-10

OpenMinion exposes typed memory-record filters through `memctl` and the local
HTTP API. The canonical model remains `sophiagraph.models.MemoryNamespace`.
OpenMinion requires `sophiagraph>=0.0.1`; that release contains all namespace
dimensions and the list/search filter contracts used here.

## Namespace fields

One query accepts one composite namespace containing any non-empty combination
of:

1. `tenant_id`
2. `org_id`
3. `user_id`
4. `agent_id`
5. `session_id`
6. `conversation_id`
7. `project_id`
8. `graph_id`

Supplied dimensions are matched together. OpenMinion does not derive IDs from
display names, titles, record content, or prompts.

## CLI

The existing `memctl list` and `memctl search` commands accept matching
`--tenant-id`, `--org-id`, `--user-id`, `--agent-id`, `--session-id`,
`--conversation-id`, `--project-id`, and `--graph-id` options.

```bash
memctl list --user-id user-a --agent-id agent-a --json
memctl search "deployment convention" \
  --user-id user-a \
  --project-id project-a \
  --json
```

Legacy scope calls remain supported:

```bash
memctl list --scope agent:agent-a
memctl search "deployment convention" --scope project:project-a
```

When `--scope` and typed fields are combined, overlapping values must agree.
An `agent:agent-a` scope combined with `--agent-id agent-b` fails closed.

## Local HTTP API

The local API server exposes:

1. `POST /memory/records/list`
2. `POST /memory/records/search`

List example:

```json
{
  "namespace": {"user_id": "user-a", "agent_id": "agent-a"},
  "scope": "agent:agent-a",
  "types": ["fact"],
  "limit": 100,
  "offset": 0
}
```

Search example:

```json
{
  "query": "deployment convention",
  "namespace": {"user_id": "user-a", "project_id": "project-a"},
  "limit": 20
}
```

Successful responses include `count`, `records`, the resolved canonical
`namespace`, the optional legacy `scope`, and `legacy_scope_only`. Invalid
input returns `400 invalid_request`; an unavailable durable-memory provider
returns `503 memory_unavailable`. A valid namespace with no matching records
returns `200` with `count: 0`.

## Security boundary

These routes are local operator surfaces. Namespace filters isolate records but
do not authenticate a human principal or authorize which tenant or user IDs a
principal may access. Deployments that expose the API beyond a trusted local
boundary must provide their own network and operator-authentication controls.

RBAC, operator IdP integration, and cross-channel identity pairing are not part
of this surface.

## Compatibility and rollback

The rollout is additive:

1. legacy scope records remain readable through `MemoryNamespace.from_scope`,
2. no background record migration is required,
3. old `memctl` scope invocations remain valid, and
4. SQLite persists explicit namespace JSON while deriving missing legacy
   namespaces from scope.

Rollback removes the typed CLI options, the two HTTP routes, the API runtime
query dependency, and the namespace smoke runner. It leaves existing scope
queries and persisted records unchanged.
