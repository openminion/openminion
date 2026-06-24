# `services/channel/`

Owner: services-layer
Pairs with: standalone (no single `modules/` peer; consumed by
controlplane channels in `openminion.controlplane.channels.*`)
Canonical builder: `build_channel_authenticity_policy`

## Purpose

Cross-channel runtime policy. Holds the channel-authenticity policy
used to gate inbound messages on transports (Telegram, CLI, gateway
HTTP) and the per-channel policy decision record consumed by the
gateway. Independent of any single transport — channels register
themselves and consume the shared policy.

## Public surface

Re-exported from `openminion.services.channel`:

- `ChannelAuthenticityPolicy` — authenticity policy class
- `build_channel_authenticity_policy(...)` — canonical builder
- `ChannelPolicyDecision` — typed decision record

## Owned objects

- The runtime `ChannelAuthenticityPolicy` instance (one per runtime).
- Per-message `ChannelPolicyDecision` records emitted into the
  gateway pipeline.

## Non-goals

- Specific transport adapters (Telegram, HTTP, CLI) — those live
  in `controlplane/channels/*`.
- Identity verification — handled by `modules/identity/` and
  `services/identity/`.
- Tool-execution authorization — that lives in `services/security/`.
- Message shape — owned by `modules/session/` (`Message`) and
  `services/gateway/`.

## Dependencies

- `modules/identity/` — actor and bundle types.
- `base/config/` — operator-tunable channel allowlists.
- `services/security/` — composes the authenticity decision with
  security checks at the gateway boundary.

## How this differs from `modules/`

There is no `modules/channel/`. The runtime channel concern (gate
inbound messages, decide whether an authenticated actor can post on a
channel) is a pure-services concern; the transport-specific channel
implementations live under `openminion.controlplane.channels.*`.
This package owns the cross-transport policy fabric only.
