import os
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from services import get_xrpl_client
from xrpl.clients import WebsocketClient
from xrpl.core.binarycodec import encode
from xrpl.models.requests import AccountInfo, GenericRequest, SubmitOnly
from xrpl.models.transactions import Payment
from xrpl.transaction import autofill_and_sign
from xrpl.utils import xrp_to_drops
from xrpl.wallet import Wallet

router = APIRouter(prefix="/account", tags=["Account"])

# Well-known genesis seed used by xrpld when initializing a fresh standalone genesis ledger
GENESIS_SEED = os.getenv("XRPL_GENESIS_SEED", "snoPBrXtMeMyMHUVTgbuqAfg1SUTb")


def _get_client() -> WebsocketClient:
    """
    Create and return a WebSocket client connected to the local XRPL standalone node.
    """
    return get_xrpl_client()


class FundAccountRequest(BaseModel):
    """
    Input schema to fund a test or producer XRPL account from the standalone genesis account.
    """

    destination_address: str = Field(
        ..., description="Target XRPL classic address (r-address) to fund"
    )
    amount_xrp: float = Field(
        default=100.0,
        gt=0,
        description="Amount of XRP to transfer from the genesis account",
    )
    advance_ledger: bool = Field(
        default=True,
        description="If true, call ledger_accept in standalone mode after submitting the funding tx",
    )


@router.post("/generate", summary="Generate a new XRPL wallet locally")
def generate_wallet() -> dict[str, Any]:
    """
    Generate a new XRPL keypair/wallet locally in memory.

    Note: The generated address is not active on the ledger until it receives
    an initial XRP Payment meeting the account reserve requirement.
    """
    print("CALLED TO CREATE AN ACCOUNT")
    wallet = Wallet.create()
    print(f"WALLET CREATED: {wallet if wallet else 'FALSE'}")
    return {
        "classic_address": wallet.classic_address,
        "public_key": wallet.public_key,
        "private_key": wallet.private_key,
        "seed": wallet.seed,
    }


@router.get("/{address}", summary="Get account info and balance")
def get_account_info(address: str) -> dict[str, Any]:
    """
    Query current account metadata, sequence number, and XRP balance from the local XRPL node.
    """
    try:
        with _get_client() as client:
            # Request account state at the current working ledger
            response = client.request(
                AccountInfo(account=address, ledger_index="current", strict=False)
            )
            if not response.is_successful():
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=response.result,
                )
            return response.result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"XRPL node error: {exc}",
        ) from exc


@router.post("/fund", summary="Fund an account from the standalone genesis account")
def fund_from_genesis(req: FundAccountRequest) -> dict[str, Any]:
    """
    Send test XRP from the pre-funded standalone genesis account to activate and fund
    a producer or test wallet on the local ledger.
    """
    try:
        # Load the genesis wallet that holds initial test XRP in standalone mode
        genesis_wallet = Wallet.from_seed(GENESIS_SEED)

        # Build a Payment transaction converting XRP to drops (1 XRP = 1,000,000 drops)
        tx = Payment(
            account=genesis_wallet.classic_address,
            destination=req.destination_address,
            amount=xrp_to_drops(req.amount_xrp),
        )

        with _get_client() as client:
            # Fill missing fields (such as Sequence and Fee) and sign with genesis_wallet
            signed_tx = autofill_and_sign(tx, client, genesis_wallet)

            # Convert transaction dict from to_xrpl() into serialized hex binary blob (str)
            tx_blob = encode(signed_tx.to_xrpl())

            # Submit the serialized signed blob using the typed Submit request model
            submit_res = client.request(SubmitOnly(tx_blob=tx_blob))

            # Optionally advance the standalone ledger so the funded balance is immediately closed
            ledger_res = None
            if req.advance_ledger:
                ledger_res = client.request(GenericRequest(method="ledger_accept"))

            return {
                "status": "funded",
                "tx_hash": signed_tx.get_hash(),
                "submit_result": submit_res.result,
                "ledger_accept_result": ledger_res.result if ledger_res else None,
            }
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Funding failed: {exc}",
        ) from exc
