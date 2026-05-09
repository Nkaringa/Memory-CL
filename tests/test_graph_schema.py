from __future__ import annotations

import pytest
from pydantic import ValidationError

from schemas import EdgeKind, GraphEdge, GraphNode, NodeKind, is_edge_allowed
from schemas.graph import EDGE_RULES


def test_every_rule_targets_at_least_one_node_kind() -> None:
    for src, kind, allowed in EDGE_RULES:
        assert isinstance(src, NodeKind)
        assert isinstance(kind, EdgeKind)
        assert len(allowed) >= 1


@pytest.mark.parametrize(
    ("src", "kind", "dst", "ok"),
    [
        (NodeKind.MODULE, EdgeKind.IMPORTS, NodeKind.MODULE, True),
        (NodeKind.MODULE, EdgeKind.IMPORTS, NodeKind.EXTERNAL, True),
        (NodeKind.FUNCTION, EdgeKind.CALLS, NodeKind.METHOD, True),
        (NodeKind.CLASS, EdgeKind.INHERITS, NodeKind.CLASS, True),
        # disallowed:
        (NodeKind.FILE, EdgeKind.IMPORTS, NodeKind.MODULE, False),
        (NodeKind.MODULE, EdgeKind.CALLS, NodeKind.FUNCTION, False),
        (NodeKind.FUNCTION, EdgeKind.INHERITS, NodeKind.CLASS, False),
    ],
)
def test_edge_rule_table(
    src: NodeKind, kind: EdgeKind, dst: NodeKind, ok: bool
) -> None:
    assert is_edge_allowed(src, kind, dst) is ok


def test_self_edges_rejected() -> None:
    with pytest.raises(ValidationError):
        GraphEdge(
            src_id="x",
            kind=EdgeKind.CALLS,
            dst_id="x",
            repo_id="r",
            commit_sha="c",
        )


def test_node_and_edge_are_frozen() -> None:
    n = GraphNode(
        node_id="n1",
        kind=NodeKind.FUNCTION,
        repo_id="r",
        qualified_name="pkg.mod.fn",
        name="fn",
        file_path="pkg/mod.py",
        line_start=1,
        line_end=5,
        commit_sha="c",
        source_sha="s",
    )
    with pytest.raises(ValidationError):
        n.name = "other"  # type: ignore[misc]


def test_external_node_allows_missing_provenance() -> None:
    n = GraphNode(
        node_id="external:numpy",
        kind=NodeKind.EXTERNAL,
        repo_id="r",
        qualified_name="numpy",
        name="numpy",
    )
    assert n.file_path is None
    assert n.commit_sha is None
