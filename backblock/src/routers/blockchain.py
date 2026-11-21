import json
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from services import get_xrpl_client
from xrpl.clients import WebsocketClient
from xrpl.core.binarycodec import encode
from xrpl.models.requests import GenericRequest, SubmitOnly, Tx
from xrpl.models.transactions import AccountSet, Memo
from xrpl.transaction import autofill_and_sign, submit_and_wait
from xrpl.wallet import Wallet

router = APIRouter(prefix="/blockchain", tags=["Blockchain and Ledger Operations"])


def _get_client() -> WebsocketClient:
    """
    Create and return a WebSocket client configured for the local XRPL standalone node.
    """
    return get_xrpl_client()


def _decode_hex_field(value: str | None) -> str | None:
    """
    Convert a hexadecimal string stored inside an XRPL Memo field back into a UTF-8 string.

    If the field is empty or not valid UTF-8 after hex decoding, return the original value
    as a safe fallback instead of crashing the response parser.
    """
    if not value:
        return None
    try:
        return bytes.fromhex(value).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return value


class AnchorMerkleRootRequest(BaseModel):
    """
    Input schema used by a producer to anchor a sensor batch's Merkle root on XRPL.
    """

    producer_seed: str = Field(
        ...,
        description="Secret seed of the producer's XRPL account signing the anchor transaction",
    )
    merkle_root_hex: str = Field(
        ...,
        min_length=64,
        max_length=64,
        description="64-character hex SHA-256 Merkle root of the sensor batch",
    )
    batch_id: str | None = Field(
        default=None,
        description="Optional batch identifier stored inside the Memo JSON payload",
    )
    advance_ledger: bool = Field(
        default=True,
        description="If true, call ledger_accept in standalone mode to close and validate the ledger",
    )


@router.post("/merkle-root", summary="Anchor a batch Merkle root on XRPL")
def anchor_merkle_root(req: AnchorMerkleRootRequest) -> dict[str, Any]:
    """
    Build, sign, and submit an XRPL Payment transaction carrying the batch Merkle root
    inside its Memos field, optionally closing the ledger in standalone mode.
    """
    # Step 1: Reconstruct the producer's XRPL wallet from its secret seed
    try:
        wallet = Wallet.from_seed(req.producer_seed)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid producer seed: {exc}",
        ) from exc

    # Step 2: Prepare the structured payload to embed inside the transaction's Memo field
    memo_payload = {
        "type": "sensor_batch_merkle_root",
        "merkle_root": req.merkle_root_hex.lower(),
        "batch_id": req.batch_id,
    }
    # XRPL Memo fields expect hexadecimal strings, so serialize JSON -> UTF-8 bytes -> hex
    memo_data_hex = (
        json.dumps(memo_payload, separators=(",", ":")).encode("utf-8").hex()
    )
    memo_type_hex = b"ixh26/merkle-root".hex()

    # Step 3: Construct an AccountSet transaction as a Memo carrier bound to the producer account
    tx = AccountSet(
        account=wallet.classic_address,
        memos=[
            Memo(
                memo_type=memo_type_hex,
                memo_data=memo_data_hex,
            )
        ],
    )

    try:
        # Step 4: Open connection to the local standalone xrpld node
        with _get_client() as client:
            if req.advance_ledger:
                # Populate sequence/fee/LastLedgerSequence from the node and sign locally with wallet
                signed_tx = autofill_and_sign(tx, client, wallet)

                # Convert transaction dict from to_xrpl() into serialized hex binary blob (str)
                tx_blob = encode(signed_tx.to_xrpl())
                # Use a typed Submit request model instead of a raw dict to satisfy Request typing
                submit_response = client.request(SubmitOnly(tx_blob=tx_blob))

                # In --standalone mode there is no peer consensus, so ledger_accept closes the ledger
                ledger_response = client.request(GenericRequest(method="ledger_accept"))

                return {
                    "status": "submitted_and_accepted",
                    "tx_hash": signed_tx.get_hash(),
                    "producer_address": wallet.classic_address,
                    "memo_payload": memo_payload,
                    "submit_result": submit_response.result,
                    "ledger_accept_result": ledger_response.result,
                }

            # Fallback path when ledger advancement is handled separately by the caller
            response = submit_and_wait(tx, client, wallet)
            return {
                "status": "submitted",
                "tx_hash": response.result.get("tx_json", {}).get("hash")
                or response.result.get("hash"),
                "producer_address": wallet.classic_address,
                "memo_payload": memo_payload,
                "result": response.result,
            }
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"XRPL node error: {exc}",
        ) from exc


@router.post(
    "/ledger/accept", summary="Close current ledger and advance (standalone mode)"
)
def accept_ledger() -> dict[str, Any]:
    """
    Force the standalone xrpld instance to close the current working ledger and advance
    to the next ledger index so submitted transactions become closed/validated locally.
    """
    try:
        with _get_client() as client:
            # Send admin/testing command ledger_accept via GenericRequest
            response = client.request(GenericRequest(method="ledger_accept"))
            if not response.is_successful():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=response.result,
                )
            return {
                "status": "ledger_accepted",
                "result": response.result,
            }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to advance ledger: {exc}",
        ) from exc


@router.get("/tx/{tx_hash}", summary="Retrieve transaction and decoded memos by hash")
def get_transaction_by_hash(tx_hash: str) -> dict[str, Any]:
    """
    Look up a transaction by its hash on the XRPL node and decode any Memos.

    During image verification, the verifier uses tx_hash from the image metadata to:
    1) recover 'producer_account' (the sender Account that anchored the Merkle root), and
    2) read the anchored 'merkle_root' from 'decoded_memos'.
    """
    try:
        with _get_client() as client:
            # Query the transaction using the typed Tx request model
            response = client.request(Tx(transaction=tx_hash))
            if not response.is_successful():
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=response.result,
                )

            result = response.result
            # Depending on node/xrpl-py response shape, transaction fields may sit under tx_json or tx
            tx_data = result.get("tx_json") or result.get("tx") or {}
            raw_memos = tx_data.get("Memos", [])

            decoded_memos = []
            for item in raw_memos:
                memo_obj = item.get("Memo", {})
                decoded_data_str = _decode_hex_field(memo_obj.get("MemoData"))

                # Attempt to parse MemoData as JSON because anchor_merkle_root writes JSON there
                parsed_json = None
                if decoded_data_str:
                    try:
                        parsed_json = json.loads(decoded_data_str)
                    except json.JSONDecodeError:
                        parsed_json = None

                decoded_memos.append(
                    {
                        "memo_type": _decode_hex_field(memo_obj.get("MemoType")),
                        "memo_data_raw": decoded_data_str,
                        "memo_data_json": parsed_json,
                    }
                )

            return {
                "tx_hash": tx_hash,
                "producer_account": tx_data.get("Account"),
                "ledger_index": result.get("ledger_index")
                or tx_data.get("ledger_index"),
                "validated": result.get("validated", False),
                "decoded_memos": decoded_memos,
                "raw_result": result,
            }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Error querying transaction {tx_hash}: {exc}",
        ) from exc
