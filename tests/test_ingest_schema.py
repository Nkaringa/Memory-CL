from __future__ import annotations

import pytest
from pydantic import ValidationError

from schemas import (
    IngestionUnit,
    Language,
    UnitKind,
    content_sha,
    stable_unit_id,
)


def _make_unit(**overrides: object) -> IngestionUnit:
    base: dict[str, object] = {
        "unit_id": stable_unit_id("repo-1", "pkg/mod.py", "pkg.mod.fn"),
        "repo_id": "repo-1",
        "commit_sha": "abc123",
        "kind": UnitKind.FUNCTION,
        "name": "fn",
        "qualified_name": "pkg.mod.fn",
        "parent_qualified_name": "pkg.mod",
        "file_path": "pkg/mod.py",
        "language": Language.PYTHON,
        "line_start": 10,
        "line_end": 20,
        "content": "def fn():\n    return 1\n",
        "source_sha": content_sha("def fn():\n    return 1\n"),
    }
    base.update(overrides)
    return IngestionUnit(**base)


def test_stable_unit_id_is_deterministic_and_path_sensitive() -> None:
    a = stable_unit_id("repo-1", "pkg/mod.py", "pkg.mod.fn")
    b = stable_unit_id("repo-1", "pkg/mod.py", "pkg.mod.fn")
    c = stable_unit_id("repo-1", "pkg/other.py", "pkg.mod.fn")
    d = stable_unit_id("repo-2", "pkg/mod.py", "pkg.mod.fn")
    assert a == b
    assert a != c
    assert a != d
    assert len(a) == 64


def test_content_sha_changes_with_content() -> None:
    assert content_sha("a") != content_sha("b")
    assert content_sha("hello") == content_sha("hello")


def test_arrays_are_sorted_and_deduplicated() -> None:
    u = _make_unit(
        imports=["z.mod", "a.mod", "a.mod", "m.mod"],
        calls=["z.fn", "a.fn"],
        bases=["B", "A", "B"],
        references=["x", "y", "x"],
    )
    assert u.imports == ["a.mod", "m.mod", "z.mod"]
    assert u.calls == ["a.fn", "z.fn"]
    assert u.bases == ["A", "B"]
    assert u.references == ["x", "y"]


def test_line_end_must_be_after_line_start() -> None:
    with pytest.raises(ValidationError):
        _make_unit(line_start=20, line_end=10)


def test_unit_carries_versioned_metadata() -> None:
    u = _make_unit()
    assert u.schema_version == "1"
    assert u.created_at is not None
    assert u.updated_at is not None
    assert u.source == "memory-cl"
    # Checksum must be content-derived and stable.
    cs1 = u.compute_checksum()
    cs2 = u.compute_checksum()
    assert cs1 == cs2 and len(cs1) == 64


def test_extra_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        _make_unit(unexpected="value")  # type: ignore[arg-type]
