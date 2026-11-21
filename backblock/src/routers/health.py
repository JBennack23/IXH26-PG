from fastapi import APIRouter
from services import get_xrpl_client

router = APIRouter(prefix="/health", tags=["Blockchain and Ledger Operations"])


@router.get("/xrpl-status", summary="Verify the connection to the XRPL node")
def check_xrpl_health():
    try:
        get_xrpl_client()
        return {"status": "connected", "node_url": XRPL_NODE_URL}
    except Exception as e:
        return {"status": "disconnected", "error": str(e)}


@router.get("/status", summary="Verify this node status")
def check_health():
    return {"status": "online", "service": "backblock-gateway"}
