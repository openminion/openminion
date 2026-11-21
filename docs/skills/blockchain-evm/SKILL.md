---
name: blockchain-evm
description: Operate the OpenMinion EVM tools safely, from research to one approved send.
tools: [blockchain.resolve_contract, blockchain.inspect, blockchain.debug, blockchain.prepare_transaction, blockchain.send_transaction]
tags: [blockchain, evm, smart-contract, transaction]
applies_to: [read-only research, contract inspection, simulation, single approved write]
preflight: [blockchain.inspect action=chain, blockchain.resolve_contract, blockchain.debug action=simulate_call]
stop_conditions: [unresolved contract, unknown chain anchor, pending or unknown broadcast, cap refusal, revert]
version: 1
---

# Blockchain EVM

Use this skill when a task names a chain, a contract, a transaction, a balance, or a
call result. The tools are structural: they verify what they are given and refuse
what they cannot anchor. Your job is to supply researched facts, read the refusals
literally, and never improvise a second broadcast.

# Preflight, before any write

1. Read the chain with `blockchain.inspect` `action=chain`: chain id, genesis hash,
   latest block. Compare it with the chain the user named.
2. Resolve the contract with `blockchain.resolve_contract` when the task gives a
   name, a protocol, or a source rather than an ABI. Supply the expected chain id
   and genesis hash, the address, a public HTTPS RPC without credentials or query
   string, and the display and source URLs you actually read. The result pins one
   endpoint, one address, one lineage and one ABI behind a digest.
3. Inspect what you intend to touch: balance for a transfer, bytecode for a
   contract, a pinned block for a read.
4. Simulate before proposing anything: `blockchain.debug` `action=simulate_call`,
   or the simulation that `prepare_transaction` returns. A revert here is the
   cheapest possible place to learn.

If the chain anchor does not match, or the resolver refuses the candidate, stop and
report the mismatch. Do not fall back to a different endpoint, address, or ABI in
the same run.

# Reading a contract

- Call through resolution when the task gives a contract name: `contract_call` and
  `resolved_contract_call` carry `function_signature` from the resolved ABI, which
  the runtime checks for you.
- Call through an explicit ABI only when you were given the ABI or decoded it from
  verified source, using `contract_call` with a single function ABI object.
- Amounts are integers in base units. For an ERC-20 the base unit is the token's
  decimals, not the display unit: `1000000` of a six-decimal token is one token.
  Read `decimals()` and the user's intent before you build arguments, and state the
  conversion you used in the reply.
- Use `blockchain.debug action=decode_calldata` when a caller hands you hex and you
  need to know what it does before anything else.

# Writing, the only correct order

1. `blockchain.prepare_transaction` with the exact intent. It builds and simulates,
   returns the normalized transaction and a preparation digest, and does not
   broadcast.
2. Read the returned preview as a checklist, not a summary: chain, sender,
   recipient, value, nonce, transaction type, gas and fee figures, calldata size and
   digest, decoded function and arguments. If any field is not what you intended,
   prepare again instead of sending this one.
3. Request exactly one approval. The approval is consumed before the chain state is
   rechecked, so approving a preparation that has gone stale spends the approval and
   returns a stale-field refusal.
4. `blockchain.send_transaction` once. It revalidates the prepared payload, signs
   with the single configured signer, and broadcasts exactly once.
5. Follow the operation with `blockchain.inspect` `action=operation_status` using the
   preparation digest until the configured confirmation depth is reached.
6. Decode the receipt events you care about with `blockchain.debug`
   `action=transaction_events`, then read the resulting state.

# Refusals and what each one means

| Refusal | Meaning | Correct response |
| --- | --- | --- |
| stale fields, `nonce` | another transaction moved the account | prepare again from the new nonce |
| stale fields, `fees` | fee estimate now exceeds the configured cap | report the cap; do not raise it |
| stale fields, `chain_id` | the endpoint is no longer the resolved chain | stop; the resolution is invalid |
| `broadcast_unknown` | submission was marked but the RPC never confirmed acceptance | inspect the stored operation; never re-send |
| `pending` | accepted, no receipt yet | inspect by hash or digest; never re-send |
| `reorged` | a pre-depth receipt disappeared or changed | inspect again; report the reorg |
| `reverted` | mined with status 0 | quote the decoded revert; do not retry blind |
| `postcondition_failed` | confirmed but the stated equality did not hold | report the observed value against the expected one |

Pending, unknown, and reorged outcomes are not retryable. There is exactly one
broadcast per preparation, and a restart resumes the same operation rather than
sending a second one.

# Prohibitions

- Never put a private key, mnemonic, or keystore secret in tool arguments, prompts,
  files, or your reply. Signing uses the configured secret; you never see it.
- Never re-send a preparation because a receipt was slow. Inspect the operation.
- Never widen a cap, enable writes, or change the signer to get past a refusal.
  Report the refusal to the operator; those are their settings.
- Never resolve a contract from a link you did not read, and never treat a successful
  resolution as proof that a deployment is official.
- Never construct calldata by hand when an ABI is available, and never guess tuple
  order: tuples are JSON arrays in ABI component order.
- Never describe a pending or unknown write as done.

# Operating envelope for the operator

- Keep `writes_enabled` false until a write is actually needed.
- Use one signer per agent or per task, funded with what that task may spend. That
  funding is the real budget: the configured caps bound fees and, when set, value.
- Configure `rpc_url` and `chain_id` together, or use resolution alone. The pair is
  never a fallback for a researched resolution.
- Raise `confirmation_depth` above 1 on any network where a reorg would matter.
- Keep `max_total_fee_wei` tight, and set the value caps to the smallest amount the
  task legitimately needs.
- Prefer a disposable local chain (Anvil) or a testnet for anything new.
