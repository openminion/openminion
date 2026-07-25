# Controlplane Runtime Dispatch

This package owns the synchronous controlplane inbound dispatch flow.

## Layout

- `coordinator.py` owns `ControlPlaneDispatcher`, public constructor
  compatibility, inbound persistence, outbound emission, and the top-level
  wizard -> command -> chat ordering.
- `wizard.py` owns active wizard-session dispatch and the thread-backed async
  escape hatch used when a caller already has an event loop.
- `command.py` owns parsed slash-command execution and command audit events.
- `chat.py` owns normal chat dispatch, clarify answer/request extraction,
  brain status normalization, and chat audit events.
- `clarify.py` owns pending-clarify in-memory state plus durable store hydrate,
  set, and clear operations.

## Dispatch Flow

1. `ControlPlaneDispatcher.handle_inbound()` canonicalizes and persists inbound
   messages, then delegates to `dispatch()`.
2. `dispatch()` gives active wizards first chance to handle the inbound message.
3. If no wizard handles it, the command parser runs and `CommandDispatcher`
   handles parsed commands.
4. If no command is parsed, `ChatDispatcher` sends the message through the brain
   client and records clarify state when the brain asks the user for input.

## Clarify Lifecycle

`ClarifyStateManager.hydrate_from_store()` runs once during dispatcher
construction. Chat dispatch reads pending clarify state through the manager,
sets it when the brain returns a blocking clarify request, and clears it after
normal completion.

## Adding A Dispatch Surface

Add a new surface only when it has a distinct routing predicate and ownership
model. Keep the coordinator thin: the new surface should own its behavior in a
separate module and expose one small `dispatch` or `try_dispatch` method.
