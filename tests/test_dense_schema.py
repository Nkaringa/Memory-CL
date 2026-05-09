from __future__ import annotations

import json

from schemas import DENSE_VERSION, DenseRecord


def test_all_keys_within_max_length() -> None:
    for key in DenseRecord.model_fields:
        assert len(key) <= DenseRecord.MAX_KEY_LEN


def test_dense_serialization_is_deterministic() -> None:
    a = DenseRecord(
        t="svc",
        id="auth",
        dep=["redis", "postgres"],
        api=["refresh", "login"],
    )
    b = DenseRecord(
        t="svc",
        id="auth",
        dep=["postgres", "redis", "redis"],  # dup + unsorted
        api=["login", "refresh"],
    )
    # Same logical content -> identical serialized bytes.
    assert a.to_dense_json() == b.to_dense_json()

    parsed = json.loads(a.to_dense_json())
    assert parsed == {
        "v": DENSE_VERSION,
        "t": "svc",
        "id": "auth",
        "dep": ["postgres", "redis"],
        "api": ["login", "refresh"],
    }


def test_dense_drops_empty_arrays_for_token_efficiency() -> None:
    r = DenseRecord(t="mod", id="x")
    payload = json.loads(r.to_dense_json(drop_empty=True))
    assert "dep" not in payload
    assert "risk" not in payload

    full = json.loads(r.to_dense_json(drop_empty=False))
    assert full["dep"] == []
    assert full["risk"] == []


def test_dense_keys_sorted_in_output() -> None:
    """The on-the-wire JSON must list top-level keys in sorted order so
    that two equal records produce byte-identical output.
    """
    r = DenseRecord(t="svc", id="auth", risk=["x"], dep=["y"])
    raw = r.to_dense_json()
    # Re-parse and re-serialize with sort_keys=True; result must match raw,
    # which proves the writer emitted sorted keys (not just a sorted dict).
    canonical = json.dumps(json.loads(raw), sort_keys=True, separators=(",", ":"))
    assert raw == canonical
