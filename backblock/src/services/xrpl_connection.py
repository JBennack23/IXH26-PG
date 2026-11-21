import os

from xrpl.clients import WebsocketClient

# Reads the standalone node URL from environment variables
XRPL_NODE_URL = os.getenv("XRPL_NODE_URL", "ws://xrpl-node:6006")


def get_xrpl_client() -> WebsocketClient:
    """Returns an active WebSocket client context manager connected to the local XRPL node."""
    return WebsocketClient(XRPL_NODE_URL)
