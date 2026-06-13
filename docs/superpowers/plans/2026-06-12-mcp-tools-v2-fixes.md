# MCP Tools v2 Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply four review fixes to `feat/mcp-tools-v2`: honest `find_symbol` truncation, capped `explore` edges, depth-clamp warning in deprecated aliases, and an updated SDK test fixture.

**Architecture:** All changes are targeted bug fixes inside three existing tool files and one test file. No new modules. Tests go alongside existing tests in `tests/test_mcp_tools.py` and `tests/test_phase9_sdk_cli.py`. Every behavior change is test-driven: write the failing test first, then fix the code.

**Tech Stack:** Python 3.11+, pytest-asyncio, SQLAlchemy async, existing `_state()`/`_run()` test helpers in `tests/test_mcp_tools.py`.

---

## File Map

| File | Change |
|---|---|
| `core/mcp/tools/discovery_tool.py` | Fix `_find_in_repo`: fetch `limit+1` rows, set `truncated=True` on extra row, return only `limit` rows |
| `core/mcp/tools/explore_tool.py` | In `_explore_impl`: filter edges to only those whose BOTH endpoints are in the kept neighbor set + seed; cap edges at 200; add `truncated_edges` flag |
| `core/mcp/tools/graph_tool.py` | In `QueryGraphTool.execute` and `GetRelatedComponentsTool.execute`: add `"warning"` key when `min(depth, 5)` clamps |
| `tests/test_mcp_tools.py` | Add four new tests: `find_symbol` truncation, `explore` edge cap/filter, `explore` edges-both-endpoints, depth-clamp warning |
| `tests/test_phase9_sdk_cli.py` | Update fake `/mcp/tools/query_graph` response to real v2-alias payload shape with `candidates[].unit_id` compat keys |

---

## Task 1: Fix `find_symbol` truncation (MUST-FIX)

**Context:** `_find_in_repo` runs `LIMIT :limit` in SQL then the caller checks `len(matches) > request.limit`. Because the SQL already caps rows at `limit`, the post-fetch length can never exceed `limit`, so `truncated` is always `False`. Fix: pass `limit + 1` to SQL, detect the extra row, strip it, set `truncated = True`.

**Files:**
- Modify: `core/mcp/tools/discovery_tool.py:32-73` (`_find_in_repo`) and `discovery_tool.py:105-122` (`FindSymbolTool.execute`)
- Test: `tests/test_mcp_tools.py` (add after the existing `find_symbol` tests)

- [ ] **Step 1: Write the failing test**

Add this test directly after `test_find_symbol_empty_hints_alternatives` in `tests/test_mcp_tools.py`:

```python
@pytest.mark.asyncio
async def test_find_symbol_truncated_flag_fires_at_limit() -> None:
    """30 fake matches, limit=10 → truncated=True, exactly 10 returned."""
    units_30 = [
        _unit(f"pkg.m.fn{i:02d}", unit_id=f"u{i:02d}") for i in range(30)
    ]
    state = _state(routes={"symbol": _symbol_rows(*units_30)})
    resp = await _run(state, "find_symbol",
                      {"query": "fn", "repo_id": "acme", "limit": 10})
    assert resp.status.value == "success"
    assert resp.data["truncated"] is True
    assert len(resp.data["matches"]) == 10
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/karinganageshgoud/Desktop/Agent-Memory/Memory-CL
.venv/bin/pytest tests/test_mcp_tools.py::test_find_symbol_truncated_flag_fires_at_limit -v
```

Expected: FAIL — `assert False is True` (truncated is currently always False because SQL already caps).

- [ ] **Step 3: Fix `_find_in_repo` to fetch `limit + 1`**

In `core/mcp/tools/discovery_tool.py`, change the SQL query in `_find_in_repo` to use `limit + 1`:

```python
async def _find_in_repo(
    state: Any, repo_id: str, query: str, limit: int
) -> list[dict[str, Any]]:
    """Substring qname search, enriched with kind + file:line + unit_id.

    Fetches `limit + 1` rows so the caller can detect truncation: if
    the extra row arrives, the result was capped and `truncated` can be
    set honestly. The extra row is never included in the returned slice.
    Read-only.
    """
    from sqlalchemy import text

    sql = text(
        "SELECT unit_id, qualified_name, kind, file_path, line_start, line_end"
        "  FROM ingestion_units"
        " WHERE repo_id = :repo_id AND qualified_name ILIKE :pattern"
        " ORDER BY length(qualified_name), qualified_name"
        " LIMIT :limit"
    )
    async with state.postgres.engine.connect() as conn:
        result = await conn.execute(
            sql,
            {
                "repo_id": repo_id,
                "pattern": f"%{escape_like(query)}%",
                "limit": limit + 1,        # fetch one extra to detect truncation
            },
        )
        rows = result.all()
    out: list[dict[str, Any]] = []
    for row in rows:
        m = row._mapping if hasattr(row, "_mapping") else row
        out.append(
            {
                "repo_id": repo_id,
                "qualified_name": m["qualified_name"],
                "kind": m["kind"],
                "file_path": m["file_path"],
                "lines": f"{m['line_start']}-{m['line_end']}",
                "unit_id": m["unit_id"],
            }
        )
    return out
```

- [ ] **Step 4: Fix `FindSymbolTool.execute` to use per-repo truncation**

The current code collects all rows first then checks total length. The check now works correctly because `_find_in_repo` returns up to `limit + 1` rows per repo. Update `execute` to detect truncation correctly and strip the extra row:

Replace the `matches: list` / loop / sort / truncation block (lines 105–122) with:

```python
        matches: list[dict[str, Any]] = []
        truncated = False
        for repo in targets:
            rows = await _find_in_repo(state, repo, request.query, request.limit)
            if len(rows) > request.limit:
                truncated = True
                rows = rows[: request.limit]
            matches.extend(rows)
        # Deterministic: shortest qname first (canonical units beat
        # deeply nested test paths), then qname, then repo.
        matches.sort(
            key=lambda m: (
                len(m["qualified_name"]),
                m["qualified_name"],
                m["repo_id"],
            )
        )
        # After merging across repos, apply the global limit.
        if len(matches) > request.limit:
            truncated = True
            matches = matches[: request.limit]
```

- [ ] **Step 5: Run test to verify it passes**

```bash
.venv/bin/pytest tests/test_mcp_tools.py::test_find_symbol_truncated_flag_fires_at_limit -v
```

Expected: PASS.

- [ ] **Step 6: Run the full test suite to confirm no regressions**

```bash
.venv/bin/pytest tests/test_mcp_tools.py tests/test_phase9_sdk_cli.py -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
cd /Users/karinganageshgoud/Desktop/Agent-Memory/Memory-CL
git add core/mcp/tools/discovery_tool.py tests/test_mcp_tools.py
git commit -m "fix(mcp): honest find_symbol truncation — fetch limit+1 per repo"
```

---

## Task 2: Cap `explore` edges (SHOULD-FIX)

**Context:** `_explore_impl` returns `edges` = every edge among `all_ids` (undirected neighbors + seed). When direction filtering cuts many neighbors, the `edges` array still contains edges to/from those cut nodes. Also there is no cap, so a well-connected seed can produce hundreds of edges. Fix: after the neighbor list is built (capped at 50, direction-filtered), re-filter edges to only those where both `src_id` and `dst_id` are in `kept_ids = {seed.unit_id} ∪ {n["node_id"] for n in neighbors}`. Cap at 200, set `truncated_edges = True` if more would have been present.

**Files:**
- Modify: `core/mcp/tools/explore_tool.py:197-215` (the `out` dict assembly)
- Test: `tests/test_mcp_tools.py` (add after existing `explore` tests)

- [ ] **Step 1: Write two failing tests**

Add these tests in `tests/test_mcp_tools.py` after the existing `explore` tests:

```python
@pytest.mark.asyncio
async def test_explore_edges_exclude_filtered_out_nodes() -> None:
    """Edges to direction-filtered-out neighbors must not appear in `edges`."""
    seed = _unit("pkg.m.seedfn", unit_id="u-seed")
    callee = _unit("pkg.m.callee", unit_id="u-callee",
                   content="def callee():\n    pass\n")
    caller = _unit("pkg.m.caller", unit_id="u-caller",
                   content="def caller():\n    seedfn()\n")
    state = _state(
        routes={"qname": _qname_route(seed)},
        units=[seed, callee, caller],
        neighbors=[
            _gnode("u-callee", "pkg.m.callee"),
            _gnode("u-caller", "pkg.m.caller"),
        ],
        edges=[
            ("u-seed", "CALLS", "u-callee"),
            ("u-caller", "CALLS", "u-seed"),
        ],
    )
    # direction=callees → only u-callee is kept; u-caller is filtered out.
    # The u-caller→u-seed edge must NOT appear because u-caller is cut.
    resp = await _run(state, "explore",
                      {"qualified_name": "pkg.m.seedfn", "repo_id": "acme",
                       "direction": "callees"})
    assert resp.status.value == "success"
    edge_pairs = {(e["src_id"], e["dst_id"]) for e in resp.data["edges"]}
    assert ("u-caller", "u-seed") not in edge_pairs
    assert ("u-seed", "u-callee") in edge_pairs
    assert resp.data.get("truncated_edges") is False


@pytest.mark.asyncio
async def test_explore_truncated_edges_flag_fires() -> None:
    """When more than 200 edges survive the endpoint filter, truncated_edges=True."""
    seed = _unit("pkg.m.hub", unit_id="u-seed")
    # 201 callee nodes + the seed.
    callee_units = [
        _unit(f"pkg.m.callee{i:03d}", unit_id=f"u-c{i:03d}") for i in range(201)
    ]
    callee_gnodes = [_gnode(u.unit_id, u.qualified_name) for u in callee_units]
    callee_edges = [("u-seed", "CALLS", u.unit_id) for u in callee_units]

    state = _state(
        routes={"qname": _qname_route(seed)},
        units=[seed, *callee_units],
        neighbors=callee_gnodes,
        edges=callee_edges,
    )
    resp = await _run(state, "explore",
                      {"qualified_name": "pkg.m.hub", "repo_id": "acme",
                       "direction": "callees"})
    assert resp.status.value == "success"
    # Neighbors capped at 50; edges filtered to kept endpoints, capped at 200.
    assert len(resp.data["neighbors"]) <= 50
    assert len(resp.data["edges"]) <= 200
    assert resp.data["truncated_edges"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_mcp_tools.py::test_explore_edges_exclude_filtered_out_nodes tests/test_mcp_tools.py::test_explore_truncated_edges_flag_fires -v
```

Expected: FAIL — `truncated_edges` key missing and filtered-out edges still appear.

- [ ] **Step 3: Fix `_explore_impl` edge assembly**

In `core/mcp/tools/explore_tool.py`, replace the `out` dict assembly block (starting with `out: dict[str, Any] = {` around line 197) with:

```python
    # Build the set of node IDs that are actually in the returned neighbor
    # list (post-cap, post-direction-filter) plus the seed itself.
    kept_ids: set[str] = {seed.unit_id} | {n["node_id"] for n in neighbors}

    # Keep only edges whose BOTH endpoints are in kept_ids, then cap.
    _MAX_EDGES = 200
    filtered_edges = [
        (s, k, d) for s, k, d in sorted(edges)
        if s in kept_ids and d in kept_ids
    ]
    truncated_edges = len(filtered_edges) > _MAX_EDGES
    filtered_edges = filtered_edges[:_MAX_EDGES]

    out: dict[str, Any] = {
        "found": True,
        "seed": {
            "qualified_name": seed.qualified_name,
            "kind": seed.kind.value,
            "file_path": seed.file_path,
            "lines": line_range(seed),
            "signature": seed.signature,
        },
        "direction": direction,
        "depth": depth,
        "neighbors": neighbors,
        "edges": [
            {"src_id": s, "kind": k, "dst_id": d} for s, k, d in filtered_edges
        ],
        "truncated": truncated,
        "truncated_edges": truncated_edges,
    }
```

Note: the `kept_ids` block goes right after the `neighbors.sort(...)` call and replaces the old `out` dict. The old `"edges": [{"src_id": s, "kind": k, "dst_id": d} for s, k, d in sorted(edges)]` line is replaced by the new filtered version.

- [ ] **Step 4: Verify the tests pass**

```bash
.venv/bin/pytest tests/test_mcp_tools.py::test_explore_edges_exclude_filtered_out_nodes tests/test_mcp_tools.py::test_explore_truncated_edges_flag_fires -v
```

Expected: PASS.

- [ ] **Step 5: Run the full suite**

```bash
.venv/bin/pytest tests/test_mcp_tools.py tests/test_phase9_sdk_cli.py -q
```

Expected: all tests pass. Note: `test_explore_all_returns_both_with_edges` already checks `edges` content and should still pass because both endpoints of the seed→callee edge are in `kept_ids`.

- [ ] **Step 6: Commit**

```bash
git add core/mcp/tools/explore_tool.py tests/test_mcp_tools.py
git commit -m "fix(mcp): cap and filter explore edges to returned endpoint set"
```

---

## Task 3: Add depth-clamp warning to deprecated aliases (SHOULD-FIX)

**Context:** Both `QueryGraphTool.execute` and `GetRelatedComponentsTool.execute` call `_explore_impl` with `depth=min(request.depth, 5)`. When `request.depth > 5` the depth is silently clamped. The caller (e.g. an old SDK consumer) has no way to know their requested depth was ignored. Fix: add `result["warning"] = "depth clamped to 5 (v1 compat); use explore for deeper traversal"` to the returned dict when clamping fired.

**Files:**
- Modify: `core/mcp/tools/graph_tool.py:123-136` (`GetRelatedComponentsTool.execute`) and `graph_tool.py:150-163` (`QueryGraphTool.execute`)
- Test: `tests/test_mcp_tools.py` (add after the existing deprecated-alias tests)

- [ ] **Step 1: Write the failing tests**

Add these tests in `tests/test_mcp_tools.py` after `test_get_related_components_delegates_to_explore`:

```python
@pytest.mark.asyncio
async def test_query_graph_warns_when_depth_clamped() -> None:
    """Requesting depth > 5 via the deprecated alias emits a warning."""
    resp = await _run(_explore_state(), "query_graph",
                      {"node": "pkg.m.seedfn", "repo_id": "acme", "depth": 8})
    assert resp.status.value == "success"
    assert "warning" in resp.data
    assert "clamped" in resp.data["warning"]
    assert "explore" in resp.data["warning"]


@pytest.mark.asyncio
async def test_query_graph_no_warning_when_depth_within_limit() -> None:
    """depth <= 5 → no warning key added."""
    resp = await _run(_explore_state(), "query_graph",
                      {"node": "pkg.m.seedfn", "repo_id": "acme", "depth": 5})
    assert resp.status.value == "success"
    assert "warning" not in resp.data


@pytest.mark.asyncio
async def test_get_related_components_warns_when_depth_clamped() -> None:
    resp = await _run(_explore_state(), "get_related_components",
                      {"component": "pkg.m.seedfn", "repo_id": "acme", "depth": 7})
    assert resp.status.value == "success"
    assert "warning" in resp.data
    assert "clamped" in resp.data["warning"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_mcp_tools.py::test_query_graph_warns_when_depth_clamped tests/test_mcp_tools.py::test_query_graph_no_warning_when_depth_within_limit tests/test_mcp_tools.py::test_get_related_components_warns_when_depth_clamped -v
```

Expected: FAIL on the two "warns" tests (key missing), PASS on "no_warning".

- [ ] **Step 3: Fix `GetRelatedComponentsTool.execute`**

In `core/mcp/tools/graph_tool.py`, replace `GetRelatedComponentsTool.execute` body:

```python
    async def execute(
        self, request: GetRelatedComponentsRequest, ctx: ExecutionContext
    ) -> dict[str, Any]:
        clamped_depth = min(request.depth, 5)
        result = await _explore_impl(
            ctx.state,
            reference=request.component,
            repo_id=request.repo_id,
            direction="all",
            depth=clamped_depth,
            request_id=ctx.request_id,
        )
        result["deprecated"] = "use explore"
        result["related"] = _legacy_candidates(result)  # v1 key
        if clamped_depth < request.depth:
            result["warning"] = (
                "depth clamped to 5 (v1 compat); use explore for deeper traversal"
            )
        return result
```

- [ ] **Step 4: Fix `QueryGraphTool.execute`**

In `core/mcp/tools/graph_tool.py`, replace `QueryGraphTool.execute` body:

```python
    async def execute(
        self, request: QueryGraphRequest, ctx: ExecutionContext
    ) -> dict[str, Any]:
        clamped_depth = min(request.depth, 5)
        result = await _explore_impl(
            ctx.state,
            reference=request.node,
            repo_id=request.repo_id,
            direction="all",
            depth=clamped_depth,
            request_id=ctx.request_id,
        )
        result["deprecated"] = "use explore"
        result["candidates"] = _legacy_candidates(result)  # v1 key
        if clamped_depth < request.depth:
            result["warning"] = (
                "depth clamped to 5 (v1 compat); use explore for deeper traversal"
            )
        return result
```

- [ ] **Step 5: Verify the tests pass**

```bash
.venv/bin/pytest tests/test_mcp_tools.py::test_query_graph_warns_when_depth_clamped tests/test_mcp_tools.py::test_query_graph_no_warning_when_depth_within_limit tests/test_mcp_tools.py::test_get_related_components_warns_when_depth_clamped -v
```

Expected: all three PASS.

- [ ] **Step 6: Run the full suite**

```bash
.venv/bin/pytest tests/test_mcp_tools.py tests/test_phase9_sdk_cli.py -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add core/mcp/tools/graph_tool.py tests/test_mcp_tools.py
git commit -m "fix(mcp): add warning when v1 alias clamps depth to 5"
```

---

## Task 4: Update `query_graph` fake in SDK test to real v2-alias payload (CHEAP)

**Context:** The fake `/mcp/tools/query_graph` handler in `tests/test_phase9_sdk_cli.py` returns an OLD payload shape: `{"node": ..., "found": True, "depth": ..., "candidates": []}`. The real `QueryGraphTool.execute` returns the full v2-alias shape, which includes `neighbors`, `edges`, `seed`, `direction`, `truncated`, `truncated_edges`, `deprecated`, AND `candidates` (the v1 compat key populated by `_legacy_candidates`). The test is not wrong, but it would miss alias regressions where `candidates` is dropped. Update the fake to match the real shape.

**Files:**
- Modify: `tests/test_phase9_sdk_cli.py:62-72` (the `mcp` route handler inside `_build_fake_api`)

- [ ] **Step 1: Check what the SDK's `query_graph` method actually reads from `data`**

The SDK's `AsyncMemoryClient.query_graph` calls `run_mcp_tool`, gets back a `McpToolResult`, then does:

```python
return QueryGraphResult.model_validate({
    "node": node, **result.data,
})
```

And `QueryGraphResult` has fields: `node`, `found`, `depth`, `candidates`, `edges`. The test `test_sdk_query_graph_unwraps_mcp_tool_response` checks `res.found` and `res.depth`. A regression where `candidates` is accidentally removed from the real tool's response would not be caught because the fake always returns `"candidates": []`.

- [ ] **Step 2: Update the fake to include the real v2-alias shape**

In `tests/test_phase9_sdk_cli.py`, replace the `if tool == "query_graph":` block (lines 63-71):

```python
        if tool == "query_graph":
            node = body["node"]
            depth = body.get("depth", 1)
            return {
                "tool": "query_graph",
                "request_id": "rid",
                "status": "success",
                "data": {
                    "node": node,
                    "found": True,
                    "depth": depth,
                    # v2-alias shape: neighbors + directed edges + seed.
                    "neighbors": [
                        {
                            "node_id": "u-nbr",
                            "qualified_name": "pkg.m.helper",
                            "kind": "fn",
                            "file_path": "pkg/m.py",
                            "lines": "1-5",
                            "signature": "def helper()",
                            "snippet": "def helper():",
                            "distance": 1,
                            "relation": "CALLS ->",
                        }
                    ],
                    "edges": [
                        {"src_id": node, "kind": "CALLS", "dst_id": "u-nbr"}
                    ],
                    "seed": {
                        "qualified_name": node,
                        "kind": "fn",
                        "file_path": "pkg/m.py",
                        "lines": "1-5",
                        "signature": "def f()",
                    },
                    "direction": "all",
                    "truncated": False,
                    "truncated_edges": False,
                    "deprecated": "use explore",
                    # v1 compat key — what the SDK reads via candidates[].unit_id
                    "candidates": [
                        {
                            "unit_id": "u-nbr",
                            "qualified_name": "pkg.m.helper",
                            "kind": "fn",
                            "file_path": "pkg/m.py",
                        }
                    ],
                },
                "latency_ms": 0.0,
                "schema_version": "1",
            }
```

- [ ] **Step 3: Update the existing assertions in `test_sdk_query_graph_unwraps_mcp_tool_response`**

The test currently only checks `res.found` and `res.depth`. With the new fake returning a real neighbor, also assert the `candidates` compat key is preserved:

```python
@pytest.mark.asyncio
async def test_sdk_query_graph_unwraps_mcp_tool_response(
    asgi_transport: httpx.ASGITransport,
) -> None:
    async with AsyncMemoryClient(base_url="http://t", transport=asgi_transport) as c:
        res = await c.query_graph(node="pkg.m.f", repo_id="acme", depth=2)
    assert res.found
    assert res.depth == 2
    # v1 compat: SDK can read candidates[].unit_id.
    assert len(res.candidates) == 1
    assert res.candidates[0]["unit_id"] == "u-nbr"
```

- [ ] **Step 4: Run the updated SDK test to verify it passes**

```bash
.venv/bin/pytest tests/test_phase9_sdk_cli.py::test_sdk_query_graph_unwraps_mcp_tool_response tests/test_phase9_sdk_cli.py::test_sdk_run_mcp_tool_returns_full_envelope -v
```

Expected: both PASS (the second test reads `res.data["found"]` which is still True).

- [ ] **Step 5: Run the full test suite**

```bash
.venv/bin/pytest tests/test_mcp_tools.py tests/test_phase9_sdk_cli.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add tests/test_phase9_sdk_cli.py
git commit -m "test(sdk): update query_graph fake to real v2-alias payload shape"
```

---

## Final Gate

- [ ] **Run full test suite + ruff**

```bash
cd /Users/karinganageshgoud/Desktop/Agent-Memory/Memory-CL
.venv/bin/pytest tests/ -q
.venv/bin/ruff check core/mcp/tools/discovery_tool.py core/mcp/tools/explore_tool.py core/mcp/tools/graph_tool.py tests/test_mcp_tools.py tests/test_phase9_sdk_cli.py
```

Expected: all tests pass, ruff clean.

- [ ] **Squash commits (optional) or create the final commit**

If the four task commits look good, create the gate commit:

```bash
git add -A
git commit -m "fix(mcp): honest find_symbol truncation, capped explore edges, clamp warning"
```

Report: gates (pytest count, ruff exit), commit SHA.
