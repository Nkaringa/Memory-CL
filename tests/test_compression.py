from __future__ import annotations

import json

import pytest

from core.compression import (
    DenseEncoder,
    canonical_bytes,
    canonical_json,
    compact_payload,
)
from schemas import (
    CompressionMetrics,
    DenseApi,
    DenseGraphSlice,
    DenseModule,
    EmbeddingChunk,
    IngestionUnit,
    Language,
    UnitKind,
    content_sha,
    stable_unit_id,
)


# ---- canonical_json / canonical_bytes -------------------------------------
def test_canonical_json_sorts_keys_and_uses_compact_separators() -> None:
    s = canonical_json({"b": 2, "a": 1})
    assert s == '{"a":1,"b":2}'
    assert canonical_bytes({"b": 2, "a": 1}) == s.encode("utf-8")


def test_canonical_json_sorts_nested_keys() -> None:
    s = canonical_json({"x": {"z": 1, "y": 2}})
    assert s == '{"x":{"y":2,"z":1}}'


def test_canonical_json_normalizes_pydantic_models() -> None:
    m = DenseModule(id="pkg.m", cls=["B", "A"], fn=["f"], file=["pkg/m.py"])
    parsed = json.loads(canonical_json(m))
    # cls validator already sorted ["A","B"]; canonical emit re-checks structure.
    assert parsed["cls"] == ["A", "B"]
    assert parsed["t"] == "mod"


def test_canonical_json_is_byte_deterministic() -> None:
    a = {"z": [1, 2], "a": "x"}
    b = {"a": "x", "z": [1, 2]}
    assert canonical_json(a) == canonical_json(b)


# ---- compact_payload -------------------------------------------------------
def test_compact_drops_none_and_empty() -> None:
    out = compact_payload({"a": 1, "b": None, "c": [], "d": "", "e": {}, "f": "x"})
    assert out == {"a": 1, "f": "x"}


def test_compact_optionally_drops_zeros() -> None:
    src = {"a": 0, "b": 1, "c": False, "d": True}
    assert compact_payload(src) == src
    assert compact_payload(src, drop_zero=True) == {"b": 1, "d": True}


def test_compact_does_not_recurse() -> None:
    nested = {"top": {"empty": []}}
    assert compact_payload(nested) == {"top": {"empty": []}}


def test_compact_preserves_order_for_survivors() -> None:
    src = {"z": 1, "a": None, "m": "x", "b": ""}
    out = compact_payload(src)
    assert list(out.keys()) == ["z", "m"]


# ---- DenseModule / DenseApi / DenseGraphSlice serialization ---------------
def test_dense_module_drops_empty_arrays_in_json() -> None:
    m = DenseModule(id="pkg.m", file=["pkg/m.py"])
    payload = json.loads(m.to_dense_json(drop_empty=True))
    assert "cls" not in payload
    assert "fn" not in payload
    assert payload["file"] == ["pkg/m.py"]
    assert payload["t"] == "mod"


def test_dense_api_keys_within_max_length() -> None:
    for key in DenseApi.model_fields:
        assert len(key) <= 5, key


def test_dense_graph_slice_uses_alias_for_in() -> None:
    g = DenseGraphSlice(id="n1", k="Function", o=["n2"], i=["n3"], deg=2)
    payload = json.loads(g.to_dense_json())
    # `in` is a Python keyword; the model exposes alias "in" on output.
    assert "i" in payload or "in" in payload
    # Round-trip via canonical bytes is deterministic.
    assert canonical_bytes(g) == canonical_bytes(g.model_copy())


def test_dense_records_are_byte_deterministic_across_construction_orders() -> None:
    a = DenseModule(id="pkg.m", cls=["X", "A"], fn=["c", "a"], file=["pkg/m.py"])
    b = DenseModule(id="pkg.m", cls=["A", "X"], fn=["a", "c"], file=["pkg/m.py"])
    assert a.to_dense_json() == b.to_dense_json()


# ---- DenseEncoder ----------------------------------------------------------
def _u(kind: UnitKind, qname: str, *, calls=None, imports=None, bases=None) -> IngestionUnit:
    src = "def fn(): return 1\n"
    return IngestionUnit(
        unit_id=stable_unit_id("r", "pkg/m.py", qname),
        repo_id="r",
        commit_sha="c",
        kind=kind,
        name=qname.split(".")[-1],
        qualified_name=qname,
        parent_qualified_name=None,
        file_path="pkg/m.py",
        language=Language.PYTHON,
        line_start=1,
        line_end=1,
        content=src,
        source_sha=content_sha(src),
        imports=imports or [],
        calls=calls or [],
        bases=bases or [],
    )


def test_encoder_maps_unit_kind_to_t_tag() -> None:
    enc = DenseEncoder()
    cases = [
        (UnitKind.MODULE, "pkg.m", "mod"),
        (UnitKind.CLASS, "pkg.m.C", "cls"),
        (UnitKind.FUNCTION, "pkg.m.f", "fn"),
        (UnitKind.METHOD, "pkg.m.C.m", "mth"),
        (UnitKind.CONSTANT, "pkg.m.K", "const"),
    ]
    for kind, qn, tag in cases:
        ru = enc.encode_unit(_u(kind, qn))
        payload = json.loads(ru.record.to_dense_json())
        assert payload["t"] == tag
        assert payload["id"] == qn


def test_encoder_dep_field_depends_on_kind() -> None:
    enc = DenseEncoder()
    mod = enc.encode_unit(_u(UnitKind.MODULE, "pkg.m", imports=["os", "sys"]))
    fn = enc.encode_unit(_u(UnitKind.FUNCTION, "pkg.m.f", calls=["bar"]))
    cls = enc.encode_unit(_u(UnitKind.CLASS, "pkg.m.C", bases=["B"]))

    assert json.loads(mod.record.to_dense_json())["dep"] == ["os", "sys"]
    assert json.loads(fn.record.to_dense_json())["dep"] == ["bar"]
    assert json.loads(cls.record.to_dense_json())["dep"] == ["B"]


def test_encoder_reports_byte_savings() -> None:
    enc = DenseEncoder()
    long_src = "def fn():\n    " + ("# " + "x " * 50 + "\n    ") * 5 + "return 1\n"
    u = _u(UnitKind.FUNCTION, "pkg.m.fn")
    u_with_long_content = u.model_copy(update={
        "content": long_src,
        "source_sha": content_sha(long_src),
    })
    enc_unit = enc.encode_unit(u_with_long_content)
    assert enc_unit.bytes_input > enc_unit.bytes_output
    assert enc_unit.bytes_input == len(long_src.encode("utf-8"))


def test_encoder_is_deterministic_for_unsorted_input() -> None:
    enc = DenseEncoder()
    a = _u(UnitKind.FUNCTION, "pkg.m.aa")
    b = _u(UnitKind.FUNCTION, "pkg.m.bb")
    out1 = enc.encode_units([a, b])
    out2 = enc.encode_units([b, a])
    assert [e.unit_id for e in out1] == [e.unit_id for e in out2]
    assert [e.record.to_dense_json() for e in out1] == \
           [e.record.to_dense_json() for e in out2]


# ---- CompressionMetrics ----------------------------------------------------
def test_metrics_token_reduction_ratio() -> None:
    m = CompressionMetrics(bytes_input=1000, bytes_output=300)
    assert m.token_reduction_ratio() == pytest.approx(0.7)
    assert m.as_dict()["token_reduction_ratio"] == pytest.approx(0.7, abs=1e-6)


def test_metrics_ratio_zero_for_empty_input() -> None:
    m = CompressionMetrics()
    assert m.token_reduction_ratio() == 0.0


def test_metrics_ratio_clamped_to_zero_when_output_exceeds_input() -> None:
    m = CompressionMetrics(bytes_input=100, bytes_output=200)
    assert m.token_reduction_ratio() == 0.0


# ---- EmbeddingChunk validation --------------------------------------------
def test_embedding_chunk_rejects_inverted_range() -> None:
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        EmbeddingChunk(
            chunk_id="u1#0", unit_id="u1", repo_id="r",
            seq=0, content="x", char_start=10, char_end=5, token_estimate=1,
        )
