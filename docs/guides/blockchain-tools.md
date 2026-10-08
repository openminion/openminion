# Blockchain Tools

OpenMinion provides five Ethereum/EVM tools when the optional blockchain
capability is enabled:

- `blockchain.resolve_contract` verifies one public RPC, researched chain
  identity, contract address, deployed code, direct or EIP-1967 lineage, and
  Sourcify ABI. It stores an immutable session resolution for later calls.
- `blockchain.inspect` reads chain, balance, bytecode, contract, transaction,
  and receipt facts. It can also read a resolved contract at a pinned block and
  inspect a submitted resolved operation by preparation digest.
- `blockchain.debug` simulates calls and decodes calldata, revert data, or
  events from one transaction receipt. It is read-only and never signs or
  sends.
- `blockchain.prepare_transaction` builds and simulates one native transfer,
  raw call, or contract call without broadcasting it.
- `blockchain.send_transaction` revalidates one prepared transaction, requires
  exact one-time approval, signs it, and submits it once.

The model can research an EVM network, public RPC, deployment, and readable
sources through the ordinary agent loop, then submit one candidate bundle to
the resolver. The runtime validates those facts; it does not choose endpoints,
retry another provider, or decide the next step. An optional configured EVM
network remains available for the original explicit-ABI tools. One configured
signer remains the only write identity.

## Install

Install the optional Web3.py dependency:

```bash
python -m pip install 'openminion[blockchain]'
```

## Configure autonomous read-only access

Add the blockchain block under the active profile's `runtime.tools` object:

```json
{
  "blockchain": {
    "enabled": true,
    "signer_secret_key": "",
    "signer_secret_namespace": "blockchain",
    "writes_enabled": false,
    "max_total_fee_wei": "10000000000000000",
    "receipt_timeout_seconds": 60,
    "confirmation_depth": 1
  }
}
```

Keep `writes_enabled` false for read-only use. Search, fetch, or browser tools
must also be available when the model needs to research a candidate. The user
does not need to configure a chain, RPC, explorer, contract address, or ABI for
the autonomous path.

The resolver accepts one model-researched candidate. The RPC must be public
HTTPS without credentials, a query, or a fragment. The request includes the
expected chain ID and genesis hash, an optional checkpoint, the contract
address, and bounded display/source URLs. OpenMinion pins the vetted endpoint,
checks chain identity and code lineage, and uses the fixed official Sourcify v2
lookup for the ABI. A successful result proves internal consistency; it does
not establish that a deployment or network is official.

Resolved reads and writes refer only to the stored resolution digest. They
cannot replace the endpoint, address, lineage, or ABI on a later call.

## Configure the explicit compatibility network

To keep using the original explicit-ABI inspect, debug, prepare, and send
requests, configure `rpc_url` and `chain_id` together:

```json
{
  "blockchain": {
    "enabled": true,
    "rpc_url": "http://127.0.0.1:8545",
    "chain_id": 31337,
    "writes_enabled": false
  }
}
```

Neither value may be configured alone. This pair is never an automatic
fallback for a researched resolution.

In Focus or chat, describe the outcome rather than using a command alias.
Examples:

```text
Find the verified deployment for Protocol X, cite the sources you used, and report its current APR.
What is the latest block and chain ID on the configured Ethereum blockchain?
Check the native balance of 0x... on the configured blockchain.
Check the receipt for transaction 0x...
```

## Configure local writes

Use a local Anvil chain and a disposable account first. Set
`OPENMINION_SECRET_KEY` to a Fernet key, then store the private key through the
existing `SecretService` under the same key and namespace named in config. The
private key must not appear in the config file, prompt, tool arguments, logs, or
evidence artifacts.

After the signer is stored, set:

```json
{
  "signer_secret_key": "local-anvil-signer",
  "signer_secret_namespace": "blockchain",
  "writes_enabled": true
}
```

Prepare first:

```text
Prepare and simulate, but do not send, 1 wei to 0x... on the configured EVM blockchain.
```

The result contains the complete normalized transaction, optional call context,
and preparation digest. A later send uses the latest preparation in the current
session by default; pass its digest only when selecting an earlier preparation.
OpenMinion resolves the exact prepared payload before requesting a one-time
`yes` or `no` decision. Approval is consumed before current chain state is
checked, so a stale preparation requires a new prepare-and-approve cycle.

The approval prompt shows the verified chain, sender, recipient, value,
transaction type, nonce, gas and fee values, calldata size and digest,
preparation digest, and decoded function call when a call ABI is available.
Opaque calldata is shown as bounded hex instead. Calldata over 4,096 bytes or a
configured-flow preview over 16,384 bytes is rejected before an approval is
created.

A resolved write uses `blockchain-send-preview-v2`, bounded to 65,536 bytes.
It also shows the researched RPC and Sourcify origins, expected and observed
chain anchors, resolution and preparation blocks, direct/proxy/implementation
identity, code hashes, signer, exact call and arguments, simulation facts, and
equality-only postconditions. It states that current state, nonce, simulation,
gas, receipt, and finality come from one researched RPC.

## Diagnose a call

`blockchain.debug` has exactly four actions:

- `simulate_call` runs one read-only call against a named block and returns
  gas, return bytes, optional decoded returns, or a structured revert.
- `decode_calldata` verifies a function selector and decodes its ordered input
  values using one supplied function ABI.
- `decode_revert` decodes standard errors, panics, or explicitly supplied
  custom-error ABIs.
- `transaction_events` decodes one supplied event ABI from one known
  transaction receipt.

Example requests:

```json
{"action":"simulate_call","from_address":"0x1111111111111111111111111111111111111111","to_address":"0x2222222222222222222222222222222222222222","data":"0x","value_wei":"0","block_identifier":"pending"}
{"action":"decode_calldata","function_abi":{"type":"function","name":"balanceOf","inputs":[{"name":"owner","type":"address"}],"outputs":[{"name":"","type":"uint256"}],"stateMutability":"view"},"data":"0x70a082310000000000000000000000001111111111111111111111111111111111111111"}
{"action":"decode_revert","data":"0x08c379a0"}
{"action":"transaction_events","transaction_hash":"0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","event_abi":{"type":"event","name":"Swap","inputs":[{"name":"sender","type":"address","indexed":true},{"name":"amountOut","type":"uint256","indexed":false}],"anonymous":false}}
```

Tuple values are JSON arrays in ABI component order, including nested tuples
and tuple arrays. Objects, guessed field order, and value coercion are not
accepted. Function, error, and event ABIs describe one item rather than a whole
contract ABI.

Debug requests and responses are limited to 65,536 serialized bytes. Event
decoding is limited to 100 matching events from the named receipt. Results are
rejected when a limit is exceeded; they are not silently truncated.

## Build a local transaction flow

On Anvil or another disposable local EVM chain, the five tools can be composed
without a separate workflow layer:

1. inspect the chain, account, contract, and quote;
2. debug a call or revert with an explicit ABI;
3. prepare and simulate one transaction;
4. review and allow once or deny the complete approval preview;
5. inspect the operation by preparation digest until the configured
   confirmation depth is reached; and
6. decode the receipt event and read the resulting contract state.

If receipt status is temporarily unavailable after one submission, inspect by
the returned hash. Do not send the prepared transaction again.

## Terminal states

The configured compatibility send keeps its original receipt states.

A resolved send creates a durable operation before its single permitted
broadcast. `blockchain.inspect` with `action=operation_status` returns:

- `broadcast_unknown` when submission was marked but RPC acceptance is unknown;
- `pending` when the exact signed hash was accepted without a receipt;
- `confirming` while a canonical receipt is below the stored depth;
- `succeeded` after a status-1 receipt reaches depth and postconditions match;
- `reverted` after a status-0 receipt reaches depth;
- `reorged` when a pre-depth receipt disappears or changes; or
- `postcondition_failed` when a confirmed status-1 receipt does not satisfy an
  exact equality postcondition.

Pending and unknown outcomes are non-retryable. Inspect the stored operation;
never send the preparation again. Restart loads the same endpoint, resolution,
preparation, and precomputed transaction hash without new research or a second
broadcast.

## Safety boundary

- Keep public-mainnet writes disabled. Use local Anvil or an explicitly
  authorized disposable testnet for write qualification.
- Use the fee cap to bound the maximum estimated transaction fee.
- Treat every send as irreversible and verify chain, sender, recipient, value,
  calldata, and fee fields in the approval preview.
- Never paste a private key, mnemonic, or secret reference into a model prompt.

## Before production use

The resolver supports direct contracts and the EIP-1967 implementation slot.
It does not support other proxy families, arbitrary metadata providers,
private/authenticated RPC endpoints, or endpoint failover. Current facts and
finality come from one researched RPC. Trading-specific work still needs
reviewed quoting, token and allowance handling, slippage and deadline controls,
chain-specific fee policy, disposable-testnet evidence, monitoring, and
operator runbooks. Keep public-mainnet writes disabled until those owners and
acceptance gates exist.
