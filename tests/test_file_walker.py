from __future__ import annotations

from pathlib import Path

import pytest

from core.parsing import FileWalker, WalkResult
from schemas import Language


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A small synthetic repo with mixed languages and ignored dirs."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "service.py").write_text("def f(): pass\n")
    (tmp_path / "pkg" / "_helpers.py").write_text("def h(): pass\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_service.py").write_text("")
    # noise that must be ignored
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "junk.pyc").write_text("")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.py").write_text("# should be skipped")
    (tmp_path / "README.md").write_text("# readme")
    (tmp_path / "data.json").write_text("{}")
    # custom .gitignore
    (tmp_path / ".gitignore").write_text("secrets/\n*.tmp\n")
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "creds.py").write_text("API_KEY='x'")
    (tmp_path / "scratch.tmp").write_text("")
    return tmp_path


def test_walker_returns_only_python_files(repo: Path) -> None:
    result = FileWalker().walk(repo, repo_id="r1")
    assert isinstance(result, WalkResult)
    paths = [f.path for f in result.files]
    assert paths == [
        "pkg/__init__.py",
        "pkg/_helpers.py",
        "pkg/service.py",
        "tests/test_service.py",
    ]


def test_walker_respects_gitignore(repo: Path) -> None:
    paths = [f.path for f in FileWalker().walk(repo, repo_id="r1").files]
    assert "secrets/creds.py" not in paths


def test_walker_skips_default_ignores(repo: Path) -> None:
    paths = [f.path for f in FileWalker().walk(repo, repo_id="r1").files]
    assert all("__pycache__" not in p for p in paths)
    assert all("node_modules" not in p for p in paths)


def test_walker_is_deterministic(repo: Path) -> None:
    a = [f.path for f in FileWalker().walk(repo, repo_id="r1").files]
    b = [f.path for f in FileWalker().walk(repo, repo_id="r1").files]
    assert a == b
    assert a == sorted(a)


def test_walker_rejects_non_directory(tmp_path: Path) -> None:
    f = tmp_path / "not-a-dir.py"
    f.write_text("")
    with pytest.raises(NotADirectoryError):
        FileWalker().walk(f, repo_id="r")


def test_extra_ignores_compose_with_gitignore(repo: Path) -> None:
    walker = FileWalker(extra_ignores=("tests/",))
    paths = [f.path for f in walker.walk(repo, repo_id="r1").files]
    assert all(not p.startswith("tests/") for p in paths)


def test_walker_maps_js_ts_extensions(tmp_path) -> None:
    (tmp_path / "a.js").write_text("const x = 1;")
    (tmp_path / "b.mjs").write_text("export const y = 1;")
    (tmp_path / "c.cjs").write_text("module.exports = {};")
    (tmp_path / "d.jsx").write_text("export default () => null;")
    (tmp_path / "e.ts").write_text("const z: number = 1;")
    (tmp_path / "f.tsx").write_text("export default () => null;")
    (tmp_path / "g.mts").write_text("export {};")
    (tmp_path / "h.cts").write_text("export {};")
    (tmp_path / "i.cs").write_text("class A {}")
    (tmp_path / "j.go").write_text("package main")
    (tmp_path / "k.java").write_text("class A {}")
    (tmp_path / "l.rs").write_text("fn main() {}")
    (tmp_path / "skip.css").write_text("body {}")
    (tmp_path / "skip.csx").write_text("// C# script — not walked")

    result = FileWalker().walk(tmp_path, repo_id="r")
    langs = {f.path: f.language for f in result.files}

    assert langs == {
        "a.js": Language.JAVASCRIPT,
        "b.mjs": Language.JAVASCRIPT,
        "c.cjs": Language.JAVASCRIPT,
        "d.jsx": Language.JAVASCRIPT,
        "e.ts": Language.TYPESCRIPT,
        "f.tsx": Language.TYPESCRIPT,
        "g.mts": Language.TYPESCRIPT,
        "h.cts": Language.TYPESCRIPT,
        "i.cs": Language.CSHARP,
        "j.go": Language.GO,
        "k.java": Language.JAVA,
        "l.rs": Language.RUST,
    }


def test_walker_includes_go_test_files(tmp_path) -> None:
    # Plan decision: `_test.go` files ARE code — they must be walked.
    (tmp_path / "server.go").write_text("package main")
    (tmp_path / "server_test.go").write_text("package main")

    paths = [f.path for f in FileWalker().walk(tmp_path, repo_id="r").files]
    assert paths == ["server.go", "server_test.go"]


def test_walker_skips_declaration_files(tmp_path) -> None:
    (tmp_path / "real.ts").write_text("const x = 1;")
    (tmp_path / "types.d.ts").write_text("declare const x: number;")
    (tmp_path / "esm.d.mts").write_text("declare const y: number;")
    (tmp_path / "cjs.d.cts").write_text("declare const z: number;")

    result = FileWalker().walk(tmp_path, repo_id="r")
    assert [f.path for f in result.files] == ["real.ts"]
