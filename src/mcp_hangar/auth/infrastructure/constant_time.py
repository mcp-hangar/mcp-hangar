"""Constant-time comparison utilities for authentication.

Prevents timing side-channel attacks by ensuring key lookups
take the same amount of time regardless of whether the key exists.
Uses hmac.compare_digest for all hash comparisons.
"""

import hmac
from typing import TypeVar

V = TypeVar("V")


def constant_time_key_lookup(target_hash: str, hash_dict: dict[str, V]) -> V | None:
    """Look up a key hash in constant time.

    Iterates ALL entries in the dictionary using hmac.compare_digest
    for each comparison. This ensures the lookup time does not vary
    based on whether the key exists or where it is positioned.

    Args:
        target_hash: The hash to look up.
        hash_dict: Dictionary mapping hashes to values.

    Returns:
        The value associated with the matching hash, or None if not found.
    """
    result: V | None = None
    target_bytes = target_hash.encode("utf-8")

    for stored_hash, value in hash_dict.items():
        if hmac.compare_digest(target_bytes, stored_hash.encode("utf-8")):
            result = value

    return result
