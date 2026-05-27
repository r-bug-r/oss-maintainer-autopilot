from __future__ import annotations

import pytest

from autopilot.canonical import (
    canonical_json,
    constant_time_equals,
    hash_canonical,
    sha256_hex,
)


def test_canonical_json_sorts_keys_and_drops_whitespace() -> None:
    assert canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'
    assert canonical_json({"a": [3, {"z": 1, "y": 2}]}) == '{"a":[3,{"y":2,"z":1}]}'


def test_canonical_hash_is_key_order_independent() -> None:
    left = {"alpha": 1, "beta": {"x": True, "y": None}}
    right = {"beta": {"y": None, "x": True}, "alpha": 1}
    assert hash_canonical(left) == hash_canonical(right)
    assert len(hash_canonical(left)) == 64


