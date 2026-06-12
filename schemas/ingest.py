from __future__ import annotations

import hashlib
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from schemas.base import VersionedModel


class UnitKind(StrEnum):
    """AST-level kinds extracted by the parser.

    Higher-level kinds (svc, api) are NOT produced here — they are derived
    later by the semantic-extraction phase. Keeping this enum focused on
    structural parser output preserves Phase 2's deterministic contract.
    """

    MODULE = "mod"
    CLASS = "cls"
    FUNCTION = "fn"
    METHOD = "mth"
    CONSTANT = "const"
    SECTION = "sec"  # markdown heading section (docs ingestion)


class Language(StrEnum):
    PYTHON = "python"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    CSHARP = "csharp"
    GO = "go"
    JAVA = "java"
    RUST = "rust"
    # Documentation "languages" — parsed by DocParser, not tree-sitter.
    MARKDOWN = "markdown"
    TEXT = "text"
    # Reserved for later phases — adding values here is backward-compatible.


def stable_unit_id(repo_id: str, file_path: str, qualified_name: str) -> str:
    """Deterministic logical identity for an extracted unit.

    Stable across content edits (so re-ingestion is a true upsert), but
    NOT stable across moves or renames — those produce a new unit_id and
    the old row is invalidated by the file-level reconciliation step.
    """
    payload = f"{repo_id}\x00{file_path}\x00{qualified_name}".encode()
    return hashlib.sha256(payload).hexdigest()


def content_sha(content: str) -> str:
    """SHA256 of the unit's raw source slice. Drives re-embedding decisions."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class FileRef(BaseModel):
    """Lightweight file pointer used inside graph + retrieval payloads."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    repo_id: str
    path: str = Field(description="Repo-relative POSIX path")
    language: Language = Language.PYTHON


class IngestionUnit(VersionedModel):
    """Canonical AST extraction output.

    One IngestionUnit corresponds to one parsed structural unit (module,
    class, function, method, top-level constant). The unit is the *atom*
    of every downstream layer: Postgres rows, graph nodes, and vector
    payloads all key off `unit_id`.
    """

    # ----- Identity -----
    unit_id: str = Field(description="Stable logical id — see stable_unit_id()")
    repo_id: str = Field(description="Multi-tenant scoping key")
    commit_sha: str = Field(description="Provenance — commit producing this row")

    # ----- Kind / naming -----
    kind: UnitKind
    name: str = Field(description="Local symbol name (e.g. `compute_score`)")
    qualified_name: str = Field(description="Fully-qualified path inside the repo")
    parent_qualified_name: str | None = Field(
        default=None,
        description="Enclosing class/module qname; None for top-level modules",
    )

    # ----- Location -----
    file_path: str = Field(description="Repo-relative POSIX path")
    language: Language = Language.PYTHON
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)

    # ----- Content -----
    content: str = Field(description="Raw source slice (verbatim)")
    source_sha: str = Field(description="SHA256 of `content` — drives invalidation")
    docstring: str | None = None
    signature: str | None = Field(
        default=None,
        description="Function/method signature; None for modules/classes",
    )

    # ----- Static relations (graph extraction inputs) -----
    imports: list[str] = Field(
        default_factory=list,
        description="Imported module paths (sorted, deduplicated)",
    )
    calls: list[str] = Field(
        default_factory=list,
        description="Best-effort qualified callee names (sorted)",
    )
    references: list[str] = Field(
        default_factory=list,
        description="Symbols referenced (sorted, deduplicated)",
    )
    bases: list[str] = Field(
        default_factory=list,
        description="For classes: base-class qnames (sorted)",
    )

    # ----- Token budgeting -----
    token_count: int = Field(
        default=0, ge=0, description="Cached token count for retrieval planning"
    )

    @field_validator("imports", "calls", "references", "bases")
    @classmethod
    def _sorted_unique(cls, v: list[str]) -> list[str]:
        # Determinism: arrays must be sorted and deduplicated at write time.
        return sorted(set(v))

    @field_validator("line_end")
    @classmethod
    def _end_after_start(cls, v: int, info: object) -> int:
        data = getattr(info, "data", {}) or {}
        start = data.get("line_start")
        if start is not None and v < start:
            raise ValueError("line_end must be >= line_start")
        return v
