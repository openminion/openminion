---
name: blockchain-evm
description: Operate the OpenMinion EVM tools safely, from research to one approved send.
tools: [blockchain.resolve_contract, blockchain.inspect, blockchain.debug, blockchain.prepare_transaction, blockchain.send_transaction]
tags: [blockchain, evm, smart-contract, transaction]
applies_to: [read-only research, contract inspection, simulation, single approved write]
preflight: [blockchain.inspect action=chain_summary, blockchain.resolve_contract, blockchain.debug action=simulate_call]
stop_conditions: [unresolved contract, chain anchor mismatch, pending or unknown broadcast, fee cap refusal, revert]
version: 1
---

# Blockchain EVM

Use this skill when a task names a chain, a contract, a transaction, a balance, or a
call result. The tools are structural: they verify what they are given and refuse
what they cannot anchor. Your job is to supply researched facts, read the refusals
literally, and never improvise a second broadcast.

# Two routes, two owners

- Configured: requires `rpc_url` and `chain_id` together. `blockchain.inspect`
  supports `chain_summary`, `native_balance`, `bytecode`, `contract_read`,
  `transaction`, and `receipt`; `blockchain.debug` supports its four actions;
  `blockchain.prepare_transaction` supports `native_transfer`, `raw_call`, and
  `contract_call`; and `blockchain.send_transaction` submits the prepared result.
- Resolved: requires a stored resolution and does not require a configured
  network. It supports inspect `resolved_contract_call` and `operation_status`,
  and prepare kind `resolved_contract_call`; `blockchain.send_transaction`
  submits the prepared result. The resolver takes your researched
  `expected_chain_id` and `expected_genesis_hash`, checks them against the RPC,
  and stores the result behind a `resolution_digest`.

A resolution-only configuration returns `FEATURE_UNAVAILABLE` for every
configured action, including `chain_summary`. Do not invent an action, an
endpoint, or an implicit fallback to work around that refusal.

# Preflight, before any write

1. On a configured network, read the chain with `blockchain.inspect`
   `action=chain_summary`. It returns `chain_id` and `latest_block_number`, not a
   genesis hash. Compare the chain id with the chain the user named.
2. Resolve the contract with `blockchain.resolve_contract` when the task gives a
   name, a protocol, or a source rather than an ABI. Supply the expected chain id
   and the expected genesis hash you researched, the address, a public HTTPS RPC
   without credentials or query string, and the display and source URLs you
   actually read. The resolver checks the expected chain id and genesis hash
   against observed values and pins one endpoint, address, lineage and ABI behind
   a digest.
3. Inspect what you intend to touch: balance for a transfer, bytecode for a
   contract, a pinned block for a read.
4. Simulate before proposing anything: `blockchain.debug` `action=simulate_call`
   on a configured network, or the simulation that `prepare_transaction` returns.
   A revert here is the cheapest possible place to learn.

If the chain anchor does not match, or the resolver refuses the candidate, stop and
report the mismatch. Do not fall back to a different endpoint, address, or ABI in
the same run.

# Reading a contract

- The configured read is `blockchain.inspect` `action=contract_read` with
  `contract_address`, one `function_abi` function object, and `function_args` in
  ABI input order. It returns the encoded `function_signature` and
  `return_values`:

```json
{"action":"contract_read","contract_address":"0x2222222222222222222222222222222222222222","function_abi":{"type":"function","name":"balanceOf","inputs":[{"name":"owner","type":"address"}],"outputs":[{"name":"","type":"uint256"}],"stateMutability":"view"},"function_args":["0x1111111111111111111111111111111111111111"]}
```

- The resolved read is `blockchain.inspect` `action=resolved_contract_call` with
  the `resolution_digest` returned by the resolver, an exact
  `function_signature` copied from `available_function_signatures`, and
  `arguments` in ABI input order. It takes no `contract_address` or
  `function_abi`:

```json
{"action":"resolved_contract_call","resolution_digest":"sha256:1212121212121212121212121212121212121212121212121212121212121212","function_signature":"balanceOf(address)","arguments":["0x1111111111111111111111111111111111111111"]}
```

- `contract_call` is a prepare kind, not an inspect action. Configured prepare
  uses `function_abi` and `function_args`; resolved prepare uses
  `resolution_digest`, `function_signature`, and `arguments`.
- Amounts are integers in base units. `value_wei` is native value. A token amount
  is a function argument in the token's base units: `1000000` of a six-decimal
  token is one token. An allowance is not native value and not a spend budget.
  Read `decimals()` and the user's intent before you build arguments, and state
  the conversion you used in the reply.
- On a configured network, use `blockchain.debug action=decode_calldata` when a
  caller hands you hex and you need to know what it does before anything else.

# Writing, the only correct order

1. `blockchain.prepare_transaction` with the exact intent. It builds and
   simulates, returns the normalized transaction and a preparation digest, and
   does not broadcast.
2. Read the returned preview as a checklist, not a summary: chain, sender,
   recipient, value, nonce, transaction type, gas and fee figures, calldata size
   and digest, decoded function and arguments. If any field is not what you
   intended, prepare again instead of sending this one.
3. Request exactly one approval. The approval is consumed before the chain state
   is rechecked, so approving a preparation that has gone stale spends the
   approval and returns a stale-field refusal.
4. `blockchain.send_transaction` once. Do not call it again for the same
   preparation. It revalidates the prepared payload, signs with the single
   configured signer, and broadcasts at most once per approved send.
5. Follow the result by route. After a resolved send, poll `blockchain.inspect`
   `action=operation_status` with the `preparation_digest` until the stored
   operation reaches the configured `confirmation_depth`. After a configured
   send, inspect `action=receipt` with the returned `transaction_hash` when the
   result is pending or unknown. The configured send returns receipt facts and
   writes an audit event, but it does not create the durable operation record
   that `operation_status` reads.
6. Decode the receipt events you care about with `blockchain.debug`
   `action=transaction_events`, then read the resulting state.

# Refusals and what each one means

| Refusal | Meaning | Correct response |
| --- | --- | --- |
| stale fields, `nonce` | another transaction moved the account | prepare again from the new nonce |
| stale fields, `fees` | fee estimate now exceeds the configured `max_total_fee_wei` | report the cap; do not raise it |
| stale fields, `chain_id` | the endpoint no longer matches the pinned or configured chain | stop; the chain anchor is invalid |
| `broadcast_unknown` | submission was marked but the RPC never confirmed acceptance | resolved: inspect `operation_status`; configured: inspect `receipt` by the returned hash; never re-send |
| `pending` | accepted, no receipt yet | resolved: poll `operation_status`; configured: poll `receipt`; never re-send |
| `reorged` | only a resolved operation reports this state | inspect again; report the reorg |
| `reverted` | mined with status 0 | quote the decoded revert; do not retry blind |
| `postcondition_failed` | a resolved operation confirmed but the stated equality did not hold | report the observed value against the expected one |

Pending and unknown outcomes are not retryable. The resolved route creates a
durable operation before its single permitted broadcast, `operation_status` reads
that record, and a restart resumes status without new research or a second
broadcast. The configured route does not create that record: it writes an audit
event and returns its receipt, pending, or unknown state, so keep the returned
transaction hash and inspect it. Never re-send a preparation because a receipt
was slow.

# Prohibitions

- Never put a private key, mnemonic, or keystore secret in tool arguments, prompts,
  files, or your reply. Signing uses the configured secret; you never see it.
- Never re-send a preparation because a receipt was slow. Inspect the resolved
  operation or the returned hash.
- Never widen a cap, enable writes, or change the signer to get past a refusal.
  Report the refusal to the operator; those are their settings.
- Never resolve a contract from a link you did not read, and never treat a successful
  resolution as proof that a deployment is official.
- Never construct calldata by hand when an ABI is available, and never guess tuple
  order: tuples are JSON arrays in ABI component order.
- Never describe a pending or unknown write as done.

# Operating envelope for the operator

- Keep `writes_enabled` false until a write is actually needed. While it is false,
  `blockchain.send_transaction` refuses at runtime with `FEATURE_DISABLED`; the
  tool stays registered.
- Use one signer per agent or per task, funded with what that task may spend. The
  wallet balance is the real budget. `max_total_fee_wei` bounds the estimated fee;
  there is no per-transaction native-value cap and no cumulative spend budget.
- Configure `rpc_url` and `chain_id` together, or use resolution alone. The pair
  is never a fallback for a researched resolution.
- Raise `confirmation_depth` above 1 on any network where a reorg would matter.
  It applies to the resolved operation status.
- Keep `max_total_fee_wei` tight; it bounds fees only, not value or a budget.
- Prefer a disposable local chain (Anvil) or a testnet for anything new.
