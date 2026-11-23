# Blockchain Tools

OpenMinion provides five Ethereum/EVM tools when the optional blockchain
capability is enabled:

- `blockchain.resolve_contract` verifies one public RPC, researched chain
  identity, contract address, deployed code, direct or EIP-1967 lineage, and
  Sourcify ABI. It stores an immutable session resolution for later calls.
- `blockchain.inspect` reads a configured chain summary (`chain_id` and
  `latest_block_number`), balance, bytecode, contract, transaction, and receipt
  facts. It can also read a resolved contract at a pinned block and inspect a
  submitted resolved operation by preparation digest.
- `blockchain.debug` simulates calls and decodes calldata, revert data, or
  events from one transaction receipt. It is read-only and never signs or
  sends.
- `blockchain.prepare_transaction` builds and simulates one native transfer,
  raw call, or contract call without broadcasting it.
- `blockchain.send_transaction` revalidates one prepared transaction, requires
  exact one-time approval, signs it, and submits it at most once per approved
  call.

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
address, and bounded display/source URLs. The configured chain summary returns
the chain ID and latest block, not a genesis hash; the expected genesis hash is
a researched input to the resolver. OpenMinion pins the vetted endpoint, checks
chain identity and code lineage, and uses the fixed official Sourcify v2 lookup
for the ABI. A successful result proves internal consistency; it does not
establish that a deployment or network is official.

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
5. inspect the result by route: `action=operation_status` by preparation digest
   for a resolved send, `action=receipt` by the returned hash for a configured
   send; and
6. decode the receipt event and read the resulting contract state.

If receipt status is temporarily unavailable after one submission, inspect by
the returned hash. Do not send the prepared transaction again.

## Terminal states

The configured compatibility send returns its original receipt states and
writes an audit event. It does not create the durable operation record that
`operation_status` reads, so follow a configured send with `blockchain.inspect`
`action=receipt` and the returned transaction hash.

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

Pending and unknown outcomes are non-retryable. For a resolved send, inspect the
stored operation; never send the preparation again. A resolved-send restart
loads the same endpoint, resolution, preparation, and precomputed transaction
hash without new research or a second broadcast.

## Disposable-testnet acceptance

The opt-in testnet runner is an acceptance tool, not a general transaction
command. Copy `tests/e2e/fixtures/blockchain/testnet_write.example.json` to the
approved external attempt parent, then edit that copy with the approved
disposable signer and observed balance. Use a non-approval mutation such as
the one-wei WETH `deposit()` shown there. Do not edit or run the tracked example
in place.

Before the first network call, the runner requires
`OPENMINION_BLOCKCHAIN_TESTNET_WRITE_APPROVED_` values for the source commit,
config SHA-256, chain ID, genesis hash, checkpoint digest, RPC origin, signer,
contract, function signature, argument digest, value, fee cap, confirmation
depth, duration, precondition digest, postcondition digest, attempt ID, parent
root, attempt root, one mutation invocation, and one maximum broadcast. The
config supplies the exact public inputs and names the environment variable
that contains the caller-owned private key. Both the edited config and attempt
root must be outside the OpenMinion checkout. The attempt root must not exist
on first entry and is created owner-only. From the OpenMinion checkout, create
the external paths under the umbrella scratch tree:

```bash
OPENMINION_ROOT="$(git rev-parse --show-toplevel)"
ATTEMPT_PARENT="$(dirname "$OPENMINION_ROOT")/workspace-tmp/blockchain-testnet-write-$ATTEMPT_ID"
WRITE_CONFIG="$ATTEMPT_PARENT/testnet-write.json"
ATTEMPT_ROOT="$ATTEMPT_PARENT/$ATTEMPT_ID"
mkdir -p "$ATTEMPT_PARENT"
cp tests/e2e/fixtures/blockchain/testnet_write.example.json "$WRITE_CONFIG"
chmod 700 "$ATTEMPT_PARENT"
chmod 600 "$WRITE_CONFIG"
```

Edit only `$WRITE_CONFIG`, then review and freeze it before continuing.

From a clean committed OpenMinion checkout, derive the five JSON/config
fingerprints from the exact reviewed config:

```bash
PY=.venv/bin/python3.11
export OPENMINION_BLOCKCHAIN_TESTNET_WRITE_CONFIG="$WRITE_CONFIG"
PYTHONPATH=src "$PY" - <<'PY'
from tests.e2e.runners.run_blockchain_testnet_write import (
    _canonical_digest,
    _required_config,
)

_, payload, config_sha256 = _required_config()
prepare = payload["prepare_request"]
print(f"CONFIG_SHA256={config_sha256}")
print(f"CHECKPOINT_DIGEST={_canonical_digest(payload['resolve_request'].get('expected_checkpoint'))}")
print(f"ARGUMENTS_DIGEST={_canonical_digest(prepare['arguments'])}")
print(f"PRECONDITION_DIGEST={_canonical_digest(payload['precondition'])}")
print(f"POSTCONDITION_DIGEST={_canonical_digest(prepare['postconditions'][0])}")
PY
```

Freeze those outputs and every other reviewed value, then run the initial
attempt with the exact environment below. The private-key variable itself is
the name in `signer_secret_env`; set it in the parent environment without
putting the value in shell history.

```bash
OPENMINION_BLOCKCHAIN_TESTNET_WRITE_APPROVED_SOURCE_COMMIT="$APPROVED_SHA" \
OPENMINION_BLOCKCHAIN_TESTNET_WRITE_APPROVED_CONFIG_SHA256="$CONFIG_SHA256" \
OPENMINION_BLOCKCHAIN_TESTNET_WRITE_APPROVED_PARENT_ROOT="$ATTEMPT_PARENT" \
OPENMINION_BLOCKCHAIN_TESTNET_WRITE_APPROVED_ATTEMPT_ROOT="$ATTEMPT_ROOT" \
OPENMINION_BLOCKCHAIN_TESTNET_WRITE_APPROVED_CHAIN_ID="$CHAIN_ID" \
OPENMINION_BLOCKCHAIN_TESTNET_WRITE_APPROVED_GENESIS_HASH="$GENESIS_HASH" \
OPENMINION_BLOCKCHAIN_TESTNET_WRITE_APPROVED_CHECKPOINT_DIGEST="$CHECKPOINT_DIGEST" \
OPENMINION_BLOCKCHAIN_TESTNET_WRITE_APPROVED_RPC_ORIGIN="$RPC_ORIGIN" \
OPENMINION_BLOCKCHAIN_TESTNET_WRITE_APPROVED_SIGNER_ADDRESS="$SIGNER_ADDRESS" \
OPENMINION_BLOCKCHAIN_TESTNET_WRITE_APPROVED_CONTRACT_ADDRESS="$CONTRACT_ADDRESS" \
OPENMINION_BLOCKCHAIN_TESTNET_WRITE_APPROVED_FUNCTION_SIGNATURE="$FUNCTION_SIGNATURE" \
OPENMINION_BLOCKCHAIN_TESTNET_WRITE_APPROVED_ARGUMENTS_DIGEST="$ARGUMENTS_DIGEST" \
OPENMINION_BLOCKCHAIN_TESTNET_WRITE_APPROVED_VALUE_WEI="$VALUE_WEI" \
OPENMINION_BLOCKCHAIN_TESTNET_WRITE_APPROVED_MAX_TOTAL_FEE_WEI="$MAX_TOTAL_FEE_WEI" \
OPENMINION_BLOCKCHAIN_TESTNET_WRITE_APPROVED_CONFIRMATION_DEPTH="$CONFIRMATION_DEPTH" \
OPENMINION_BLOCKCHAIN_TESTNET_WRITE_APPROVED_MAX_DURATION_SECONDS="$MAX_DURATION_SECONDS" \
OPENMINION_BLOCKCHAIN_TESTNET_WRITE_APPROVED_PRECONDITION_DIGEST="$PRECONDITION_DIGEST" \
OPENMINION_BLOCKCHAIN_TESTNET_WRITE_APPROVED_POSTCONDITION_DIGEST="$POSTCONDITION_DIGEST" \
OPENMINION_BLOCKCHAIN_TESTNET_WRITE_APPROVED_ATTEMPT_ID="$ATTEMPT_ID" \
OPENMINION_BLOCKCHAIN_TESTNET_WRITE_APPROVED_OPERATOR_ID="$OPERATOR_ID" \
OPENMINION_BLOCKCHAIN_TESTNET_WRITE_APPROVED_MUTATION_INVOCATIONS=1 \
OPENMINION_BLOCKCHAIN_TESTNET_WRITE_APPROVED_MAX_BROADCASTS=1 \
OPENMINION_BLOCKCHAIN_TESTNET_WRITE_CONFIG="$WRITE_CONFIG" \
OPENMINION_BLOCKCHAIN_TESTNET_WRITE_E2E=1 \
  "$PY" tests/e2e/runners/run_blockchain_testnet_write.py
```

Use the same command for status-only recovery, with the private-key variable
unset. If a hard stop occurred before the runner wrote `secret-scan.json`, the
caller must first scan the exact attempt root for the key bytes and supply
`OPENMINION_BLOCKCHAIN_TESTNET_WRITE_SCAN_ATTESTATION` pointing to an owner-only
JSON file with this closed shape:

```json
{
  "schema_version": "blockchain-testnet-secret-scan-v1",
  "attempt_id": "the approved attempt ID",
  "private_root_identifier": "sha256: canonical JSON digest of the normalized absolute attempt-root string",
  "signer_address": "the approved public signer",
  "operator_id": "the approved operator ID",
  "secret_absent": true,
  "source": "caller_recovery",
  "scanned_at": "an ISO-8601 UTC timestamp"
}
```

After preparation, the runner writes `attempt.json` before its single send.
Restart behavior is intentionally narrow:

- a root without a complete manifest is abandoned;
- a prepared manifest without a production operation record closes as
  `not_submitted`; and
- an operation record with `submission_started=true` resumes by status only,
  without loading the signer or sending again.

The runner retains only public facts and digests in the manifest and scans its
artifacts for the loaded key without printing matched content. After terminal
receipt and evidence verification, the operator deletes the caller-owned key
and records the following outside the attempt root as
`$ATTEMPT_PARENT/$ATTEMPT_ID-key-deletion.json`; the runner does not claim
deletion:

```json
{
  "attempt_id": "the approved attempt ID",
  "signer_address": "the approved public signer",
  "secret_source_kind": "file or parent-shell environment",
  "deletion_time": "an ISO-8601 UTC timestamp",
  "operator_identity": "the approved operator ID"
}
```

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

## Ship it to an agent as a skill

The tools are the capability; the procedure still has to be taught. Ingest the
bundled skill so the model reads the write order, the refusal table, and the
prohibitions before it touches a chain:

```bash
openminion skill ingest --name blockchain-evm \
  --file docs/skills/blockchain-evm/SKILL.md
```

Skills are immutable, versioned, and admitted by the operator; bundled scripts are
stored with `executable=false` and never run, so a skill cannot act on its own
authority. It only changes what the model knows, never what the runtime permits.

Give the agent an envelope at the same time, because the skill can only describe
these settings, not change them:

| Setting | Safe starting point | Why it matters |
| --- | --- | --- |
| `writes_enabled` | `false` until a write is needed | send refuses at runtime; the tool stays registered |
| signer | one key per agent or task, funded with what that task may spend | the wallet balance is the real budget |
| `rpc_url` / `chain_id` | set together, or omit both and use resolution | a lone value is rejected, and the pair never overrides a resolution |
| `confirmation_depth` | `1` on a disposable chain, more where reorgs matter | controls when a resolved operation is reported final |
| `max_total_fee_wei` | the smallest fee the task tolerates | bounds fee spend per transaction |

OpenMinion does not yet have a per-transaction value cap or a cumulative spend
budget; the approval preview and the funded wallet are the current limits. State
that plainly to an operator rather than implying a monetary cap exists.
