from __future__ import annotations

import textwrap

import pytest

from core.ingestion.graph_builder import _UNIT_TO_NODE, GraphBuilder
from core.ingestion.pipeline import _default_parsers
from core.parsing import DocParser
from schemas import EdgeKind, Language, NodeKind, UnitKind

REPO = "test-repo"
COMMIT = "deadbeef"


def _parse(
    source: str,
    file_path: str = "docs/setup.md",
    language: Language = Language.MARKDOWN,
) -> list:
    return DocParser(language).parse_file(
        source=textwrap.dedent(source).lstrip("\n"),
        repo_id=REPO,
        file_path=file_path,
        commit_sha=COMMIT,
    )


# ---------------------------------------------------------------------------
# Module unit
# ---------------------------------------------------------------------------
def test_module_unit_first_with_full_source() -> None:
    units = _parse("# Title\n\nbody\n")
    assert units[0].kind == UnitKind.MODULE
    assert units[0].qualified_name == "docs.setup"
    assert units[0].name == "setup"
    assert units[0].language == Language.MARKDOWN
    assert units[0].content == "# Title\n\nbody\n"
    assert units[0].line_start == 1


def test_preamble_first_paragraph_is_module_docstring() -> None:
    units = _parse("""
        Memory-CL is a project memory system.
        It has docs.

        More preamble that is NOT the first paragraph.

        # First Heading

        body
    """)
    assert units[0].docstring == (
        "Memory-CL is a project memory system.\nIt has docs."
    )


def test_headingless_markdown_yields_module_only() -> None:
    units = _parse("just prose\n\nno headings anywhere\n")
    assert len(units) == 1
    assert units[0].kind == UnitKind.MODULE
    assert units[0].docstring == "just prose"


# ---------------------------------------------------------------------------
# Heading split + nesting qnames
# ---------------------------------------------------------------------------
def test_sections_split_with_nesting_qnames() -> None:
    units = _parse("""
        # Setup

        Top-level intro.

        ## Database Config

        Postgres settings.

        ### Pooling

        pool body

        ## Cache

        redis body

        # Usage

        run it
    """)
    by_qname = {u.qualified_name: u for u in units}
    assert set(by_qname) == {
        "docs.setup",
        "docs.setup.setup",
        "docs.setup.setup.database-config",
        "docs.setup.setup.database-config.pooling",
        "docs.setup.setup.cache",
        "docs.setup.usage",
    }
    sec = by_qname["docs.setup.setup.database-config"]
    assert sec.kind == UnitKind.SECTION
    assert sec.name == "Database Config"
    # Flat parenting: hierarchy lives in the qname chain, parent is the module.
    assert sec.parent_qualified_name == "docs.setup"
    assert sec.docstring == "Postgres settings."
    assert sec.signature is None


def test_section_content_is_own_body_verbatim_with_exact_lines() -> None:
    source = "# A\n\nalpha body\n\n## B\n\nbeta body\n"
    units = DocParser(Language.MARKDOWN).parse_file(
        source=source, repo_id=REPO, file_path="README.md", commit_sha=COMMIT
    )
    by_qname = {u.qualified_name: u for u in units}
    a = by_qname["README.a"]
    assert (a.line_start, a.line_end) == (1, 4)
    assert a.content == "# A\n\nalpha body\n\n"
    b = by_qname["README.a.b"]
    assert (b.line_start, b.line_end) == (5, 7)
    assert b.content == "## B\n\nbeta body\n"
    # Child text is NOT duplicated into the parent section.
    assert "beta body" not in a.content


def test_sibling_heading_pops_nesting_stack() -> None:
    units = _parse("""
        ## Deep

        body

        # Top

        after a deeper heading, levels reset
    """)
    qnames = {u.qualified_name for u in units}
    assert "docs.setup.deep" in qnames
    assert "docs.setup.top" in qnames  # NOT nested under deep


def test_slug_dedupe_uses_pipeline_ordinal_convention() -> None:
    units = _parse("""
        ## Setup

        first

        ## Setup

        second

        ## Setup

        third
    """)
    qnames = [u.qualified_name for u in units if u.kind == UnitKind.SECTION]
    assert qnames == [
        "docs.setup.setup",
        "docs.setup.setup#2",
        "docs.setup.setup#3",
    ]
    # unit_ids must differ (qname is part of the id).
    ids = [u.unit_id for u in units]
    assert len(set(ids)) == len(ids)


def test_nested_children_chain_through_deduped_parent_segment() -> None:
    units = _parse("""
        ## Setup

        ### Child

        ## Setup

        ### Child
    """)
    qnames = {u.qualified_name for u in units if u.kind == UnitKind.SECTION}
    assert qnames == {
        "docs.setup.setup",
        "docs.setup.setup.child",
        "docs.setup.setup#2",
        "docs.setup.setup#2.child",
    }


# ---------------------------------------------------------------------------
# Code-fence immunity
# ---------------------------------------------------------------------------
def test_headings_inside_fences_do_not_split() -> None:
    units = _parse("""
        # Real

        ```bash
        # not a heading
        ## also not a heading
        ```

        still in Real
    """)
    sections = [u for u in units if u.kind == UnitKind.SECTION]
    assert len(sections) == 1
    assert sections[0].qualified_name == "docs.setup.real"
    assert "still in Real" in sections[0].content


def test_tilde_fence_and_longer_close_run() -> None:
    units = _parse("""
        # Real

        ~~~
        # fenced
        ~~~~

        # Second
    """)
    qnames = {u.qualified_name for u in units if u.kind == UnitKind.SECTION}
    assert qnames == {"docs.setup.real", "docs.setup.second"}


def test_links_inside_fences_ignored() -> None:
    units = _parse("""
        # A

        ```
        [fake](other.md)
        ```
    """)
    assert units[0].imports == []


# ---------------------------------------------------------------------------
# Links -> imports
# ---------------------------------------------------------------------------
def test_relative_link_resolves_against_linking_file() -> None:
    units = _parse("see [guide](./guide.md) and [up](../README.md)\n\n# H\n")
    assert units[0].imports == ["README", "docs.guide"]


def test_link_anchor_stripped_and_pure_anchor_ignored() -> None:
    units = _parse("[a](guide.md#install) [b](#local-anchor)\n\n# H\n")
    assert units[0].imports == ["docs.guide"]


def test_http_and_mailto_links_ignored() -> None:
    units = _parse(
        "[x](https://example.com/a.md) [y](http://x.io) [z](mailto:a@b.c)\n\n# H\n"
    )
    assert units[0].imports == []


def test_link_escaping_repo_root_ignored() -> None:
    units = _parse("[bad](../../outside.md)\n\n# H\n")
    assert units[0].imports == []


def test_link_to_non_source_asset_ignored() -> None:
    units = _parse("![img](diagram.png) [tar](release.tgz)\n\n# H\n")
    assert units[0].imports == []


def test_link_to_code_file_resolves_to_module_qname() -> None:
    units = _parse("[impl](../core/pipeline.py)\n\n# H\n")
    assert units[0].imports == ["core.pipeline"]


def test_self_link_dropped() -> None:
    units = _parse("[me](./setup.md)\n\n# H\n")
    assert units[0].imports == []


def test_section_carries_links_from_its_own_body() -> None:
    units = _parse("""
        [preamble-link](a.md)

        # One

        [one-link](b.md)

        # Two

        [two-link](c.md)
    """)
    by_qname = {u.qualified_name: u for u in units}
    # Module aggregates every link in the file.
    assert by_qname["docs.setup"].imports == ["docs.a", "docs.b", "docs.c"]
    # Each section carries only its own body's links.
    assert by_qname["docs.setup.one"].imports == ["docs.b"]
    assert by_qname["docs.setup.two"].imports == ["docs.c"]


# ---------------------------------------------------------------------------
# Plain text
# ---------------------------------------------------------------------------
def test_txt_whole_file_module_only() -> None:
    units = _parse(
        "# looks like a heading but txt has no sections\n\nbody [l](x.md)\n",
        file_path="notes/todo.txt",
        language=Language.TEXT,
    )
    assert len(units) == 1
    mod = units[0]
    assert mod.kind == UnitKind.MODULE
    assert mod.qualified_name == "notes.todo"
    assert mod.language == Language.TEXT
    assert mod.imports == []  # no link extraction for plain text
    assert mod.content.startswith("# looks like a heading")


def test_doc_parser_rejects_code_languages() -> None:
    with pytest.raises(ValueError):
        DocParser(Language.PYTHON)


# ---------------------------------------------------------------------------
# Determinism + error tolerance
# ---------------------------------------------------------------------------
def test_parse_is_deterministic() -> None:
    source = """
        pre

        # A

        [l](b.md)

        ## B

        body
    """
    first = _parse(source)
    second = _parse(source)
    volatile = {"created_at", "updated_at"}
    assert [u.model_dump(exclude=volatile) for u in first] == [
        u.model_dump(exclude=volatile) for u in second
    ]
    assert [u.unit_id for u in first] == [u.unit_id for u in second]


def test_empty_file_yields_module_unit() -> None:
    units = _parse("")
    assert len(units) == 1
    assert units[0].kind == UnitKind.MODULE
    assert units[0].content == ""
    assert (units[0].line_start, units[0].line_end) == (1, 1)
    assert units[0].docstring is None


def test_weird_unicode_tolerated() -> None:
    units = _parse("﻿é世界 \U0001f600\n\n# Título 世界\n\nbödy\n")
    by_kind = {u.kind for u in units}
    assert UnitKind.SECTION in by_kind
    sec = next(u for u in units if u.kind == UnitKind.SECTION)
    assert sec.qualified_name == "docs.setup.t-tulo"  # non-ascii slugs to hyphens
    assert sec.docstring == "bödy"


def test_unclosed_fence_swallows_rest_of_file() -> None:
    # CommonMark: an unclosed fence runs to EOF — headings after it never split.
    units = _parse("# A\n\n```\n# swallowed\n")
    sections = [u for u in units if u.kind == UnitKind.SECTION]
    assert [s.qualified_name for s in sections] == ["docs.setup.a"]


# ---------------------------------------------------------------------------
# Graph + pipeline integration
# ---------------------------------------------------------------------------
def test_graph_builder_emits_section_nodes_and_link_edges() -> None:
    p = DocParser(Language.MARKDOWN)
    readme = p.parse_file(
        source="# A\n\nsee [g](guide.md)\n\n## B\n\nbody\n",
        repo_id=REPO,
        file_path="README.md",
        commit_sha=COMMIT,
    )
    guide = p.parse_file(
        source="# G\n\nguide body\n", repo_id=REPO, file_path="guide.md", commit_sha=COMMIT
    )
    resolver = {
        u.qualified_name: (u.unit_id, _UNIT_TO_NODE[u.kind]) for u in readme + guide
    }
    result = GraphBuilder().build(readme, qname_resolver=resolver)

    kinds = {n.kind for n in result.nodes}
    assert NodeKind.SECTION in kinds

    ids = {u.qualified_name: u.unit_id for u in readme + guide}
    edge_set = {(e.src_id, e.kind, e.dst_id) for e in result.edges}
    # Module-DEFINES->Section, File-CONTAINS->Section.
    assert (ids["README"], EdgeKind.DEFINES, ids["README.a"]) in edge_set
    assert (f"file:{REPO}:README.md", EdgeKind.CONTAINS, ids["README.a"]) in edge_set
    # The link edge: README module AND the linking section import guide's module.
    assert (ids["README"], EdgeKind.IMPORTS, ids["guide"]) in edge_set
    assert (ids["README.a"], EdgeKind.IMPORTS, ids["guide"]) in edge_set


def test_default_parsers_dispatch_markdown_and_text_to_doc_parser() -> None:
    parsers = _default_parsers()
    assert isinstance(parsers[Language.MARKDOWN], DocParser)
    assert isinstance(parsers[Language.TEXT], DocParser)
