# IXH26 Advanced Cryptography Track - Perugia Team

The infrastructure is built as a collection of independent containers, using `Docker Compose`.
Each container has a single responsability:

- **xrpl-node**: use the official `rippleci`'s xrpld latest image, running a node in standlone mode. This is used to run a local XRPL blockchain.
- **backblock**: is the intermediary between any requester and the xrpl node. It leverages on the official `xrpl-py` client to connect to the blockchain and run commands required by the algorithm.
- **sensor-simulator**: simulates the behavior of a sensor, signing images with the exposed algorithm. It runs indefinitly, waiting for someone to connect through `docker exec` and execute the requested command. It also offers the feature to verify an image (i.e. verify its signature and the sensor identity by fetching the merkle root of the vendor in the corresponding blockchain transaction)

The images can be built with:

```bash
docker compose build --no-cache # Omit no-cache if the code hasn't changed
```

## Running & using the distributed environment

The environment can be started using the command:

```bash
docker compose -f docker-compose.yaml up -d
```

This starts all the container declared before. The running status can be verified using `docker ps -a`.

If any image were rebuilt, use instead the command:

```bash
docker compose up -d --force-recreate
```

### Signing process

To simulate the signing process, enter the sensor container using the command `docker exec -it sensor /bin/bash`. This will start a bash shell inside the container where the sensor mock module has been injected by the docker compose command.

Next, we can execute the desired function:

1. Assure that the path is where the `.py` module is located (i.e. _/app/src_).
2. Execute the module using `python sensor.py sign [-o <output_image>] [input_image]`.

For instance, `python sensor.py sign -o signed.png input.png`. This will use default sample data for omitted fields. As explained, the signature includes also that to calculate a merkle root and compare it against the in-blockchain published one.

You can use a more granular sensor simulation, with custom data for that fields, using:

```bash
python sensor_simulator.py sign ./data/input.png \
  -o ./data/signed.png \
  --keypair ./data/sensor_01.json \
  --tx-hash "A1B2C3D4E5F60718293A4B5C6D7E8F90123456789ABCDEF0123456789ABCDEF0" \
  --merkle-proof-json '[{"position":"right","hash":"1111111111111111111111111111111111111111111111111111111111111111"}]'
```

where:

- `--keypair` is where keys are located.
- `--tx-hash` is the hash of the blockchain transaction published by the vendor that contains the true merkle root.
- `--merkle-proof-json` is the data used to compute locally the merkle root.

All of these pieces of information are stored inside the sensor in a real case scenario.

> [!NOTE]
> This container, by default, mounts (through the docker compose configuration) the _/sensor_simulator/data_ folder in _/app/src/data_ container's memory. It can be used to quickly move image to sign/verify to container's memory.

### Verification process

Similarly, to verify an image (i.e. verifying its signature is correct and the corresponding XRPL transaction exists and contains a merkle tree root that matches the one within the signed image, published by a vendor _X_), use the command:

```bash
python sensor.py verify <image_to_verify>
```

### Creating an account on XRPL

To simulate the vendor that creates an account, we have to contact the blockchain proxy server, `backblock`. We can do it through postman or via the CLI using the curl command:

```bash
curl -i -X POST http://localhost:5000/account/generate
```

That will answer something like:

```json
{
    "classic_address": "rpGY1xm8WRH69KjRTG1ZQj5AUVDPX6hpXg",
    "public_key": "ED1DD7C00ED0127C0D34C7A5B7BEA7D538D1B7E5E86E141C4BBE072035A3A71323",
    "private_key": "EDE6E98BDF7A749D4D67EC81B8B1F7350194A22242E7604116B8B201CEC1FA0221",
    "seed": "sEd7EDMiCJQjyMLSspxfA5FdgWcbaxQ"
}
```

> [!IMPORTANT]
> Rememeber that any command can be substitued by postman or any other http client.

As usual in XRPL, the account to be active must receive an initial fund; we can do that with the command:

```bash
curl -s -X POST http://localhost:5000/account/fund \
  -H "Content-Type: application/json" \
  -d '{
    "destination_address": "<vendor-address>",
    "amount_xrp": 100.0,
    "advance_ledger": true
  }'
```

Using as destination address the one from the previous example, we will obtain a successfull result, like:

```json
{
    "status": "funded",
    "tx_hash": "4359D717F72AF4B076752416F6207F0B2A3F0146C7B7B56D7BE47B4C46922457",
    "submit_result": {
        "accepted": true,
        "account_sequence_available": 2,
        "account_sequence_next": 2,
        "applied": true,
        "broadcast": true,
        "engine_result": "tesSUCCESS",
        "engine_result_code": 0,
        "engine_result_message": "The transaction was applied. Only final in a validated ledger.",
        "kept": true,
        "open_ledger_cost": "10",
        "queued": false,
        "tx_blob": "1200002400000001201B00000016614000000005F5E10068400000000000000A73210330E7FC9D56BB25D6893BA3F317AE5BCF33B3291BD63DB32654A313222F7FD02074473045022100BBCE875741467904072C63B69E997F631C60A1A6F039FF13CF0612A040F39FB802206A19A59F6FC1DB7548BB2E992302124C7B93907988DAB4FDA944C58C9CFF9A668114B5F762798A53D543A014CAF8B297CFF8F2F937E883140DE8F9E6027273B21D707F7DD32DB460C66BD9A8",
        "tx_json": {
            "Account": "rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh",
            "Amount": "100000000",
            "Destination": "rpGY1xm8WRH69KjRTG1ZQj5AUVDPX6hpXg",
            "Fee": "10",
            "LastLedgerSequence": 22,
            "Sequence": 1,
            "SigningPubKey": "0330E7FC9D56BB25D6893BA3F317AE5BCF33B3291BD63DB32654A313222F7FD020",
            "TransactionType": "Payment",
            "TxnSignature": "3045022100BBCE875741467904072C63B69E997F631C60A1A6F039FF13CF0612A040F39FB802206A19A59F6FC1DB7548BB2E992302124C7B93907988DAB4FDA944C58C9CFF9A66",
            "hash": "4359D717F72AF4B076752416F6207F0B2A3F0146C7B7B56D7BE47B4C46922457"
        },
        "validated_ledger_index": 2
    },
    "ledger_accept_result": {
        "ledger_current_index": 4
    }
}
```

### Publishing a merkle root on blockchain

A sample snippet to calculate the merkle root of a tree has been placed in [root](./merkle_root_calculator.py).

Following the previous example for values, the root, once calculated, can be pushed with:

```bash
curl -s -X POST http://localhost:5000/blockchain/merkle-root \
  -H "Content-Type: application/json" \
  -d '{
    "producer_seed": "sEd7EDMiCJQjyMLSspxfA5FdgWcbaxQ",
    "merkle_root_hex": "54b2913d967d6c6e07e4bcd9bd7f81912331e84c052edf65a68fe146c8893d9b",
    "batch_id": "batch-001",
    "advance_ledger": true
  }'
```

And the corresponding answer:

```json
{
    "status": "submitted_and_accepted",
    "tx_hash": "8FB39BD498BB1A7B2332C41A34416074C591C5736BCCE813E9AAE3D118D2298C",
    "producer_address": "rpGY1xm8WRH69KjRTG1ZQj5AUVDPX6hpXg",
    "memo_payload": {
        "type": "sensor_batch_merkle_root",
        "merkle_root": "54b2913d967d6c6e07e4bcd9bd7f81912331e84c052edf65a68fe146c8893d9b",
        "batch_id": "batch-001"
    },
    "submit_result": {
        "accepted": true,
        "account_sequence_available": 5,
        "account_sequence_next": 5,
        "applied": true,
        "broadcast": true,
        "engine_result": "tesSUCCESS",
        "engine_result_code": 0,
        "engine_result_message": "The transaction was applied. Only final in a validated ledger.",
        "kept": true,
        "open_ledger_cost": "10",
        "queued": false,
        "tx_blob": "1200032400000004201B0000001868400000000000000A7321ED619621D493091EE1F1446A295332574C74BAD506AA6FEDC605A8133316CC36A37440E5DD0161AEA2A790EC10041B638E6E42F6E1EEE8345B8D8A4562557D974CD59EC29BA72B524BDA9BB8D5AD8BF366D954848740C4634EA97140C0712F45600C0C81143B312E2684D8ADCC7C4EFE72FB2757D3F0EC3689F9EA7C1169786832362F6D65726B6C652D726F6F747D8B7B2274797065223A2273656E736F725F62617463685F6D65726B6C655F726F6F74222C226D65726B6C655F726F6F74223A2235346232393133643936376436633665303765346263643962643766383139313233333165383463303532656466363561363866653134366338383933643962222C2262617463685F6964223A2262617463682D303031227DE1F1",
        "tx_json": {
            "Account": "raPy8RZQLDhHBJQm2hHDKRk3gggC6uyVQz",
            "Fee": "10",
            "LastLedgerSequence": 24,
            "Memos": [
                {
                    "Memo": {
                        "MemoData": "7B2274797065223A2273656E736F725F62617463685F6D65726B6C655F726F6F74222C226D65726B6C655F726F6F74223A2235346232393133643936376436633665303765346263643962643766383139313233333165383463303532656466363561363866653134366338383933643962222C2262617463685F6964223A2262617463682D303031227D",
                        "MemoType": "69786832362F6D65726B6C652D726F6F74"
                    }
                }
            ],
            "Sequence": 4,
            "SigningPubKey": "ED619621D493091EE1F1446A295332574C74BAD506AA6FEDC605A8133316CC36A3",
            "TransactionType": "AccountSet",
            "TxnSignature": "E5DD0161AEA2A790EC10041B638E6E42F6E1EEE8345B8D8A4562557D974CD59EC29BA72B524BDA9BB8D5AD8BF366D954848740C4634EA97140C0712F45600C0C",
            "hash": "8FB39BD498BB1A7B2332C41A34416074C591C5736BCCE813E9AAE3D118D2298C"
        },
        "validated_ledger_index": 4
    },
    "ledger_accept_result": {
        "ledger_current_index": 6
    }
}
```
