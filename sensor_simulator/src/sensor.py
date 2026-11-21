import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from PIL import Image
from PIL.PngImagePlugin import PngInfo


def load_or_create_keypair(
    keypair_path: Path,
) -> tuple[Ed25519PrivateKey, Ed25519PublicKey]:
    """
    Load an existing Ed25519 keypair from disk or generate a new one if missing.

    In our hackathon prototype, this simulates the immutable key material stored
    inside a physical camera sensor.
    """
    # If the keypair file already exists, deserialize the private key and derive the public key
    if keypair_path.exists():
        data = json.loads(keypair_path.read_text(encoding="utf-8"))
        private_bytes = bytes.fromhex(data["private_key_hex"])
        private_key = Ed25519PrivateKey.from_private_bytes(private_bytes)
        return private_key, private_key.public_key()

    # Generate a fresh Ed25519 private key (32 bytes raw) and corresponding public key (32 bytes raw)
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    # Serialize both keys to raw bytes and convert to hex strings for JSON storage
    private_hex = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    ).hex()
    public_hex = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()

    # Ensure parent directory exists and write keypair to disk
    keypair_path.parent.mkdir(parents=True, exist_ok=True)
    keypair_path.write_text(
        json.dumps(
            {
                "public_key_hex": public_hex,
                "private_key_hex": private_hex,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return private_key, public_key


def compute_decoded_pixels_sha256_hex(image_path: Path) -> str:
    """
    Compute the SHA-256 hash of a file in streaming chunks to avoid loading huge files into RAM.
    """
    digest = hashlib.sha256()
    with Image.open(image_path) as img:
        # Include mode (e.g., "RGB", "RGBA") and dimensions (width, height)
        # so the hash is unambiguous across image layouts
        header = f"{img.mode}:{img.size[0]}x{img.size[1]}:".encode()
        digest.update(header)

        # Hash raw decoded pixel bytes
        pixel_bytes = img.tobytes()
        digest.update(pixel_bytes)

    return digest.hexdigest()


def canonical_json_bytes(obj: dict[str, Any]) -> bytes:
    """
    Serialize a dictionary into canonical UTF-8 bytes.

    Sorting keys (sort_keys=True) and removing whitespace (separators=(",", ":"))
    guarantees that both the signer and the verifier produce the exact same byte sequence
    for the same logical payload.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign_image(
    input_image_path: Path,
    output_image_path: Path,
    keypair_path: Path,
    tx_hash: str,
    merkle_proof: list[dict[str, str]],
) -> dict[str, Any]:
    """
    Sign an image along with its verification metadata and embed the bundle inside a PNG text chunk.

    The signature covers the canonicalized payload (image hash, timestamp, XRPL tx_hash,
    sensor public key, and Merkle sibling proof) so tampering with any of those fields
    breaks signature verification.
    """
    # Load or create the simulated sensor's Ed25519 keypair
    private_key, public_key = load_or_create_keypair(keypair_path)
    public_key_hex = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()

    # Hash the raw image bytes and record current UTC epoch timestamp
    image_sha256 = compute_decoded_pixels_sha256_hex(input_image_path)
    timestamp = int(
        datetime.now(UTC).timestamp()
    )  # This timestamp will actually be obtained from the "Aggregator" server

    # Build the structured payload bound by the cryptographic signature
    payload_to_sign = {
        "image_sha256": image_sha256,
        "timestamp": timestamp,
        "tx_hash": tx_hash,
        "public_key_hex": public_key_hex,
        "merkle_proof": merkle_proof,
    }

    # Sign the canonical byte representation using the sensor's private key
    # In Ed25519, .sign() returns a 64-byte signature
    signature_bytes = private_key.sign(canonical_json_bytes(payload_to_sign))
    signature_hex = signature_bytes.hex()

    # Assemble the metadata bundle to be embedded inside the PNG
    signed_bundle = {
        "payload": payload_to_sign,
        "signature_hex": signature_hex,
        "signature_alg": "Ed25519",
        "hash_alg": "SHA-256",
    }

    # Open source image, attach JSON metadata into a PNG text chunk, and save as PNG
    output_image_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(input_image_path) as img:
        png_info = PngInfo()
        # Store the entire bundle under the "sensor_attestation" PNG metadata key
        png_info.add_text(
            "sensor_attestation",
            json.dumps(signed_bundle, separators=(",", ":")),
        )
        img.save(output_image_path, format="PNG", pnginfo=png_info)

    return signed_bundle


def verify_signature_only(signed_image_path: Path) -> dict[str, Any]:
    """
    Validate ONLY the Ed25519 cryptographic signature using the public key embedded
    inside the PNG metadata.

    Important scope note:
    - This function does NOT check the Merkle tree or query XRPL.
    - It also checks whether the current image file still matches the image_sha256
      recorded inside the signed payload.
    """
    # Open the PNG image and extract the embedded "sensor_attestation" metadata text
    with Image.open(signed_image_path) as img:
        raw_metadata = img.info.get("sensor_attestation")

    # If the metadata field is missing, we cannot validate the embedded signature bundle
    if not raw_metadata:
        return {
            "signature_valid": False,
            "reason": "Missing 'sensor_attestation' metadata in image",
        }

    # Parse the JSON bundle from the metadata string
    try:
        bundle = json.loads(raw_metadata)
        payload = bundle["payload"]
        signature_hex = bundle["signature_hex"]
        public_key_hex = payload["public_key_hex"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        return {
            "signature_valid": False,
            "reason": f"Malformed attestation metadata: {exc}",
        }

    # Reconstruct the public key from the hex string stored in the metadata
    try:
        public_key_bytes = bytes.fromhex(public_key_hex)
        public_key = Ed25519PublicKey.from_public_bytes(public_key_bytes)
        signature_bytes = bytes.fromhex(signature_hex)
    except ValueError as exc:
        return {
            "signature_valid": False,
            "reason": f"Invalid hex encoding in key or signature: {exc}",
        }

    # Verify the Ed25519 signature against the canonicalized payload bytes
    try:
        public_key.verify(signature_bytes, canonical_json_bytes(payload))
    except InvalidSignature:
        return {
            "signature_valid": False,
            "reason": "Cryptographic signature verification failed",
            "public_key_hex": public_key_hex,
        }

    # Consistency check between the actual image file bytes and payload.image_sha256
    actual_image_sha256 = compute_decoded_pixels_sha256_hex(signed_image_path)
    payload_image_sha256 = payload.get("image_sha256")
    image_matches_hash = actual_image_sha256 == payload_image_sha256

    return {
        "signature_valid": True,
        "image_matches_payload_hash": image_matches_hash,
        "public_key_hex": public_key_hex,
        "payload": payload,
    }


def compute_merkle_root_from_proof(
    public_key_hex: str,
    merkle_proof: list[dict[str, str]],
) -> str:
    """
    Reconstruct the Merkle root starting from the sensor's public key hash
    and iteratively combining each sibling hash along the Merkle proof path.

    Convention:
    - Leaf digest = SHA-256 of raw Ed25519 public key bytes (32 bytes).
    - Each proof step has {"position": "left" | "right", "hash": "<64-char hex>"}.
    - If position == "left", sibling is concatenated before current node: H(sibling || current).
    - If position == "right", current node is concatenated before sibling: H(current || sibling).
    """
    # Compute leaf hash from raw public key bytes
    try:
        pub_key_bytes = bytes.fromhex(public_key_hex.strip())
    except ValueError as exc:
        raise ValueError(f"Invalid public_key_hex format: {exc}") from exc

    current_digest: bytes = hashlib.sha256(pub_key_bytes).digest()

    # Walk up the Merkle tree using the sibling hashes in merkle_proof
    for step_idx, step in enumerate(merkle_proof):
        if not isinstance(step, dict):
            raise TypeError(
                f"Invalid Merkle proof step at index {step_idx}: expected dict"
            )

        position = step.get("position")
        sibling_hex = step.get("hash")

        if position not in ("left", "right"):
            raise ValueError(
                f"Invalid position '{position}' at proof step {step_idx}; expected 'left' or 'right'"
            )
        if not isinstance(sibling_hex, str):
            raise TypeError(
                f"Missing or non-string sibling hash at proof step {step_idx}"
            )

        try:
            sibling_bytes = bytes.fromhex(sibling_hex.strip())
        except ValueError as exc:
            raise ValueError(
                f"Invalid hex sibling hash at proof step {step_idx}: {exc}"
            ) from exc

        # Concatenate raw digests in exact left/right order before hashing
        if position == "left":
            combined = sibling_bytes + current_digest
        else:
            combined = current_digest + sibling_bytes

        current_digest = hashlib.sha256(combined).digest()

    # Return lowercase 64-character hex Merkle root
    return current_digest.hex()


def fetch_tx_from_backblock(
    tx_hash: str,
    backblock_base_url: str = "http://backblock:5000",
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    """
    Query the backblock service endpoint GET /blockchain/tx/{tx_hash}
    to retrieve transaction metadata, producer_account, and decoded Memos.
    """
    normalized_base = backblock_base_url.rstrip("/")
    url = f"{normalized_base}/blockchain/tx/{tx_hash}"
    http_request = Request(
        url=url, method="GET", headers={"Accept": "application/json"}
    )

    try:
        with urlopen(http_request, timeout=timeout_seconds) as response:
            body_bytes = response.read()
            return json.loads(body_bytes.decode("utf-8"))
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Backblock returned HTTP {exc.code} for tx {tx_hash}: {error_body}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(
            f"Unable to reach backblock at {normalized_base}: {exc.reason}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON response from backblock: {exc}") from exc


def _extract_anchored_merkle_root(tx_response: dict[str, Any]) -> str | None:
    """
    Extract the anchored Merkle root from decoded_memos returned by GET /blockchain/tx/{tx_hash}.
    """
    decoded_memos = tx_response.get("decoded_memos") or []
    for memo in decoded_memos:
        memo_json = memo.get("memo_data_json")
        if isinstance(memo_json, dict):
            merkle_root = memo_json.get("merkle_root")
            if isinstance(merkle_root, str) and merkle_root.strip():
                return merkle_root.strip().lower()
    return None


def verify_image_end_to_end(
    signed_image: Path,
    backblock_base_url: str = "http://backblock:5000",
) -> dict[str, Any]:
    """
    Perform full verification of a signed image:
    1. Verify Ed25519 signature over canonical payload and check decoded pixel hash integrity.
    2. Query backblock GET /blockchain/tx/{tx_hash} to fetch the XRPL transaction.
    3. Reconstruct the Merkle root from (public_key_hex, merkle_proof) and compare it
       against the Merkle root anchored in the XRPL transaction memos.
    4. Return producer_account so the caller/verifier can apply its trust policy on the producer.
    """
    # Verify signature and pixel hash integrity using existing helper
    sig_check = verify_signature_only(signed_image)
    if not sig_check.get("signature_valid"):
        return {
            "verified": False,
            "stage": "signature_verification",
            "details": sig_check,
        }

    if not sig_check.get("image_matches_payload_hash"):
        return {
            "verified": False,
            "stage": "pixel_integrity_check",
            "details": sig_check,
        }

    payload = sig_check["payload"]
    tx_hash = payload.get("tx_hash")
    public_key_hex = payload.get("public_key_hex")
    merkle_proof = payload.get("merkle_proof")

    if not isinstance(tx_hash, str) or not tx_hash.strip():
        return {
            "verified": False,
            "stage": "payload_validation",
            "reason": "Missing tx_hash in signed payload",
        }

    if not isinstance(merkle_proof, list):
        return {
            "verified": False,
            "stage": "payload_validation",
            "reason": "Missing or invalid merkle_proof list in signed payload",
        }

    # Query backblock for the XRPL transaction anchoring the batch Merkle root
    try:
        tx_data = fetch_tx_from_backblock(
            tx_hash=tx_hash.strip(),
            backblock_base_url=backblock_base_url,
        )
    except RuntimeError as exc:
        return {
            "verified": False,
            "stage": "blockchain_tx_lookup",
            "reason": str(exc),
            "tx_hash": tx_hash,
        }

    on_chain_merkle_root = _extract_anchored_merkle_root(tx_data)
    if not on_chain_merkle_root:
        return {
            "verified": False,
            "stage": "on_chain_merkle_root_extraction",
            "reason": "No merkle_root found in decoded transaction memos",
            "tx_hash": tx_hash,
            "producer_account": tx_data.get("producer_account"),
        }

    # Reconstruct Merkle root locally from sensor public key and sibling proof
    try:
        computed_merkle_root = compute_merkle_root_from_proof(
            public_key_hex=public_key_hex,
            merkle_proof=merkle_proof,
        )
    except ValueError as exc:
        return {
            "verified": False,
            "stage": "merkle_proof_computation",
            "reason": str(exc),
            "tx_hash": tx_hash,
            "on_chain_merkle_root": on_chain_merkle_root,
        }

    merkle_root_matches = computed_merkle_root == on_chain_merkle_root

    # Return full diagnostic result including producer_account for trust decision
    return {
        "verified": merkle_root_matches,
        "stage": "completed",
        "signature_valid": True,
        "image_matches_payload_hash": True,
        "merkle_root_matches": merkle_root_matches,
        "computed_merkle_root": computed_merkle_root,
        "on_chain_merkle_root": on_chain_merkle_root,
        "producer_account": tx_data.get("producer_account"),
        "tx_hash": tx_hash,
        "public_key_hex": public_key_hex,
        "timestamp": payload.get("timestamp"),
    }


def _parse_merkle_proof_arg(merkle_proof_json: str | None) -> list[dict[str, str]]:
    """
    Parse a JSON-encoded Merkle proof list from CLI arguments, or return a
    placeholder proof list for local testing when omitted.
    """
    # Default placeholder proof for local testing when no JSON proof is passed on CLI
    if not merkle_proof_json:
        return [
            {
                "position": "right",
                "hash": "2222222222222222222222222222222222222222222222222222222222222222",
            },
            {
                "position": "right",
                "hash": "3a8b88513c86f9bc4a25fa4570c4767ca79d29ef24d987e42cc64d2ba4be89a5",
            },
        ]

    parsed = json.loads(merkle_proof_json)
    if not isinstance(parsed, list):
        raise TypeError("--merkle-proof-json must be a JSON list of objects")
    return parsed


def build_cli_parser() -> argparse.ArgumentParser:
    """
    Build the CLI argument parser with 'sign' and 'verify' subcommands.
    """
    parser = argparse.ArgumentParser(
        description="Sensor simulator CLI for signing and end-to-end verification"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Subcommand: sign <image>
    sign_parser = subparsers.add_parser(
        "sign", help="Sign an image and embed attestation metadata in PNG"
    )
    sign_parser.add_argument("image", type=Path, help="Input image path")
    sign_parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output signed PNG path (default: signed_<input_filename>.png)",
    )
    sign_parser.add_argument(
        "--keypair",
        type=Path,
        default=Path("sensor_keys/sensor_01.json"),
        help="Path to sensor keypair JSON file",
    )
    sign_parser.add_argument(
        "--tx-hash",
        type=str,
        default="8FB39BD498BB1A7B2332C41A34416074C591C5736BCCE813E9AAE3D118D2298C",
        help="XRPL transaction hash anchoring the batch Merkle root",
    )
    sign_parser.add_argument(
        "--merkle-proof-json",
        type=str,
        default=None,
        help="JSON array string of Merkle sibling steps",
    )

    # Subcommand: verify <image>
    verify_parser = subparsers.add_parser(
        "verify", help="Verify image signature, pixel hash, Merkle proof, and XRPL tx"
    )
    verify_parser.add_argument("image", type=Path, help="Signed PNG image path")
    verify_parser.add_argument(
        "--backblock-url",
        type=str,
        default="http://backblock:5000",
        help="Backblock service base URL (default: http://backblock:5000)",
    )

    return parser


def cli_main(argv: list[str] | None = None) -> int:
    """
    Execute the selected CLI subcommand and return an OS exit code (0 = ok, 1 = fail).
    """
    parser = build_cli_parser()
    args = parser.parse_args(argv)

    if args.command == "sign":
        # Derive default output path if -o/--output was not provided
        output_path: Path = (
            args.output
            if args.output is not None
            else args.image.parent / f"signed_{args.image.stem}.png"
        )
        proof = _parse_merkle_proof_arg(args.merkle_proof_json)

        bundle = sign_image(
            input_image_path=args.image,
            output_image_path=output_path,
            keypair_path=args.keypair,
            tx_hash=args.tx_hash,
            merkle_proof=proof,
        )
        print(
            json.dumps(
                {
                    "status": "signed",
                    "output_image": str(output_path),
                    "public_key_hex": bundle["payload"]["public_key_hex"],
                    "tx_hash": bundle["payload"]["tx_hash"],
                },
                indent=2,
            )
        )
        return 0

    if args.command == "verify":
        result = verify_image_end_to_end(
            signed_image=args.image,
            backblock_base_url=args.backblock_url,
        )
        print(json.dumps(result, indent=2))
        return 0 if result.get("verified") else 1

    return 1


if __name__ == "__main__":
    sys.exit(cli_main())
