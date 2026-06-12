from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from pathspec import GitIgnoreSpec

from schemas import FileRef, Language

# Hard skips applied even without a .gitignore. These directories never
# contain user-relevant Python source for our purposes.
_DEFAULT_IGNORES: tuple[str, ...] = (
    ".git/",
    ".hg/",
    ".svn/",
    ".venv/",
    "venv/",
    "env/",
    "__pycache__/",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    "node_modules/",
    "dist/",
    "build/",
    "*.egg-info/",
    ".tox/",
    ".idea/",
    ".vscode/",
    # Tooling/agent directories — these hold assistant configs, prompts and
    # planning docs whose markdown must never ingest as project knowledge.
    ".claude/",
    ".codex/",
    ".gemini/",
    ".cursor/",
    ".github/",
    ".planning/",
)


@dataclass(frozen=True, slots=True)
class WalkResult:
    """Output of a single repo walk.

    `files` is sorted alphabetically by repo-relative POSIX path —
    determinism rule from PHASE_2_PLAN §6.
    """

    repo_id: str
    repo_root: Path
    files: tuple[FileRef, ...]


class FileWalker:
    """Deterministic, gitignore-aware repo walker.

    Walks Python, JS/TS, C#, Go, Java, and Rust sources plus
    Markdown/text documentation. Adding more languages later is a pure
    additive change to `LANGUAGE_EXTENSIONS`.
    """

    LANGUAGE_EXTENSIONS: tuple[tuple[str, Language], ...] = (
        (".py", Language.PYTHON),
        (".js", Language.JAVASCRIPT),
        (".mjs", Language.JAVASCRIPT),
        (".cjs", Language.JAVASCRIPT),
        (".jsx", Language.JAVASCRIPT),
        (".ts", Language.TYPESCRIPT),
        (".tsx", Language.TYPESCRIPT),
        (".mts", Language.TYPESCRIPT),
        (".cts", Language.TYPESCRIPT),
        # Batch 2 — no `.csx` (C# scripting) on purpose; `_test.go` files
        # ARE included (tests are code).
        (".cs", Language.CSHARP),
        (".go", Language.GO),
        (".java", Language.JAVA),
        (".rs", Language.RUST),
        # Documentation files → DocParser (no tree-sitter grammar).
        (".md", Language.MARKDOWN),
        (".mdx", Language.MARKDOWN),
        (".rst", Language.MARKDOWN),
        (".txt", Language.TEXT),
    )

    # TypeScript declaration files carry types only, no logic. Their
    # suffix per `path.suffix` is just ".ts"/".mts"/".cts", so they need
    # a name-based check, not a suffix-table entry.
    _DECLARATION_SUFFIXES: tuple[str, ...] = (".d.ts", ".d.mts", ".d.cts")

    def __init__(
        self,
        *,
        extra_ignores: Iterable[str] = (),
        respect_gitignore: bool = True,
    ) -> None:
        self._extra_ignores = tuple(extra_ignores)
        self._respect_gitignore = respect_gitignore

    def walk(self, repo_path: Path | str, *, repo_id: str) -> WalkResult:
        root = Path(repo_path).resolve()
        if not root.is_dir():
            raise NotADirectoryError(f"repo_path is not a directory: {root}")

        spec = self._build_spec(root)
        ext_to_lang = dict(self.LANGUAGE_EXTENSIONS)

        files: list[FileRef] = []
        # `Path.rglob` order is not deterministic across filesystems, so we
        # collect into a list and sort explicitly at the end.
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            if spec.match_file(rel):
                continue
            if path.name.endswith(self._DECLARATION_SUFFIXES):
                continue
            language = ext_to_lang.get(path.suffix)
            if language is None:
                continue
            files.append(FileRef(repo_id=repo_id, path=rel, language=language))

        files.sort(key=lambda f: f.path)
        return WalkResult(repo_id=repo_id, repo_root=root, files=tuple(files))

    def _build_spec(self, root: Path) -> GitIgnoreSpec:
        patterns: list[str] = list(_DEFAULT_IGNORES)
        patterns.extend(self._extra_ignores)
        if self._respect_gitignore:
            gitignore = root / ".gitignore"
            if gitignore.exists():
                patterns.extend(
                    line for line in gitignore.read_text(encoding="utf-8").splitlines()
                    if line.strip() and not line.lstrip().startswith("#")
                )
        return GitIgnoreSpec.from_lines(patterns)
