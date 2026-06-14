"""Real round-trip tests for the SQLite ingestion repo (lite mode).

No mocks — these run against an actual temp SQLite file, which is exactly
how lite mode behaves. That makes them genuine integration tests with zero
external infrastructure.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from schemas import IngestionUnit, Language, UnitKind, content_sha, stable_unit_id
from storage.lite.engine import make_sqlite_engine
from storage.lite.ingestion_repo import SqliteIngestionRepository

pytestmark = pytest.mark.asyncio


def _u(
    qname: str,
    content: str,
    *,
    repo_id: str = "r",
    file_path: str = "pkg/m.py",
    language: Language = Language.PYTHON,
    imports: list[str] | None = None,
    calls: list[str] | None = None,
) -> IngestionUnit:
    return IngestionUnit(
        unit_id=stable_unit_id(repo_id, file_path, qname),
        repo_id=repo_id,
        commit_sha="c",
        kind=UnitKind.FUNCTION,
        name=qname.rsplit(".", 1)[-1],
        qualified_name=qname,
        parent_qualified_name="pkg.m",
        file_path=file_path,
        language=language,
        line_start=1,
        line_end=max(1, content.count("\n") + 1),
        content=content,
        source_sha=content_sha(content),
        imports=imports or [],
        calls=calls or [],
    )


async def _repo(tmp_path: Path) -> SqliteIngestionRepository:
    engine = make_sqlite_engine(tmp_path / "t.db")
    repo = SqliteIngestionRepository(engine)
    await repo.ensure_schema()
    return repo


async def test_insert_then_read_roundtrips_all_fields(tmp_path: Path) -> None:
    repo = await _repo(tmp_path)
    u = _u("pkg.m.f", "def f(): return 1\n", imports=["os", "sys"], calls=["g", "h"])
    assert await repo.upsert_unit(u) is True
    got = await repo.get_unit(u.unit_id)
    assert got is not None
    assert got.qualified_name == "pkg.m.f"
    assert got.language is Language.PYTHON
    assert got.kind is UnitKind.FUNCTION
    # JSON-array columns round-trip exactly.
    assert got.imports == ["os", "sys"]
    assert got.calls == ["g", "h"]
    assert got.references == [] and got.bases == []
    assert got.source_sha == u.source_sha
    # timestamps survive the TEXT round-trip.
    assert got.created_at == u.created_at
    assert got.updated_at == u.updated_at


async def test_change_detection(tmp_path: Path) -> None:
    repo = await _repo(tmp_path)
    u = _u("pkg.m.f", "def f(): return 1\n")
    assert await repo.upsert_unit(u) is True   # inserted
    assert await repo.upsert_unit(u) is False  # identical source_sha -> skipped
    # Same unit_id, new content -> new source_sha -> changed.
    u2 = _u("pkg.m.f", "def f(): return 2\n")
    assert u2.unit_id == u.unit_id and u2.source_sha != u.source_sha
    assert await repo.upsert_unit(u2) is True
    got = await repo.get_unit(u.unit_id)
    assert got is not None and got.content == "def f(): return 2\n"


async def test_upsert_units_counts_changed(tmp_path: Path) -> None:
    repo = await _repo(tmp_path)
    units = [_u(f"pkg.m.f{i}", f"def f{i}(): pass\n") for i in range(3)]
    assert await repo.upsert_units(units) == 3
    assert await repo.upsert_units(units) == 0  # nothing changed


async def test_list_for_file_and_repo(tmp_path: Path) -> None:
    repo = await _repo(tmp_path)
    await repo.upsert_units([
        _u("pkg.m.a", "a\n", file_path="pkg/m.py"),
        _u("pkg.m.b", "b\n", file_path="pkg/m.py"),
        _u("pkg.n.c", "c\n", file_path="pkg/n.py"),
    ])
    file_units = await repo.list_units_for_file("r", "pkg/m.py")
    assert {u.qualified_name for u in file_units} == {"pkg.m.a", "pkg.m.b"}
    repo_units = await repo.list_units_for_repo("r")
    assert len(repo_units) == 3


async def test_list_repos_aggregates(tmp_path: Path) -> None:
    repo = await _repo(tmp_path)
    await repo.upsert_units([
        _u("a", "x\n", repo_id="r1", file_path="a.py", language=Language.PYTHON),
        _u("b", "y\n", repo_id="r1", file_path="b.js", language=Language.JAVASCRIPT),
        _u("c", "z\n", repo_id="r2", file_path="c.py", language=Language.PYTHON),
    ])
    repos = {s.repo_id: s for s in await repo.list_repos()}
    assert repos["r1"].units == 2 and repos["r1"].files == 2
    assert set(repos["r1"].languages) == {"python", "javascript"}
    assert repos["r2"].units == 1


async def test_search_qnames(tmp_path: Path) -> None:
    repo = await _repo(tmp_path)
    await repo.upsert_units([
        _u("pkg.auth.login", "x\n"),
        _u("pkg.auth.logout", "y\n"),
        _u("pkg.db.query", "z\n"),
    ])
    matches = await repo.search_qnames("r", "auth", limit=10)
    names = {m.qualified_name for m in matches}
    assert names == {"pkg.auth.login", "pkg.auth.logout"}
    # Underscore is escaped (literal), not a wildcard.
    assert await repo.search_qnames("r", "pkg_", limit=10) == []


async def test_delete_units_for_file(tmp_path: Path) -> None:
    repo = await _repo(tmp_path)
    await repo.upsert_units([
        _u("pkg.m.a", "a\n", file_path="pkg/m.py"),
        _u("pkg.m.b", "b\n", file_path="pkg/m.py"),
    ])
    deleted = await repo.delete_units_for_file("r", "pkg/m.py")
    assert deleted == 2
    assert await repo.list_units_for_file("r", "pkg/m.py") == []
