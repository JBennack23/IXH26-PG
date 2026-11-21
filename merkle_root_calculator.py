import hashlib


def _sha256_hex(data: bytes) -> str:
    """Return a lowercase hexadecimal SHA-256 digest for the given bytes."""
    return hashlib.sha256(data).hexdigest()


def _hash_pair(left_hex: str, right_hex: str) -> str:
    """Compute the parent SHA-256 hex digest from two sibling hex digests."""
    return _sha256_hex(
        bytes.fromhex(left_hex.strip()) + bytes.fromhex(right_hex.strip())
    )


def compute_merkle_root_from_dict(sensor_leaves: dict[str, str]) -> str:
    """Compute the Merkle root from an ordered mapping of sensor IDs to leaf hex hashes."""
    if not sensor_leaves:
        raise ValueError("sensor_leaves must contain at least one sensor leaf.")

    # Preserve insertion order of the dictionary as the ordered leaf layer
    current_level: list[str] = [
        digest.strip().lower() for digest in sensor_leaves.values()
    ]

    while len(current_level) > 1:
        # Duplicate the last node if the current level has an odd number of nodes
        if len(current_level) % 2 == 1:
            current_level.append(current_level[-1])

        next_level: list[str] = []
        for idx in range(0, len(current_level), 2):
            parent_hash = _hash_pair(current_level[idx], current_level[idx + 1])
            next_level.append(parent_hash)

        current_level = next_level

    return current_level[0]


if __name__ == "__main__":
    # The sample sensor's public keys hashes
    batch_sensors: dict[str, str] = {
        "sensor_01": "70834315ee579e1a979d6ee489f4c7545a797a688d149fb98bc720f2f0b0481d",
        "sensor_02": "2222222222222222222222222222222222222222222222222222222222222222",
        "sensor_03": "3333333333333333333333333333333333333333333333333333333333333333",
        "sensor_04": "4444444444444444444444444444444444444444444444444444444444444444",
    }

    merkle_root = compute_merkle_root_from_dict(batch_sensors)
    print(f"Merkle Root: {merkle_root}")
