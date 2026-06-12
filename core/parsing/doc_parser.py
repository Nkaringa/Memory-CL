"""Markdown / plain-text documentation parser (docs ingestion).

Pure Python — no tree-sitter grammar. Markdown structure that matters
for retrieval is line-shaped: ATX headings (`#`..`######`) split the
document into SECTION units, and `[text](relative/path.md)` links become
import edges. Everything else is verbatim content that flows into the
embedding pipeline exactly like code does.

Contract (mirrors PythonParser / TreeSitterParser):

- Module unit first (full source, line 1..N), children sorted by
  `(line_start, name)`.
- MARKDOWN: each ATX heading opens a `UnitKind.SECTION` unit whose
  qname is ``<module_qname>.<slugified heading chain>`` respecting
  nesting (e.g. ``docs.setup.database-config``). Repeated slugs at the
  same chain position get a ``#N`` ordinal suffix — the same collision
  convention the pipeline applies (`_resolve_qname_collisions`).
- Section content = heading + its OWN body, verbatim, up to the next
  heading of any level (child sections are separate units; their text
  is not duplicated into the parent — that would double-embed it).
- Preamble before the first heading belongs to the MODULE unit; the
  module docstring is the preamble's first paragraph. A section's
  docstring is the first paragraph of its body.
- Headings inside fenced code blocks (``` / ~~~) do NOT split sections.
- Links: relative targets resolve against the linking file (anchors
  stripped, ``index``/``__init__`` semantics via
  `module_qname_from_path`) and land in the module unit's `imports`;
  links inside a section's own body land in that section's `imports`
  too (Section-IMPORTS edge rule). http(s)/mailto/etc. and pure-anchor
  links are ignored, as are targets that escape the repo root or point
  at non-source assets (images etc.).
- TEXT (and heading-less markdown): MODULE unit only, full content,
  docstring = first paragraph. No link extraction for plain text.

Error tolerance: there is no such thing as a markdown syntax error —
parsing never raises on document content.
"""

from __future__ import annotations

import posixpath
import re
import time

from core.ingestion.logevent import emit_phase2_event
from core.observability import get_tracer
from core.parsing.languages._shared import _make_unit, _ParseInputs, _slice_source
from core.parsing.qnames import SOURCE_SUFFIXES, module_qname_from_path
from schemas import IngestionUnit, Language, UnitKind, content_sha

_tracer = get_tracer("core.parsing.doc_parser")

# ATX heading: 1-6 `#`, at least one space, title (trailing closing #s
# tolerated). Up to 3 leading spaces is still a heading per CommonMark.
_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$")

# Fence open/close: ``` or ~~~ (3+), up to 3 leading spaces.
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")

# Inline link: [text](target ...) — target is the first non-space run.
_LINK_RE = re.compile(r"\[[^\]]*\]\(\s*<?([^)\s>]+)>?[^)]*\)")

# URL scheme (http:, https:, mailto:, ftp:, ...).
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def _slugify(title: str) -> str:
    """Heading title -> qname segment: lowercase, non-alnum runs -> `-`."""
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug or "section"


def _first_paragraph(lines: list[str]) -> str | None:
    """First contiguous run of non-blank lines, joined verbatim."""
    para: list[str] = []
    for line in lines:
        if line.strip():
            para.append(line.rstrip())
        elif para:
            break
    return "\n".join(para) or None


def _resolve_link_target(target: str, file_path: str) -> str | None:
    """A markdown link target -> dotted module qname, or None to skip.

    Mirrors the JS import resolver: posixpath-normalize against the
    linking file, drop anything escaping the repo root. Anchors are
    stripped first; scheme-ful targets (http(s), mailto, ...) and
    targets without a known source/doc suffix (images, archives) carry
    no graph value and are skipped.
    """
    if _SCHEME_RE.match(target):
        return None
    target = target.split("#", 1)[0]
    if not target:
        return None  # pure in-page anchor
    if target.startswith("/"):
        # Repo-root-absolute (site-style) link.
        resolved = posixpath.normpath(target.lstrip("/"))
    else:
        resolved = posixpath.normpath(
            posixpath.join(posixpath.dirname(file_path), target)
        )
    if resolved.startswith("..") or resolved in (".", ""):
        return None
    if not resolved.endswith(SOURCE_SUFFIXES):
        return None
    return module_qname_from_path(resolved)


class DocParser:
    """Satisfies the `SourceParser` protocol for MARKDOWN and TEXT files."""

    def __init__(self, language: Language = Language.MARKDOWN) -> None:
        if language not in (Language.MARKDOWN, Language.TEXT):
            raise ValueError(f"DocParser does not handle {language}")
        self._language = language

    def parse_file(
        self,
        *,
        source: str,
        repo_id: str,
        file_path: str,
        commit_sha: str,
    ) -> list[IngestionUnit]:
        start = time.perf_counter()
        with _tracer.start_as_current_span("doc_parser.parse_file") as span:
            span.set_attribute("repo_id", repo_id)
            span.set_attribute("file_path", file_path)

            module_qname = module_qname_from_path(file_path)
            inputs = _ParseInputs(
                source=source,
                repo_id=repo_id,
                file_path=file_path,
                commit_sha=commit_sha,
                module_qname=module_qname,
                language=self._language,
            )
            lines = source.splitlines()

            if self._language is Language.MARKDOWN:
                headings = _scan_headings(lines)
                links_by_line = _scan_links(lines, file_path)
            else:
                headings = []
                links_by_line = {}

            children = _build_sections(
                lines, headings, inputs, module_qname, links_by_line
            )

            # Module docstring: first paragraph of the preamble (text
            # before the first heading), or of the whole file when there
            # are no headings.
            preamble_end = headings[0][0] - 1 if headings else len(lines)
            docstring = _first_paragraph(lines[:preamble_end])

            all_links = sorted(
                {q for qs in links_by_line.values() for q in qs} - {module_qname}
            )
            module_unit = _make_unit(
                inputs=inputs,
                kind=UnitKind.MODULE,
                name=module_qname.split(".")[-1] if module_qname else file_path,
                qualified_name=module_qname,
                parent_qualified_name=None,
                line_start=1,
                line_end=max(1, source.count("\n") + 1),
                content=source,
                docstring=docstring,
                signature=None,
                imports=all_links,
                calls=[],
                references=[],
                bases=[],
            )
            units = [module_unit, *children]

            emit_phase2_event(
                event="parse_ok",
                operation="doc_parser.parse_file",
                status="success",
                duration_ms=(time.perf_counter() - start) * 1000,
                file_path=file_path,
                content_hash=content_sha(source),
                level="debug",
                units_emitted=len(units),
            )
            span.set_attribute("units_emitted", len(units))
            return units


def _scan_headings(lines: list[str]) -> list[tuple[int, int, str]]:
    """ATX headings outside code fences -> [(1-based line, level, title)].

    Fence state machine: a ``` / ~~~ run opens a fence; the fence closes
    only on a run of the SAME character with at least the same length
    (CommonMark). Headings inside an open fence never split sections.
    """
    headings: list[tuple[int, int, str]] = []
    fence_char: str | None = None
    fence_len = 0
    for i, line in enumerate(lines, start=1):
        fence = _FENCE_RE.match(line)
        if fence is not None:
            run = fence.group(1)
            if fence_char is None:
                fence_char, fence_len = run[0], len(run)
                continue
            if run[0] == fence_char and len(run) >= fence_len:
                fence_char, fence_len = None, 0
                continue
        if fence_char is not None:
            continue
        m = _HEADING_RE.match(line)
        if m is not None:
            headings.append((i, len(m.group(1)), m.group(2)))
    return headings


def _scan_links(lines: list[str], file_path: str) -> dict[int, list[str]]:
    """Resolved link qnames per 1-based line number (fence-aware)."""
    out: dict[int, list[str]] = {}
    fence_char: str | None = None
    fence_len = 0
    for i, line in enumerate(lines, start=1):
        fence = _FENCE_RE.match(line)
        if fence is not None:
            run = fence.group(1)
            if fence_char is None:
                fence_char, fence_len = run[0], len(run)
                continue
            if run[0] == fence_char and len(run) >= fence_len:
                fence_char, fence_len = None, 0
                continue
        if fence_char is not None:
            continue
        qnames = [
            q
            for target in _LINK_RE.findall(line)
            if (q := _resolve_link_target(target, file_path)) is not None
        ]
        if qnames:
            out[i] = qnames
    return out


def _build_sections(
    lines: list[str],
    headings: list[tuple[int, int, str]],
    inputs: _ParseInputs,
    module_qname: str,
    links_by_line: dict[int, list[str]],
) -> list[IngestionUnit]:
    """One SECTION unit per heading; own body only (no child duplication)."""
    sections: list[IngestionUnit] = []
    # Stack of (level, final_segment) — final segments carry any `#N`
    # dedupe suffix so nested chains stay faithful to the parent's qname.
    stack: list[tuple[int, str]] = []
    used_qnames: set[str] = {module_qname}
    for idx, (line_no, level, title) in enumerate(headings):
        while stack and stack[-1][0] >= level:
            stack.pop()
        segment = _slugify(title)
        chain = [seg for _, seg in stack] + [segment]
        qname = ".".join([module_qname, *chain]) if module_qname else ".".join(chain)
        if qname in used_qnames:
            # Same convention as pipeline._resolve_qname_collisions: the
            # first occupant keeps the name; later ones get `#2`, `#3`, ...
            ordinal = 2
            while f"{qname}#{ordinal}" in used_qnames:
                ordinal += 1
            qname = f"{qname}#{ordinal}"
            segment = f"{segment}#{ordinal}"
        used_qnames.add(qname)
        stack.append((level, segment))

        # Own body: up to the next heading of ANY level (child sections
        # are their own units), else end of file.
        next_line = headings[idx + 1][0] if idx + 1 < len(headings) else len(lines) + 1
        line_end = max(line_no, next_line - 1)
        section_links = sorted(
            {
                q
                for ln in range(line_no, line_end + 1)
                for q in links_by_line.get(ln, ())
                if q != module_qname  # self-link carries no graph value
            }
        )
        sections.append(
            _make_unit(
                inputs=inputs,
                kind=UnitKind.SECTION,
                name=title,
                qualified_name=qname,
                # Flat parenting on the module: hierarchy lives in the
                # qname chain; the structural edge set stays exactly
                # Module-DEFINES->Section (EDGE_RULES).
                parent_qualified_name=module_qname or None,
                line_start=line_no,
                line_end=line_end,
                content=_slice_source(inputs.source, line_no, line_end),
                docstring=_first_paragraph(lines[line_no : line_end]),
                signature=None,
                imports=section_links,
                calls=[],
                references=[],
                bases=[],
            )
        )
    return sections
