# Blockchain Tools

OpenMinion provides three Ethereum/EVM tools when the optional blockchain
capability is enabled:

- `blockchain.inspect` reads chain, balance, bytecode, contract, transaction,
  and receipt facts.
- `blockchain.prepare_transaction` builds and simulates one native transfer,
  raw call, or contract call without broadcasting it.
- `blockchain.send_transaction` revalidates one prepared transaction, requires
  exact one-time approval, signs it, and submits it once.

The first version supports one configured EVM network and one signer. It does
not provide swaps, bridges, deployment, batching, automatic retries, or
multi-chain routing.

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
