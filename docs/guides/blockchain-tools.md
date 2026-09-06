# Blockchain Tools

OpenMinion provides four Ethereum/EVM tools when the optional blockchain
capability is enabled:

- `blockchain.inspect` reads chain, balance, bytecode, contract, transaction,
  and receipt facts.
- `blockchain.debug` simulates calls and decodes calldata, revert data, or
  events from one transaction receipt. It is read-only and never signs or
  sends.
- `blockchain.prepare_transaction` builds and simulates one native transfer,
  raw call, or contract call without broadcasting it.
- `blockchain.send_transaction` revalidates one prepared transaction, requires
  exact one-time approval, signs it, and submits it once.

The current foundation supports one configured EVM network and one signer. It
does not provide a trading strategy, swap or bridge adapter, deployment,
batching, automatic retry, or multi-chain routing.

## Install

Install the optional Web3.py dependency:

```bash
python -m pip install 'openminion[blockchain]'
```

## Configure read-only access

Add the blockchain block under the active profile's `runtime.tools` object:

```json
{
  "blockchain": {
    "enabled": true,
    "rpc_url": "http://127.0.0.1:8545",
    "chain_id": 31337,
    "signer_secret_key": "",
    "signer_secret_namespace": "blockchain",
    "writes_enabled": false,
    "max_total_fee_wei": "10000000000000000",
    "receipt_timeout_seconds": 60
  }
}
```

Keep `writes_enabled` false for read-only use. The model receives the configured
chain ID but never the RPC URL or secret reference.

In Focus or chat, ask for the configured chain rather than using a command
alias. Examples:

```text
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
and preparation digest. Send that exact result in a later tool call. OpenMinion
requires a one-time `yes` or `no` decision for every send. Approval is consumed
before current chain state is checked, so a stale preparation requires a new
prepare-and-approve cycle.

The approval prompt shows the verified chain, sender, recipient, value,
transaction type, nonce, gas and fee values, calldata size and digest,
preparation digest, and decoded function call when a call ABI is available.
Opaque calldata is shown as bounded hex instead. Calldata over 4,096 bytes or a
serialized preview over 16,384 bytes is rejected before an approval is created.

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

On Anvil or another disposable local EVM chain, the four tools can be composed
without a separate workflow layer:

1. inspect the chain, account, contract, and quote;
2. debug a call or revert with an explicit ABI;
3. prepare and simulate one transaction;
4. review and allow once or deny the complete approval preview;
5. inspect the returned transaction hash and receipt; and
6. decode the receipt event and read the resulting contract state.

If receipt status is temporarily unavailable after one submission, inspect by
the returned hash. Do not send the prepared transaction again.

## Terminal states

`blockchain.send_transaction` broadcasts at most once. Its terminal state is:

- `succeeded` when a successful receipt is observed;
- `reverted` when a mined receipt has status `0`;
- `pending` when submission succeeded but the receipt is not available;
- `broadcast_unknown` when submission outcome is unknown;
- `stale` when the prepared transaction no longer matches current chain state;
- `failed` for a typed pre-broadcast failure.

Pending and unknown outcomes are non-retryable. Inspect the transaction or
receipt by hash instead of sending again.

## Safety boundary

- Keep mainnet writes disabled. This capability's required write validation is
  local Anvil only.
- Use the fee cap to bound the maximum estimated transaction fee.
- Treat every send as irreversible and verify chain, sender, recipient, value,
  calldata, and fee fields in the approval preview.
- Never paste a private key, mnemonic, or secret reference into a model prompt.

## Before production use

The local foundation is not production trading support. A production rollout
still needs separately reviewed protocol-specific quoting and transaction
construction, token and allowance handling, slippage and deadline controls,
fork or testnet evidence, chain-specific fee and finality policy, monitored RPC
operations, and operator runbooks. Keep public-mainnet writes disabled until
those owners and acceptance gates exist.
